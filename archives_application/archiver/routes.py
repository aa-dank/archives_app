import datetime
import flask
import flask_sqlalchemy
import json
import os
import random
import shutil
import pandas as pd
from datetime import timedelta
from flask_login import login_required, current_user
from sqlalchemy import func
from typing import Union

# imports from this application
from archives_application.archiver.archival_file import ArchivalFile
from archives_application import utils
from archives_application.archiver.forms import *
from archives_application.models import *
from archives_application import db, bcrypt


archiver = flask.Blueprint('archiver', __name__)

EXCLUDED_FILENAMES = ['Thumbs.db', 'thumbs.db', 'desktop.ini']
EXCLUDED_FILE_EXTENSIONS = ['DS_Store', '.ini']


def web_exception_subroutine(flash_message, thrown_exception, app_obj):
    """
    Sub-process for handling patterns
    @param flash_message:
    @param thrown_exception:
    @param app_obj:
    @return:
    """
    flash_message = flash_message + f": {str(thrown_exception)}"
    flask.flash(flash_message, 'error')
    app_obj.logger.error(thrown_exception, exc_info=True)
    return flask.redirect(flask.url_for('main.home'))


def api_exception_subroutine(response_message, thrown_exception):
    """
    Subroutine for handling an exception and returning response code to api call.
    (In contrast to the web_exception_subroutine, which is for handling exceptions in the web app.)
    @param response_message: message sent with response code
    @param thrown_exception: exception that broke the 'try' conditional
    @return:
    """
    flask.current_app.logger.error(thrown_exception, exc_info=True)
    return flask.Response(response_message + "\n" + str(thrown_exception), status=500)


def remove_file_location(db: flask_sqlalchemy.SQLAlchemy, file_path: str):
    """
    Removes a file from the server and deletes the entry from the database
    :param db: SQLAlchemy object
    :param file_path: path to file on the server
    :return: None
    """
    if os.path.exists(file_path):
        os.remove(file_path)
    
    # extract the directories it is nested within and the filename; use these to query the database
    path_list = utils.split_path(file_path)
    file_server_root_index = os.path.join(*path_list[:-1]) 
    server_dirs_list = path_list[file_server_root_index:-1]
    server_dirs = os.path.join(*server_dirs_list)
    file_loc = db.session.query(FileLocationModel).filter(FileLocationModel.file_server_directories == server_dirs,
                                                          FileLocationModel.filename == path_list[-1]).first()
    
    if not file_loc:
        return True
    
    other_locations = len(db.session.query(FileLocationModel).filter(FileLocationModel.file_id == file_loc.file_id).all()) > 1
    file_deleted = db.session.delete(file_loc)
    if not other_locations:
        file_to_delete = db.session.query(FileModel).filter(FileModel.id == file_loc.file_id)
        db.session.query(ArchivedFileModel).filter(ArchivedFileModel.file_id == file_to_delete.id).update({"file_id": None})
        db.session.delete(file_to_delete)
    
    db.session.commit()
    return file_deleted


def get_user_handle():
    '''
    user's email handle without the rest of the address (eg dilbert.dogbert@ucsc.edu would return dilbert.dogbert)
    :return: string handle
    '''
    return current_user.email.split("@")[0]


def has_admin_role(usr: UserModel):
    """
    Checks if a user has admin role
    """
    return any([admin_str in usr.roles.split(",") for admin_str in ['admin', 'ADMIN']])


