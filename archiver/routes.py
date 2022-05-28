import fitz
import os
import flask
import shutil
import archiver.ArchiverUtilities as ArchiverUtilities
from archiver.archival_file import ArchivalFile
from archiver.server_edit import ServerEdit
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


def get_user_handle():
    '''
    user's email handle without the rest of the address (eg dilbert.dogbert@ucsc.edu would return dilbert.dogbert)
    :return: string handle
    '''
    return current_user.email.split("@")[0]


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
                flask.flash("Need a different role to access this.", 'danger')
                return flask.redirect(flask.url_for('home'))

        return wrap

    return decorator


@app.route("/")
@app.route("/home")
def home():
    return flask.render_template('home.html', posts=posts)


@app.route("/register", methods=['GET', 'POST'])
def register():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('home'))

    form = RegistrationForm()
    if form.validate_on_submit():
        hashed_password = bcrypt.generate_password_hash(form.password.data).decode('utf-8')
        user_roles = ",".join(form.roles.data)
        user = UserModel(email=form.email.data, first_name=form.first_name.data, last_name=form.last_name.data,
                         roles=user_roles, password=hashed_password)
        db.session.add(user)
        db.session.commit()
        flask.flash(f'Account crflask.eated for {form.email.data}!', 'success')
        return flask.redirect(flask.url_for('login'))
    return flask.render_template('register.html', title='Register', form=form)


@app.route("/login", methods=['GET', 'POST'])
def login():
    # if the current user has already been authenticated, just send them to the home page.
    if current_user.is_authenticated:
        return flask.redirect(flask.url_for('home'))

    form = LoginForm()
    if form.validate_on_submit():
        user = UserModel.query.filter_by(email=form.email.data).first()
        if user and bcrypt.check_password_hash(user.password, form.password.data):
            login_user(user, remember=form.remember.data)
            next_page = flask.request.args.get('next')
            # after successful login it will attempt to send user to the previous page they were trying to access.
            # If that is not available, it will flask.redirect to the home page
            return flask.redirect(next_page) if next_page else flask.redirect(flask.url_for('home'))
        else:
            flask.flash(f'Login Unsuccessful! Check credentials.', 'danger')
    return flask.render_template('login.html', title='Login', form=form)


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flask.flash(f'You have logged out. Good-bye.', 'success')
    return flask.redirect(flask.url_for('home'))


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

        flask.flash(f'Server oprerating system call executed to make requested change.', 'success')
        return flask.redirect(flask.url_for('server_change'))
    return flask.render_template('server_change.html', title='Make change to file server', form=form)


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
            flask.flash(f'File archived here: \n{arch_file.get_destination_path()}', 'success')
            return flask.redirect(flask.url_for('upload_file'))
    return flask.render_template('upload_file.html', title='Upload File to Archive', form=form)


@app.route("/inbox_item", methods=['GET', 'POST'])
@roles_required(['ADMIN', 'ARCHIVIST'])
def inbox_item():
    def pdf_preview_image(pdf_path, image_destination):
        """

        :param pdf_path:
        :param image_destination:
        :return:
        """
        # Turn the pdf filename into equivalent png filename and create destination path
        pdf_filename = ArchiverUtilities.split_path(pdf_path)[-1]
        preview_filename = ".".join(pdf_filename.split(".")[:-1])
        preview_filename += ".png"
        output_path = os.path.join(image_destination, preview_filename)

        # create png preview and save it
        fitz_doc = fitz.open(pdf_path)
        first_page_pix_map = fitz_doc.load_page(0).get_pixmap()
        first_page_pix_map.save(output_path)
        return output_path

    user_inbox_path = os.path.join(config.INBOXES_LOCATION, get_user_handle())
    user_inbox_files = lambda: [thing for thing in os.listdir(user_inbox_path) if
                                os.path.isfile(os.path.join(user_inbox_path, thing))]
    if not os.path.exists(user_inbox_path):
        os.makedirs(user_inbox_path)

    # if no files in the user inbox, move a file from the INBOX directory to the user inbox to be processed.
    # This avoids other users from processing the same file, creating errors.
    if not user_inbox_files():
        general_inbox_files = [t for t in os.listdir(config.INBOXES_LOCATION) if os.path.isfile(os.path.join(config.INBOXES_LOCATION, t))]

        # if there are no files to archive in either the user inbox or the archivist inbox we will send the user to
        # the homepage.
        if not general_inbox_files:
            flask.flash("The archivist inboxes are empty. Add files to the inbox directories to archive them.", 'info')
            return flask.redirect(flask.url_for('home'))

        item_path = os.path.join(config.INBOXES_LOCATION, general_inbox_files[0])
        shutil.move(item_path, os.path.join(user_inbox_path, general_inbox_files[0]))

    temp_files_directory = os.path.join(os.getcwd(), r"archiver\static\temp_files")
    arch_file_filename = user_inbox_files()[0]
    arch_file_path = os.path.join(user_inbox_path, arch_file_filename)
    arch_file_preview = pdf_preview_image(arch_file_path, temp_files_directory) #TODO how to embed in the form html
    form = InboxItemForm()

    # if the flask.session has data previously entered in this form, then re-enter it into the form before rendering
    # it in html
    if flask.session.get('inbox_form_data'):
        sesh_data = flask.session.get('inbox_form_data')
        form.project_number.data = sesh_data.get('project_number')
        form.destination_path.data = sesh_data.get('destination_path')
        form.notes.data = sesh_data.get('notes')
        form.document_date.data = sesh_data.get('document_date')
        form.new_filename.data = sesh_data.get('new_filename')
        flask.session['inbox_form_data'] = None

    if form.validate_on_submit():

        # if the user clicked the download button, we send the file to the user, save what data the user has entered,
        # and rerender the page.
        if form.download_item.data:
            file_can_be_opened_in_browser = arch_file_filename.split(".")[-1].lower() in ['pdf', 'html']
            flask.session['inbox_form_data'] = form.data #TODO make this user specific data
            return flask.send_file(arch_file_path, as_attachment= not file_can_be_opened_in_browser)
            #return flask.redirect(flask.url_for('inbox_item'))

        upload_size = os.path.getsize(arch_file_path)
        arch_file = ArchivalFile(current_path=arch_file_path, project=form.project_number.data,
                                 new_filename=ArchiverUtilities.cleanse_filename(form.new_filename.data),
                                 notes=form.notes.data, destination_dir=form.destination_directory.data)
        archiving_successful = arch_file.archive_in_destination()
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
            flask.flash(f'File archived here: \n{arch_file.get_destination_path()}', 'success')
            return flask.redirect(flask.url_for('inbox_item'))
        else:
            pass #TODO
    return flask.render_template('inbox_item.html', title='Inbox', form=form, item_filename=arch_file_filename)


@app.route("/account")
@login_required
def account():
    return flask.render_template('account.html', title='Account')
