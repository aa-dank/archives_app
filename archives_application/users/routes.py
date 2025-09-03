# archives_application/users/routes.py

import flask
import json
import os
import requests
from flask_login import login_user, logout_user, login_required, current_user
from archives_application import db, bcrypt
from archives_application.models import *
from archives_application import utils
from archives_application.users.forms import *

users = flask.Blueprint('users', __name__)


def get_google_provider_urls():
    """
    This function serves to retrieve the authorization (and other) endpoints from google so they do not have to be
    hard-coded into the application.
    :return: Dictionary of URLs
    """
    g_discovery_url = flask.current_app.config.get('GOOGLE_DISCOVERY_URL')
    response = requests.get(g_discovery_url)
    if not response.status_code == 200:
        raise Exception(
            f"Did not get valid status code from request to {g_discovery_url}\n Got code {response.status_code} instead.")
    return response.json()


def user_login_flow(user):
    login_user(user)
    flask.session[user.email] = {}
    flask.session[user.email]['temporary files'] = []


@users.route("/choose_login")
def choose_login():
    """
    Returns html page where people can choose to login using the google authentication flow or a archives app account
    :return:
    """
    if current_user.is_authenticated:
        flask.flash(f'Already logged in.', 'message')
        return flask.redirect(flask.url_for('main.home'))

    return flask.render_template('choose_login.html', title='Register')


@users.route("/google_auth")
def google_auth():
    """
    Google authentication was created using following tutorial (which needed to be heavily modified to work within this
    application):
    https://realpython.com/flask-google-login/

    :return:
    """
    try:
        authorization_endpoint = get_google_provider_urls()["authorization_endpoint"]
        # Use library to construct the request for Google login and provide
        # scopes that let you retrieve user's profile from Google
        client = flask.current_app.config['google_auth_client']
        request_uri = client.prepare_request_uri(
            authorization_endpoint,
            redirect_uri=flask.request.base_url + "/callback",
            scope=["openid", "email", "profile"],
        )
        return flask.redirect(request_uri)

    except Exception as e:
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message="Error during authentication with Google: ",
            thrown_exception=e,
            app_obj=flask.current_app
        )


@users.route("/google_auth/callback")
def callback():
    """
    When Google sends back the unique login code, it’ll be sending it to this login callback endpoint on your application
    :return:
    """

    client = flask.current_app.config['google_auth_client']
    # Get authorization code Google sent back to you
    code = utils.FlaskAppUtils.retrieve_request_param("code")

    #Get token url endpoint
    token_endpoint = get_google_provider_urls()["token_endpoint"]

    # Prepare and send a request to get tokens! Yay tokens!
    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=flask.request.url,
        redirect_url=flask.request.base_url,
        code=code
    )
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(flask.current_app.config['GOOGLE_CLIENT_ID'], flask.current_app.config['GOOGLE_CLIENT_SECRET'])
    )

    try:
        # Parse the tokens!
        client.parse_request_body_response(json.dumps(token_response.json()))

        # Now that you have tokens (yay) let's find and hit the URL
        # from Google that gives you the user's profile information,
        # including their Google profile image and email
        userinfo_endpoint = get_google_provider_urls()["userinfo_endpoint"]
        uri, headers, body = client.add_token(userinfo_endpoint)
        userinfo_response = requests.get(uri, headers=headers, data=body)

        # You want to make sure their email is verified.
        # The user authenticated with Google, authorized your
        # app, and now you've verified their email through Google!
        users_email, first_name, last_name = "","",""
        if userinfo_response.json().get("email_verified"):
            users_email = userinfo_response.json()["email"]
        else:
            flask.flash(f'User email not available or not verified by Google.', 'warning')
            return flask.redirect(flask.url_for('main.home'))

        # Check if email is included in unsanctioned accounts...
        #TODO add unsanctioned accounts to config?
        unsanctioned_accounts = ["test@ucsc.edu", "archives@ucsc.edu", "constdoc@ucsc.edu"]
        if users_email.lower() in unsanctioned_accounts:
            message = f'Account {users_email} is unsanctioned for Google Authentication.  Sign in with application password or contact application admin.'
            flask.flash(message, 'danger')
            return flask.redirect(flask.url_for('main.home'))

        # Determine if the user is in the database. If not we add them to database
        user = UserModel.query.filter_by(email=users_email).first()
        if not user:
            flask.session['new user'] = {"email": users_email}
            return flask.redirect(flask.url_for('users.google_register'))

        # if there is already an account but it is not an active account...
        if user and not user.active:
            flask.flash(f'Account is inactive. Contact application admin.', 'danger')
            return flask.redirect(flask.url_for('main.home'))

        user_login_flow(user)
        flask.flash("Login Successful.", 'success')
        return flask.redirect(flask.url_for('main.home'))

    except Exception as e:
        return utils.FlaskAppUtils.web_exception_subroutine(
            flash_message='Error during Google Authentication Callback: ',
            thrown_exception=e,
            app_obj=flask.current_app
        )