@archiver.route("/server_change", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
def server_change():
    
    # imported here to avoid circular import
    from archives_application.archiver.server_edit import ServerEdit
    
    def save_server_change(executed_edit: ServerEdit):
        """
        Subroutine for saving server changes to database
        :param change: ServerEdit object
        :return: None
        """
        editor = UserModel.query.filter_by(email=executed_edit.user).first()
        change_model = ServerChangeModel(old_path=executed_edit.old_path,
                                         new_path=executed_edit.new_path,
                                         change_type=executed_edit.change_type,
                                         files_effected=executed_edit.files_effected,
                                         data_effected=executed_edit.data_effected,
                                         user_id=editor.id
                                         )
        db.session.add(change_model)
        db.session.commit()

    form = ServerChangeForm()
    if form.validate_on_submit():
        try:
            user_email = current_user.email
            archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')

            # retrieve limits to how much can be changed on the server, but if the user has admin credentials,
            # there are no limits and they are set to zero
            files_limit = flask.current_app.config.get('SERVER_CHANGE_FILES_LIMIT')
            data_limit = flask.current_app.config.get('SERVER_CHANGE_DATA_LIMIT')
            
            # if the user has admin credentials, there are no limits
            if 'ADMIN' in current_user.roles:
                files_limit, data_limit = 0, 0

            new_path = None
            old_path = None
            edit_type = None

            # If the user entered a path to delete
            if form.path_delete.data:
                old_path = form.path_delete.data
                edit_type = 'DELETE'

            # If the user entered a path to change and the desired path change
            if form.current_path.data and form.new_path.data:
                old_path = form.current_path.data
                new_path = form.new_path.data
                edit_type = 'RENAME'

            # If the user entered a path to an asset to move and a location to move it to
            if form.asset_path.data and form.destination_path.data:
                old_path = form.asset_path.data
                new_path = form.destination_path.data
                edit_type = 'MOVE'

            # If user entered a path for a new directory to be made
            if form.new_directory.data:
                new_path = form.new_directory.data
                edit_type = 'CREATE'

            server_edit = ServerEdit(server_location=archives_location,
                                     change_type=edit_type,
                                     user=user_email,
                                     new_path=new_path,
                                     old_path=old_path)
            server_edit.execute(files_limit=files_limit, effected_data_limit=data_limit)
            save_server_change(server_edit)

            flask.flash(f"Requested '{edit_type}' change executed and recorded.", 'success')
            return flask.redirect(flask.url_for('archiver.server_change'))

        except Exception as e:
            return web_exception_subroutine(flash_message="Error processing or executing change: ",
                                            thrown_exception=e,
                                            app_obj=flask.current_app)
    return flask.render_template('server_change.html', title='Make change to file server', form=form)


@archiver.route("/upload_file", methods=['GET', 'POST'])
@login_required
def upload_file():
    """
    This function handles the upload of a single file to the server.
    """
    # import task function here to avoid circular import
    from archives_application.archiver.archiver_tasks import add_file_to_db_task

    form = UploadFileForm()
    # set filing code choices from app config
    form.destination_directory.choices = flask.current_app.config.get('DIRECTORY_CHOICES')
    if form.validate_on_submit():
        try:
            archival_filename = form.upload.data.filename
            temp_path = utils.create_temp_file_path(archival_filename)
            form.upload.data.save(temp_path)

            # raise exception if there is not the required fields filled out in the submitted form.
            if not ((form.project_number.data and form.destination_directory.data) or form.destination_path.data):
                raise Exception(
                    "Missing required fields -- either project_number and Destination_directory or destination_path")

            if form.new_filename.data:
                archival_filename = utils.cleanse_filename(form.new_filename.data)
            arch_file = ArchivalFile(current_path=temp_path, project=form.project_number.data,
                                     new_filename=archival_filename, notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'))
            
            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data:
                destination_path_list = utils.split_path(form.destination_path.data)
                if len(destination_path_list) > 1:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    *destination_path_list[1:],
                                                    archival_filename)
                else:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    archival_filename)
                arch_file.cached_destination_path = destination_path
            
            archiving_successful, archiving_exception = arch_file.archive_in_destination()

            # If the file was successfully moved to its destination, we will save the data to the database
            if archiving_successful:
                # enqueue the task of adding the file to the database
                add_file_kwargs = {'filepath': arch_file.get_destination_path(), 'archiving': False} #TODO add archiving functionality
                nk_results = utils.enqueue_new_task(db=db,
                                                    enqueued_function=add_file_to_db_task,
                                                    task_kwargs=add_file_kwargs,
                                                    timeout=None)
                
                flask.flash(f'File archived here: \n{arch_file.get_destination_path()}', 'success')
                return flask.redirect(flask.url_for('archiver.upload_file'))

            else:
                raise Exception(
                    f"Following error while trying to archive file, {form.new_filename.data}:\nException: {archiving_exception}")

        except Exception as e:
            m = "Error occurred while trying to read form data, move the asset, or record asset info in database: "
            return web_exception_subroutine(flash_message=m,
                                            thrown_exception=e,
                                            app_obj=flask.current_app)

    return flask.render_template('upload_file.html', title='Upload File to Archive', form=form)


