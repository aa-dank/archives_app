import os
from archival_file import ArchivalFile
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
        user = User(email=form.email.data, password=hashed_password)
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
        user = User.query.filter_by(email=form.email.data).first()
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
        arch_file = ArchivalFile(form_upload=form.upload, project=form.project_number.data,
                                 new_filename=form.new_filename.data, notes=form.notes.data,
                                 destination_dir=form.destination_directory.data)
        archiving_successful = arch_file.archive_in_destination()
        if archiving_successful:
            archived_file = ArchivedFile(destination_path=arch_file.destination_path,
                                         project_number=arch_file.project_number, document_date=form.document_date.data,
                                         destination_directory=arch_file.destination_dir, file_code=arch_file.file_code,
                                         notes=arch_file.notes, filename=arch_file.assemble_destination_filename())
            #TODO how to add the filesize and archivist id to this. Also need to add extension
            #TODO should I remove the form_upload attribute from archivalFile
            db.session.add(archived_file)
            db.session.commit()
            flash(f'File received!', 'success')
            return redirect(url_for('upload_file'))
    return render_template('upload_file.html', title='Upload File to Archive', form=form)

@app.route("/change", methods=['GET', 'POST'])
@login_required
def server_change():
    form = ServerChange()
    if form.validate_on_submit():




@app.route("/logout")
def logout():
    logout_user()
    return redirect(url_for('home'))

@app.route("/account")
@login_required
def account():

    return render_template('account.html', title='Account')