import os
from archiver.archival_file import ArchivalFile
from archiver.server_change import ServerChange
from flask import render_template, url_for, flash, redirect, request
from flask_login import login_user, logout_user, login_required, current_user
from archiver.forms import *
from archiver.models import *
from archiver import app, db, bcrypt


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


@app.route("/")
@app.route("/home")
def home():
    return render_template('home.html', posts=posts)


@app.route("/register", methods=['GET', 'POST'])
def register():
    #if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return redirect(url_for('home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user = UserModel(email=form.email.data, first_name=form.first_name.data, last_name=form.last_name.data,
                         password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flash(f'Account created for {form.email.data}!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)


@app.route("/login", methods=['GET', 'POST'])
def login():
    #if the current user has already been authenticated, just send them to the home page.
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


def save_uploaded_file(uploaded_file):
    #TODO placeholder
    f_name, f_ext = os.path.splitext(uploaded_file.filename)
    path = os.path.join(app.root_path)
    return path

@app.route("/upload_file", methods=['GET', 'POST'])
@login_required
def upload_file():
    form = UploadFileForm()
    if form.validate_on_submit():
        arch_file = ArchivalFile(wtform_upload=form.upload, project=form.project_number.data,
                                 new_filename=form.new_filename.data, notes=form.notes.data,
                                 destination_dir=form.destination_directory.data)
        archiving_successful = arch_file.archive_in_destination()
        if archiving_successful:
            archived_file = ArchivedFileModel(destination_path=arch_file.destination_path,
                                              project_number=arch_file.project_number, document_date=form.document_date.data,
                                              destination_directory=arch_file.destination_dir, file_code=arch_file.file_code,
                                              notes=arch_file.notes, filename=arch_file.assemble_destination_filename())
            #TODO how to add the filesize and archivist id to this. Also need to add extension
            #TODO should I remove the wtform_upload attribute from archivalFile
            db.session.add(archived_file)
            db.session.commit()
            flash(f'File received!', 'success')
            return redirect(url_for('upload_file'))
    return render_template('upload_file.html', title='Upload File to Archive', form=form)

@app.route("/server_change", methods=['GET', 'POST'])
@login_required
def server_change():
    def save_server_change(change: ServerChange):
        """
        Subroutine for saving server changes to database
        :param change: ServerChange object
        :return: None
        """
        change_model = ServerChangeModel(**vars(change))
        db.session.add(change_model)
        db.session.commit()

    form = ServerChange()
    if form.validate_on_submit():
        # TODO how to get current user email or id or whatever
        user = current_user

        # if the user entered a path to delete
        if form.path_delete.data:
            deletion = ServerChange(change_type='DELETE', new_path=form.path_delete.data, user=user)
            deletion.execute()
            save_server_change(deletion)

        # if the user entered a path to change and the desired path change
        if form.current_path.data and form.new_path.data:
            renaming = ServerChange(change_type='RENAME', old_path=form.current_path.data, new_path=form.new_path.data,
                                  user=user)
            renaming.execute()
            save_server_change(renaming)

        # if the user entered a path to an asset to move and a location to move it to
        if form.asset_path.data and form.destination_path.data:
            move = ServerChange(change_type='MOVE', old_path=form.asset_path, new_path=form.destination_path.data,
                                user=user)
            move.execute()
            save_server_change(move)

        # if user entered a path for a new directory to be made
        if form.new_directory.data:
            creation = ServerChange(change_type='CREATE', new_path=form.new_directory.data, user=user)
            creation.execute()
            save_server_change(creation)
    return render_template('server_change.html', title='Make change to file server', form=form)

@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route("/account")
@login_required
def account():

    return render_template('account.html', title='Account')