@archiver.route("/inbox_item", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN', 'ARCHIVIST'])
def inbox_item():
    """
    This function handles the archivist inbox mechanism for iterating (through each request) over the files in the user's inbox,
    presenting the user with a preview of the file, and processing the file according to the user's input.
    """
    
    # import task function here to avoid circular import
    from archives_application.archiver.archiver_tasks import add_file_to_db_task


    def get_no_preview_placeholder_url():
        """
        Selects a no_preview_image from ./static/default to use if no preview image can be generated for the inbox file.
        @return:
        """
        default_files_directory = os.path.join(os.getcwd(), *["archives_application", "static", "default"])
        placeholder_files = [x for x in os.listdir(default_files_directory) if x.lower().startswith("no_preview_image")]
        random_placeholder = random.choice(placeholder_files)
        return flask.url_for(r"static", filename="default/" + random_placeholder)

    def ignore_file(filepath):
        """Determines if the file at the path is not one we should be processing."""
        # file types that might end up in the INBOX directory but do not need to be archived
        filenames_to_ignore = ["thumbs.db"]
        file_extensions_to_ignore = ["git", "ini"]
        filename = utils.split_path(filepath)[-1]
        file_ext = filename.split(".")[-1]

        if filename.lower() in filenames_to_ignore:
            return True

        if file_ext in file_extensions_to_ignore:
            return True

        return False

    try:
        # Setup User inbox
        inbox_path = flask.current_app.config.get("ARCHIVIST_INBOX_LOCATION")
        user_inbox_path = os.path.join(inbox_path, get_user_handle())
        user_inbox_files = lambda: [thing for thing in os.listdir(user_inbox_path) if
                                    os.path.isfile(os.path.join(user_inbox_path, thing)) and not ignore_file(thing)]
        if not os.path.exists(user_inbox_path):
            os.makedirs(user_inbox_path)

        # if no files in the user inbox, move a file from the INBOX directory to the user inbox to be processed.
        # This avoids other users from processing the same file, creating errors.
        if not user_inbox_files():
            general_inbox_files = [t for t in os.listdir(inbox_path) if
                                   os.path.isfile(os.path.join(inbox_path, t)) and not ignore_file(t)]

            # if there are no files to archive in either the user inbox or the archivist inbox we will send the user to
            # the homepage.
            if not general_inbox_files:
                flask.flash("The archivist inboxes are empty. Add files to the inbox directories to archive them.", 'info')
                return flask.redirect(flask.url_for('main.home'))

            item_path = os.path.join(inbox_path, general_inbox_files[0])
            shutil.move(item_path, os.path.join(user_inbox_path, general_inbox_files[0]))

        inbox_files = user_inbox_files()
        arch_file_filename = None
        if inbox_files:
            arch_file_filename = user_inbox_files()[0]
        else:
            flask.flash("File has disappeared.", 'info')
            return flask.redirect(flask.url_for('main.home'))

        preview_generated = False
        preview_image_url = get_no_preview_placeholder_url()
        # Create the file preview image if it is a pdf
        arch_file_preview_image_path = None
        arch_file_path = os.path.join(user_inbox_path, arch_file_filename)
        if arch_file_filename.split(".")[-1].lower() in ['pdf']:
            arch_file_preview_image_path = utils.pdf_preview_image(pdf_path=arch_file_path,
                                                                       image_destination=utils.create_temp_file_path(''),
                                                                       )
            preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.split_path(arch_file_preview_image_path)[-1])
            preview_generated = True

        # if the file is a tiff, create a preview image that can be rendered in html
        tiff_file_extensions = ['tif', 'tiff']
        if arch_file_filename.split(".")[-1].lower() in tiff_file_extensions:
            arch_file_preview_image_path = utils.convert_tiff(tiff_path=arch_file_path,
                                                                  destination_directory=utils.create_temp_file_path(''),
                                                                  output_file_type='png')
            preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.split_path(arch_file_preview_image_path)[-1])

        # If the filetype can be rendered natively in html, copy file as preview of itself if the file is an image
        image_file_extensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']
        if arch_file_filename.split(".")[-1].lower() in image_file_extensions:
            preview_path = utils.create_temp_file_path(arch_file_filename)
            shutil.copy2(arch_file_path, preview_path)
            preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.split_path(preview_path)[-1])
            preview_generated = True
        
        # If we made a preview image, record the path in the session so it can be removed upon logout
        if preview_generated:
            if not flask.session[current_user.email].get('temporary files'):
                flask.session[current_user.email]['temporary files'] = []
            flask.session[current_user.email]['temporary files'].append(preview_image_url)

        form = InboxItemForm()
        form.destination_directory.choices = flask.current_app.config.get('DIRECTORY_CHOICES')

        # If the flask.session has data previously entered in this form, then re-enter it into the form before rendering
        # it in html.
        if flask.session.get(current_user.email) and flask.session.get(current_user.email).get('inbox_form_data'):
            sesh_data = flask.session.get(current_user.email).get('inbox_form_data')
            form.project_number.data = sesh_data.get('project_number')
            form.destination_path.data = sesh_data.get('destination_path')
            form.notes.data = sesh_data.get('notes')
            form.document_date.data = sesh_data.get('document_date')
            form.new_filename.data = sesh_data.get('new_filename')
            flask.session['inbox_form_data'] = None

    except Exception as e:

        m = "Issue setting up inbox item for archiving: "
        
        # Seems to be bug with the creation of the inbox path if the database (not the inbox host) is started after the application is started
        if type(e) == TypeError:
            m = f"""Issue setting up inbox item for archiving.
            TypeError with unexpected Nonetype object may suggest the application is requiring a restart.
            Addtional info --> Inbox path type: {str(type(flask.current_app.config.get("ARCHIVIST_INBOX_LOCATION")))}, User handle type: {str(type(get_user_handle()))}
            Error:"""
        return web_exception_subroutine(flash_message=m,
                                        thrown_exception=e,
                                        app_obj=flask.current_app)

    try:
        if form.validate_on_submit():

            # If the user clicked the download button, we send the file to the user, save what data the user has entered,
            # and rerender the page.
            if form.download_item.data:
                # boolean for whether to attempt opening the file in the browser
                file_can_be_opened_in_browser = arch_file_filename.split(".")[-1].lower() in ['pdf', 'html']
                flask.session[current_user.email]['inbox_form_data'] = form.data
                return flask.send_file(arch_file_path, as_attachment=not file_can_be_opened_in_browser)


            # raise exception if there is not the required fields filled out in the submitted form.
            if not ((form.project_number.data and form.destination_directory.data) or form.destination_path.data):
                raise Exception(
                    "Missing required fields -- either project_number and destination_directory or just a destination_path")

            upload_size = os.path.getsize(arch_file_path)
            archival_filename = arch_file_filename
            if form.new_filename.data:
                archival_filename = utils.cleanse_filename(form.new_filename.data)
            
            # make sure the archival filename includes the file extension
            file_ext = arch_file_filename.split(".")[-1]
            if not archival_filename.lower().endswith(file_ext.lower()):
                archival_filename = archival_filename + "." + file_ext    

            # strip the project number of any whitespace (in case an archivist adds a space after the project number)
            project_num = form.project_number.data
            if project_num:
                project_num = project_num.strip()
            arch_file = ArchivalFile(current_path=arch_file_path, project=project_num,
                                     new_filename=archival_filename, notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'),
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     destination_path=form.destination_path.data)
            
            destination_filename = arch_file.assemble_destination_filename()
            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data: #TODO make sure this is not a typo 
                destination_path_list = utils.split_path(form.destination_path.data)
                if len(destination_path_list) > 1:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    *destination_path_list[1:],
                                                    archival_filename)
                else:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    archival_filename)
                arch_file.cached_destination_path = destination_path
                arch_file.destination_dir = None
                destination_filename = archival_filename

            # archive the file in the destination and attempt to record the archival in the database    
            archiving_successful, archiving_exception = arch_file.archive_in_destination()
            
            # if the file was successfully archived, add the archiving event and the file to the application database
            if archiving_successful:
                try:
                    # add the archiving event to the database
                    archived_file = ArchivedFileModel(destination_path=arch_file.get_destination_path(),
                                                      project_number=arch_file.project_number,
                                                      date_archived=datetime.now(),
                                                      document_date=form.document_date.data,
                                                      destination_directory=arch_file.destination_dir,
                                                      file_code=arch_file.file_code, archivist_id=current_user.id,
                                                      file_size=upload_size, notes=arch_file.notes,
                                                      filename=destination_filename)
                    db.session.add(archived_file)
                    db.session.commit()

                    # add the file to the database
                    add_file_kwargs = {'filepath': arch_file.get_destination_path(), 'archiving': True}
                    nk_results = utils.enqueue_new_task(db=db,
                                                        enqueued_function=add_file_to_db_task,
                                                        task_kwargs=add_file_kwargs,
                                                        timeout=None)

                    # make sure that the old file has been removed
                    if os.path.exists(arch_file_path):
                        os.remove(arch_file_path)
                    flask.flash(f'File archived here: \n{arch_file.get_destination_path()}', 'success')

                except Exception as e:
                    # if the file wasn't deleted...
                    if os.path.exists(arch_file_path):
                        flask.flash(
                            f'File archived, but could not remove it from this location:\n{arch_file.current_path}\nException:\n{e.message}',
                            'warning')
                    else:
                        flask.current_app.logger.error(e, exc_info = True)
                        flask.flash(f"An error occured: {e}", 'warning')
                return flask.redirect(flask.url_for('archiver.inbox_item'))
            else:
                message = f'Failed to archive file:{arch_file.current_path} Destination: {arch_file.get_destination_path()} Error:'
                return web_exception_subroutine(flash_message=message,
                                                thrown_exception=archiving_exception,
                                                app_obj=flask.current_app)

        return flask.render_template('inbox_item.html', title='Inbox', form=form, item_filename=arch_file_filename,
                                     preview_image=preview_image_url)

    except Exception as e:
        return web_exception_subroutine(flash_message="Issue archiving document: ",
                                        thrown_exception=e,
                                        app_obj=flask.current_app)