@users.route("/google_auth/register", methods=['GET', 'POST'])
def google_register():
    # if this endpoint was called without first doing the google Oauth dance
    if not flask.session.get('new user'):
        flask.flash(f'Authenticate via Google Oauth before registering Google account.', 'warning')
        return flask.redirect(flask.url_for('main.home'))
    new_user_email = flask.session['new user']['email']
    form = GoogleRegisterForm()

    # cannot use flask.current_form in the form definition. Hence...
    form.roles.choices = flask.current_app.config.get('ROLES')

    if form.validate_on_submit():
        try:
            user_roles = ",".join(form.roles.data)
            user = UserModel(email=new_user_email, first_name=form.first_name.data, active=True,
                             last_name=form.last_name.data, roles=user_roles)
            db.session.add(user)
            db.session.commit()
            user = UserModel.query.filter_by(email=new_user_email).first()
            user_login_flow(user=user)
            flask.flash(f'Account created for {new_user_email}!', 'success')
            return flask.redirect(flask.url_for('main.home'))
        except Exception as e:
            return utils.FlaskAppUtils.web_exception_subroutine(
                flash_message="Error while initiating google registration workflow: ",
                thrown_exception=e,
                app_obj=flask.current_app
            )

    return flask.render_template('google_register.html', title='Register', form=form)


@users.route("/register", methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        flask.flash(f'Already logged in.', 'message')
        return flask.redirect(flask.url_for('main.home'))

    return flask.render_template('choose_registration.html', title='Register')


@users.route("/new_account_registeration", methods=['GET', 'POST'])
def new_account_registeration():
    """
    This endpoint is for creating and processing a form for creating a new account that does not use google authentication
    :return:
    """
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('main.home'))

    form = RegistrationForm()

    # cannot use flask.current_form in the form definition. Hence...
    form.roles.choices = flask.current_app.config.get('ROLES')

    if form.validate_on_submit():
        try:
            hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
            db_user = UserModel.query.filter_by(email=form.email.data).first()
            if db_user:
                flask.flash(f'''An account already exists for the email "{form.email.data}"''', 'warning')
                return flask.redirect(flask.url_for('main.home'))
            user_roles = ",".join(form.roles.data)
            user = UserModel(email=form.email.data, first_name=form.first_name.data, last_name=form.last_name.data,
                             roles=user_roles, active=True, password=hashed_password)
            db.session.add(user)
            db.session.commit()
            flask.flash(f'Account created for {form.email.data}!', 'success')
            return flask.redirect(flask.url_for('users.login'))

        except Exception as e:
            return utils.FlaskAppUtils.web_exception_subroutine(
                flash_message="Error occured while creating a new account:",
                thrown_exception=e,
                app_obj=flask.current_app
            )

    return flask.render_template('register.html', title='Register', form=form)


@users.route("/login", methods=['GET', 'POST'])
def login():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('main.home'))

    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = UserModel.query.filter_by(email=form.email.data).first()

            # if the user account already exists but is not active...
            if user and not user.active:
                flask.flash(f'Account is inactive. Contact application admin.', 'danger')
                return flask.redirect(flask.url_for('main.home'))

            if user: 
                if not user.password:
                    flask.flash(f'No password set for account. Contact application admin.', 'danger')
                    return flask.redirect(flask.url_for('main.home'))
                
                if bcrypt.check_password_hash(user.password, form.password.data):
                    user_login_flow(user=user)
                    next_page = utils.FlaskAppUtils.retrieve_request_param('next')

                    # after successful login it will attempt to send user to the previous page they were trying to access.
                    # If that is not available, it will flask.redirect to the home page
                    return flask.redirect(next_page) if next_page else flask.redirect(flask.url_for('main.home'))
            else:
                flask.flash(f'Login Unsuccessful! Check credentials.', 'danger')
                return flask.redirect(flask.url_for('main.home'))

        except Exception as e:

            return utils.FlaskAppUtils.web_exception_subroutine(
                flash_message="Error while processing user login: ",
                thrown_exception=e,
                app_obj=flask.current_app
            )

    return flask.render_template('login.html', title='Login', form=form)


@users.route("/logout")
@login_required
def logout():
    # before logging out we will delete their temporary files and remove their dict from the session
    if flask.session.get(current_user.email):
        temp_files = flask.session.get(current_user.email).get('temporary files') #TODO DELETE old files
        for file_path in temp_files:
            try:
                os.remove(file_path)
            except:
                pass
        flask.session.pop(current_user.email)
    logout_user()
    flask.flash(f'You have logged out. Good-bye.', 'success')
    return flask.redirect(flask.url_for('main.home'))


@users.route("/account")
@login_required
def account():
    return flask.render_template('account.html', title='Account')