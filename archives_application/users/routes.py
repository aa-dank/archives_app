import flask
import os
from flask_login import login_user, logout_user, login_required, current_user
from flask import Blueprint
from flask_dance.contrib.google import google
from archives_application import db, bcrypt
from archives_application.models import *
from .forms import *


users = Blueprint('users', __name__)

@users.route("/register", methods=['GET', 'POST'])
def register():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('main.home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user_roles = ",".join(form.roles.data)
        user = UserModel(email=form.email.data, first_name=form.first_name.data, last_name=form.last_name.data,
                         roles=user_roles, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flask.flash(f'Account created for {form.email.data}!', 'success')
        return flask.redirect(flask.url_for('users.login'))
    return flask.render_template('register.html', title='Register', form=form)


@users.route("/login", methods=['GET', 'POST'])
def login():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('main.home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = UserModel.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            flask.session[current_user.email] = {}
            flask.session[current_user.email]['temporary files'] = []
            next_page = flask.request.args.get('next')
            # after successful login it will attempt to send user to the previous page they were trying to access.
            # If that is not available, it will flask.redirect to the home page
            return flask.redirect(next_page) if next_page else flask.redirect(flask.url_for('main.home'))
        else:
            flask.flash(f'Login Unsuccessful! Check credentials.', 'danger')
    return flask.render_template('login.html', title='Login', form=form)


@users.route("/logout")
@login_required
def logout():
    # before logging out we will delete their temporary files and remove their dict from the session
    if flask.session.get(current_user.email):
        temp_files = flask.session.get(current_user.email).get('temporary files')
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