@archiver.route("/archived_or_not", methods=['GET', 'POST'])
def archived_or_not():

    def cleanse_locations_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        # New df is only the columns we want, 'file_server_directories' and 'filename'
        df = df[['file_server_directories', 'filename']]
        # New row  'filepath' which joins the directories and the filename
        df['filepath'] = df.apply(lambda row: (row['file_server_directories'] + "/" + row['filename']), axis=1)
        return df[['filepath']]


    form = ArchivedOrNotForm()
    if form.validate_on_submit():
        try:
            # Save file to temporary directory
            filename = form.upload.data.filename
            temp_path = utils.create_temp_file_path(filename)
            form.upload.data.save(temp_path)
            file_hash = utils.get_hash(filepath=temp_path)

            matching_file = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
            if not matching_file:
                flask.flash(f"No file found with hash {file_hash}", 'info')
                return flask.redirect(flask.url_for('archiver.archived_or_not'))
            
            # Create html table of all locations that match the hash
            locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == matching_file.id)
            locations_df = utils.db_query_to_df(locations)
            os.remove(temp_path)
            if locations_df.empty:
                raise Exception(f"No locations found for file, {filename}, with hash {file_hash}, though file was found in database.")
            
            locations_df = cleanse_locations_dataframe(locations_df)
            location_table_html = locations_df.to_html()
            return flask.render_template('locations_tables.html', title='Archived Locations',
                                         file_locations_list=[{"filename":filename, "locations_html":location_table_html}])

        except Exception as e:
            os.remove(temp_path)
            return web_exception_subroutine(flash_message="Error looking for instances of file on Server.",
                                              thrown_exception=e,
                                              app_obj=flask.current_app)

    return flask.render_template('archived_or_not.html', title='Determine if File Already Archived', form=form)


