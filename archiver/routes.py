import os
import archiver.ArchiverUtilities as ArchiverUtilities
from archiver.archival_file import ArchivalFile
from archiver.server_edit import ServerEdit
from flask import render_template, url_for, flash, redirect, request
from flask_login import login_user, logout_user, login_required, current_user
from archiver.forms import *
from archiver.models import *
from archiver import app, db, bcrypt
from dateutil import parser
from functools import wraps

posts = [
    {
        'author': 'Corey Schafer',
        'title': 'Blog Post 1',
        'content': 'First post content',
        'date_posted': 'April 20, 2018'
    },
    {
        'author': 'Jane Doe',
        'title': 'Blog Post 2',
        'content': 'Second post content',
        'date_posted': 'April 21, 2018'
    }
]


def roles_required(roles: list[str]):
    """
    :param roles: list of the roles that can access the endpoint
    :return: actual decorator function
    """
    def decorator(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            user_role_list = current_user.roles.split(",")
            # if the user has at least a single role and at least one of the user roles is in roles...
            if current_user.roles and [role for role in roles if role in user_role_list]:
                return func(*args, **kwargs)
            else:
                flash("Need a different role to access this.", 'danger')
                return redirect(url_for('home'))
        return wrap
    return decorator


@app.route("/")
@app.route("/home")
def home():
    return render_template('home.html', posts=posts)


@app.route("/register", methods=['GET', 'POST'])
def register():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user_roles = ",".join(form.roles.data)
        user = UserModel(email=form.email.data, first_name=form.first_name.data, last_name=form.last_name.data,
                         roles=user_roles, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash(f'Account created for {form.email.data}!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)


@app.route("/login", methods=['GET', 'POST'])
def login():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = UserModel.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = request.args.get('next')
            # after successful login it will attempt to send user to the previous page they were trying to access.
            # If that is not available, it will redirect to the home page
            return redirect(next_page) if next_page else redirect(url_for('home'))
        else:
            flash(f'Login Unsuccessful! Check credentials.', 'danger')
    return render_template('login.html', title='Login', form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash(f'You have logged out. Good-bye.', 'success')
    return redirect(url_for('home'))


@app.route("/send_archives_file")
@login_required
def send_archives_file():
    k = "this is a test"
    print(k)
    return k

@app.route("/server_change", methods=['GET', 'POST'])
@login_required
def server_change():
    def save_server_change(executed_edit: ServerEdit):
        """
        Subroutine for saving server changes to database
        :param change: ServerEdit object
        :return: None
        """
        editor = UserModel.query.filter_by(email=executed_edit.user).first()
        change_model = ServerChangeModel(old_path=executed_edit.old_path, new_path=executed_edit.new_path,
                                         change_type=executed_edit.change_type, user_id=editor.id)
        db.session.add(change_model)
        db.session.commit()

    form = ServerChangeForm()
    if form.validate_on_submit():
        # TODO how to get current user email or id or whatever
        user_email = current_user.email

        # if the user entered a path to delete
        if form.path_delete.data:
            deletion = ServerEdit(change_type='DELETE', user=user_email, old_path=form.path_delete.data)
            deletion.execute()
            save_server_change(deletion)

        # if the user entered a path to change and the desired path change
        if form.current_path.data and form.new_path.data:
            renaming = ServerEdit(change_type='RENAME', user=user_email, old_path=form.current_path.data,
                                  new_path=form.new_path.data)
            renaming.execute()
            save_server_change(renaming)

        # if the user entered a path to an asset to move and a location to move it to
        if form.asset_path.data and form.destination_path.data:
            move = ServerEdit(change_type='MOVE', user=user_email, old_path=form.asset_path.data,
                              new_path=form.destination_path.data)
            move.execute()
            save_server_change(move)

        # if user entered a path for a new directory to be made
        if form.new_directory.data:
            creation = ServerEdit(change_type='CREATE', user=user_email, new_path=form.new_directory.data)
            creation.execute()
            save_server_change(creation)

        flash(f'Server oprerating system call executed to make requested change.', 'success')
        return redirect(url_for('server_change'))
    return render_template('server_change.html', title='Make change to file server', form=form)


@app.route("/upload_file", methods=['GET', 'POST'])
@login_required
def upload_file():
    form = UploadFileForm()
    temp_files_directory = os.path.join(os.getcwd(), r"archiver\static\temp_files")
    if form.validate_on_submit():
        temp_path = os.path.join(temp_files_directory, form.upload.data.filename)
        form.upload.data.save(temp_path)
        upload_size = os.path.getsize(temp_path)
        arch_file = ArchivalFile(current_path=temp_path, project=form.project_number.data,
                                 new_filename=ArchiverUtilities.cleanse_filename(form.new_filename.data),
                                 notes=form.notes.data, destination_dir=form.destination_directory.data)
        archiving_successful = arch_file.archive_in_destination()

        # if the file was successfully moved to its destination, we will save the data to the database
        if archiving_successful:
            archived_file = ArchivedFileModel(destination_path=arch_file.get_destination_path(),
                                              project_number=arch_file.project_number,
                                              document_date=form.document_date.data,
                                              destination_directory=arch_file.destination_dir,
                                              file_code=arch_file.file_code, archivist_id=current_user.id,
                                              file_size=upload_size, notes=arch_file.notes,
                                              filename=arch_file.assemble_destination_filename())
            db.session.add(archived_file)
            db.session.commit()
            flash(f'File archived here: \n{arch_file.get_destination_path()}', 'success')
            return redirect(url_for('upload_file'))
    return render_template('upload_file.html', title='Upload File to Archive', form=form)

@app.route("/inbox_item")
@roles_required(['ADMIN', 'ARCHIVIST'])
def inbox_item():
    form = InboxTopForm()
    return render_template('inbox_item.html', title='Inbox', form=form)


@app.route("/account")
@login_required
def account():
    return render_template('account.html', title='Account')