def retrieve_location_to_start_scraping():
    """
    Retrieves the location from which to start scraping files. 
    This is the last directory scraped of the most recent completed scrape.
    If there is no location in the database, we use the root of the archives directory.
    
    :return: str Location to start scraping files
    """
    location = flask.current_app.config.get("ARCHIVES_LOCATION")

    most_recent_scrape = db.session.query(WorkerTaskModel).filter(
        db.cast(WorkerTaskModel.task_results, db.String).like('%Next Start Location%'),
        WorkerTaskModel.time_completed.isnot(None)
    ).order_by(db.desc(WorkerTaskModel.time_completed)).first()

    if most_recent_scrape is not None:
        previous_scrape_location = most_recent_scrape.task_results["Next Start Location"]  
        location = os.path.join(location, previous_scrape_location)
    return location


def exclude_extensions(f_path, ext_list=EXCLUDED_FILE_EXTENSIONS):
    """
    checks filepath to see if it is using excluded extensions
    """
    filename = utils.split_path(f_path)[-1]
    return any([filename.endswith(ext) for ext in ext_list])


def exclude_filenames(f_path, excluded_names=EXCLUDED_FILENAMES):
    """
    excludes files with certain names
    """
    filename = utils.split_path(f_path)[-1]
    return filename in excluded_names


@archiver.route("/scrape_files", methods=['GET', 'POST'])
def scrape_files():
    """
    Enqueues a task to scrape files from the archives location. Built to accept requests from logged in users
    and from requests that include user credentials as request arguments. The scraping will automatically
    begin at the scrape
    Use the 'user' argument to specify the user to use for the scrape.
    Use the 'password' argument to specify the password for the user.
    Use the 'scrape_time' to specify how long the scrape should run for.
    """
    # import task here to avoid circular import
    from archives_application.archiver.archiver_tasks import scrape_file_data_task
    
    # Check if the request includes user credentials or is from a logged in user. 
    # User needs to have ADMIN role.
    request_is_authenticated = False
    if flask.request.args.get('user'):
        user_param = flask.request.args.get('user')
        password_param = flask.request.args.get('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):
            request_is_authenticated = True

    elif current_user:
        if current_user.is_authenticated and has_admin_role(current_user):
            request_is_authenticated = True

    # If the request is authenticated, we can proceed to enqueue the task.
    if request_is_authenticated:
        try:
            # Retrieve scrape parameters
            scrape_location = retrieve_location_to_start_scraping()
            scrape_time = 8
            file_server_root_index = len(utils.split_path(flask.current_app.config.get("ARCHIVES_LOCATION")))
            if flask.request.args.get('scrape_time'):
                scrape_time = int(flask.request.args.get('scrape_time'))
            scrape_time = timedelta(minutes=scrape_time)
            # Create our own job id to pass to the task so it can manipulate and query its own representation 
            # in the database and Redis.
            scrape_job_id = f"{scrape_file_data_task.__name__}_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
            scrape_params = {"archives_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
                            "start_location": scrape_location,
                            "file_server_root_index": file_server_root_index,
                            "exclusion_functions": [exclude_extensions, exclude_filenames],
                            "scrape_time": scrape_time,
                            "queue_id": scrape_job_id}
            nk_call_kwargs = {'result_ttl': 43200}
            nk_results = utils.enqueue_new_task(db=db,
                                                enqueued_function=scrape_file_data_task,
                                                task_kwargs=scrape_params,
                                                enqueue_call_kwargs=nk_call_kwargs,
                                                timeout=scrape_time.seconds + 60)
            
            nk_results = {"enqueueing_results": utils.serializablize_dict(nk_results),
                          "scrape_task_params": utils.serializablize_dict(scrape_params)}
            return flask.Response(json.dumps(nk_results), status=200)

        except Exception as e:
            mssg = "Error enqueuing task"
            if e.__class__.__name__ == "ConnectionError":
                mssg = "Error connecting to Redis. Is Redis running?"
            return api_exception_subroutine(response_message=mssg, thrown_exception=e)   
        
    return flask.Response("Unauthorized", status=401)


@archiver.route("/test/scrape_files", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN'])
def test_scrape_files():
    """
    Endpoint for testing archiver_tasks.scrape_file_data function in development.
    """
    # import task here to avoid circular import
    from archives_application.archiver.archiver_tasks import scrape_file_data_task

    # Retrieve scrape parameters
    scrape_location = retrieve_location_to_start_scraping()
    scrape_time = 8
    file_server_root_index = len(utils.split_path(flask.current_app.config.get("ARCHIVES_LOCATION")))
    if flask.request.args.get('scrape_time'):
        scrape_time = int(flask.request.args.get('scrape_time'))
    scrape_time = timedelta(minutes=scrape_time)
    
    # Record test task in database
    scrape_job_id = f"{scrape_file_data_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
    new_task_record = WorkerTaskModel(task_id=scrape_job_id, time_enqueued=str(datetime.now()), origin="test",
                        function_name=scrape_file_data_task.__name__, status="queued")
    db.session.add(new_task_record)
    db.session.commit()

    scrape_params = {"archives_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
                    "start_location": scrape_location,
                    "file_server_root_index": file_server_root_index,
                    "exclusion_functions": [exclude_extensions, exclude_filenames],
                    "scrape_time": scrape_time,
                    "queue_id": scrape_job_id}
    scrape_results = scrape_file_data_task(**scrape_params)
    
    # prepare scrape results for JSON serialization
    scrape_params.pop("exclusion_functions") # remove exclusion_fuctions from scrape_params because it is not JSON serializable
    scrape_params["scrape_time"] = str(scrape_params["scrape_time"])
    scrape_dict = {"scrape_results": scrape_results, "scrape_params": scrape_params}
    return flask.Response(json.dumps(scrape_dict), status=200)


@archiver.route("/confirm_file_locations", methods=['GET', 'POST'])
def confirm_db_file_locations():
    """
    This function will confirm that the file locations in the database are still valid.
    """
    # import task here to avoid circular import
    from archives_application.archiver.archiver_tasks import confirm_file_locations_task
    
    # Check if the request includes user credentials or is from a logged in user. 
    # User needs to have ADMIN role.
    request_is_authenticated = False
    if flask.request.args.get('user'):
        user_param = flask.request.args.get('user')
        password_param = flask.request.args.get('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):
            request_is_authenticated = True

    elif current_user:
        if current_user.is_authenticated and has_admin_role(current_user):
            request_is_authenticated = True
    
    if request_is_authenticated:
        try:
            confirming_time = 10
            if flask.request.args.get('confirming_time'):
                confirming_time = int(flask.request.args.get('confirming_time'))
            
            confirming_time = timedelta(minutes=confirming_time)
            confirm_params = {"archive_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
                              "confirming_time": confirming_time}
            nk_results = utils.enqueue_new_task(db=db,
                                                enqueued_function=confirm_file_locations_task,
                                                task_kwargs=confirm_params,
                                                timeout=confirming_time.seconds + 600)
            
            # prepare task enqueueing info for JSON serialization
            nk_results = utils.serializablize_dict(nk_results)
            confirm_params['confirming_time'] = str(confirm_params['confirming_time'])
            confirm_dict = {"enqueueing_results": nk_results, "confirmation_task_params": confirm_params}
            return flask.Response(json.dumps(confirm_dict), status=200)

        except Exception as e:
            mssg = "Error enqueuing task"
            if e.__class__.__name__ == "ConnectionError":
                mssg = "Error connecting to Redis. Is Redis running?"
            return api_exception_subroutine(response_message=mssg, thrown_exception=e)
    
    return flask.Response("Unauthorized", status=401)


@archiver.route("/test/confirm_files", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN'])
def test_confirm_files():
    """
    Endpoint for testing archiver_tasks.confirm_file_locations function in development.
    """

    from archives_application.archiver.archiver_tasks import confirm_file_locations_task

    try:
        # Record test task in database
        confirm_job_id = f"{confirm_file_locations_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
        new_task_record = WorkerTaskModel(task_id=confirm_job_id, time_enqueued=str(datetime.now()), origin="test",
                            function_name=confirm_file_locations_task.__name__, status="queued")
        db.session.add(new_task_record)
        db.session.commit()
    
        confirmation_params = {"archive_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
                               "confirming_time": timedelta(minutes=3),
                               "queue_id": confirm_job_id}
        confirm_results = confirm_file_locations_task(**confirmation_params)
        confirmation_params['confirming_time'] = str(confirmation_params['confirming_time'])
        confirm_dict = {"confirmation_results": confirm_results, "confirmation_params": confirmation_params}
        return flask.Response(json.dumps(confirm_dict), status=200)
    
    except Exception as e:
        print(e)
        flask.flash(f"Confirm file locations error: {e}", 'warning')
        return flask.redirect(flask.url_for('main.home'))


@archiver.route("/file_search", methods=['GET', 'POST'])
def file_search():
    """
    Endpoint for searching the database for files.
    """

    form = FileSearchForm()
    csv_filename_prefix = "search_results_"
    timestamp_format = r'%Y%m%d%H%M%S'
    html_table_row_limit = 1000
    
    # if the request includes a timestamp for a previous search results, then we will return the spreadsheet of the search results.
    # If there is not a corresponding file, then we will raise an error.
    if flask.request.args.get('timestamp'):
        try:
            timestamp = flask.request.args.get('timestamp')
            csv_filepath = utils.create_temp_file_path(f'{csv_filename_prefix}{timestamp}.csv')
            if not os.path.exists(csv_filepath):
                # reformat timestamp to be more human-readable
                timestamp = datetime.strftime(datetime.strptime(timestamp, timestamp_format), r'%Y-%m-%d %H:%M:%S')
                message = f"Search results from {timestamp} not found. Expected file at {csv_filepath}"
                raise FileNotFoundError(message)
            
            return flask.send_file(csv_filepath, as_attachment=True)
        
        except Exception as e:
            message = f"Error retrieving search results:\n{e}"
            return web_exception_subroutine(flash_message=message,
                                            thrown_exception=e,
                                            app_obj=flask.current_app)
    
    if form.validate_on_submit():
        try:
            archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
            search_query = None
            search_term = str(form.search_term.data).lower()
            if form.search_location.data:
                search_location = utils.user_path_to_app_path(path_from_user=form.search_location.data,
                                                                location_path_prefix=archives_location)
                search_location_list = utils.split_path(search_location)
                mount_path_index = len(utils.split_path(archives_location))
                search_location_search_term = os.path.join(*search_location_list[mount_path_index:])
                search_query = FileLocationModel.query.filter(FileLocationModel.file_server_directories.like(f"%{search_location_search_term}%"),
                                                              func.lower(FileLocationModel.filename).like(f"%{search_term}%"))
            else:
                search_query = FileLocationModel.query.filter(func.lower(FileLocationModel.filename).like(f"%{search_term}%"))

            search_df = utils.db_query_to_df(search_query)
            if search_df.empty:
                flask.flash(f"No files found matching search term: {search_term}", 'warning')
                return flask.redirect(flask.url_for('archiver.file_search'))
            
            search_df['Location'] = search_df.apply(lambda row: utils.user_path_from_db_data(file_server_directories=row['file_server_directories'], archives_location=archives_location), axis=1)
            cols_to_remove = ['id', 'file_id', 'file_server_directories', 'existence_confirmed', 'hash_confirmed']
            search_df.drop(columns=cols_to_remove, inplace=True)
            search_df.rename(columns={'filename': 'Filename'}, inplace=True)
            timestamp = datetime.now().strftime(r'%Y%m%d%H%M%S')
            too_many_results = len(search_df) > html_table_row_limit
            csv_filepath = utils.create_temp_file_path(filename=f'{csv_filename_prefix}{timestamp}.csv')
            search_df.to_csv(csv_filepath, index=False)
            search_df = search_df.head(html_table_row_limit)
            search_df_html = utils.html_table_from_df(df=search_df, path_columns=['Location'])
            
            search_results_html = flask.render_template('file_search_results.html',
                                                        search_results_table=search_df_html,
                                                        timestamp=timestamp,
                                                        search_term=form.search_term.data,
                                                        too_many_results=too_many_results)
            return search_results_html
            
        except Exception as e:
            web_exception_subroutine(flash_message="Error processing query, searching database, and/or processing search results: ",
                                     thrown_exception=e,
                                     app_obj=flask.current_app)
    
    return flask.render_template('file_search.html', form=form)
            

        
@archiver.route("/scrape_location", methods=['GET', 'POST'])
def scrape_location():
    """
    Endpoint for scraping a file server location for file data and reconciling the data with the reality of the file server.
    """
    # import task here to avoid circular import
    from archives_application.archiver.archiver_tasks import scrape_location_files_task

    form = ScrapeLocationForm()
    if form.validate_on_submit():
        search_location = utils.user_path_to_app_path(path_from_user=form.scrape_location.data,
                                                        location_path_prefix=flask.current_app.config.get('ARCHIVES_LOCATION'))
        
        scrape_params = {'scrape_location': search_location,
                         'recursively': form.recursive.data,
                         'confirm_data': True}
        nk_results = utils.enqueue_new_task(db=db,
                                            enqueued_function=scrape_location_files_task,
                                            task_kwargs=scrape_params,
                                            timeout=3600)
        id = nk_results.get("_id")
        function_call = nk_results.get("description")
        m = f"Scraping task has been successfully enqueued. Function Enqueued: {function_call}"
        flask.flash(m, 'success')
        return flask.redirect(flask.url_for('archiver.scrape_location'))
    
    return flask.render_template('scrape_location.html', form=form)