import flask
import random
import shutil
from .. import utilities
from .archival_file import ArchivalFile
from .server_edit import ServerEdit
from .forms import *
from flask_login import login_required, current_user
from archives_application.models import *
from archives_application import bcrypt
from typing import Callable


archiver = flask.Blueprint('archiver', __name__)


def exception_handling_pattern(flash_message, thrown_exception, app_obj):
    """
    Sub-process for handling patterns
    @param flash_message:
    @param thrown_exception:
    @param app_obj:
    @return:
    """
    flash_message = flash_message + f": {thrown_exception}"
    flask.flash(flash_message, 'error')
    app_obj.logger.error(thrown_exception, exc_info=True)
    return flask.redirect(flask.url_for('main.home'))


def get_user_handle():
    '''
    user's email handle without the rest of the address (eg dilbert.dogbert@ucsc.edu would return dilbert.dogbert)
    :return: string handle
    '''
    return current_user.email.split("@")[0]


@archiver.route("/server_change", methods=['GET', 'POST'])
@utilities.roles_required(['ADMIN', 'ARCHIVIST'])
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
        try:
            user_email = current_user.email
            archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')

            # if the user entered a path to delete
            if form.path_delete.data:
                deletion = ServerEdit(server_location=archives_location, change_type='DELETE', user=user_email,
                                      old_path=form.path_delete.data)
                deletion.execute()
                save_server_change(deletion)

            # if the user entered a path to change and the desired path change
            if form.current_path.data and form.new_path.data:
                renaming = ServerEdit(server_location=archives_location, change_type='RENAME', user=user_email,
                                      old_path=form.current_path.data,
                                      new_path=form.new_path.data)
                renaming.execute()
                save_server_change(renaming)

            # if the user entered a path to an asset to move and a location to move it to
            if form.asset_path.data and form.destination_path.data:
                move = ServerEdit(server_location=archives_location, change_type='MOVE', user=user_email,
                                  old_path=form.asset_path.data,
                                  new_path=form.destination_path.data)
                move.execute()
                save_server_change(move)

            # if user entered a path for a new directory to be made
            if form.new_directory.data:
                creation = ServerEdit(server_location=archives_location, change_type='CREATE', user=user_email, new_path=form.new_directory.data)
                creation.execute()
                save_server_change(creation)

            flask.flash(f'Requested change executed and recorded.', 'success')
            return flask.redirect(flask.url_for('archiver.server_change'))

        except Exception as e:
            return exception_handling_pattern(flash_message="Error processing or executing change: ",
                                              thrown_exception=e, app_obj=flask.current_app)
    return flask.render_template('server_change.html', title='Make change to file server', form=form)


@archiver.route("/upload_file", methods=['GET', 'POST'])
@login_required
def upload_file():
    form = UploadFileForm()
    # set filing code choices from app config
    form.destination_directory.choices = flask.current_app.config.get('DIRECTORY_CHOICES')
    temp_files_directory = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])
    if form.validate_on_submit():
        try:
            archival_filename = form.upload.data.filename
            temp_path = os.path.join(temp_files_directory, archival_filename)
            form.upload.data.save(temp_path)
            upload_size = os.path.getsize(temp_path)
            if form.new_filename.data:
                archival_filename = utilities.cleanse_filename(form.new_filename.data)
            arch_file = ArchivalFile(current_path=temp_path, project=form.project_number.data,
                                     new_filename=archival_filename, notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'))

            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data:
                destination_path_list = utilities.split_path(form.destination_path.data)
                if len(destination_path_list) > 1:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    *destination_path_list[1:],
                                                    archival_filename)
                else:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    archival_filename)
                arch_file.cached_destination_path = destination_path

            archiving_successful, archiving_exception = arch_file.archive_in_destination()

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
                return flask.redirect(flask.url_for('archiver.upload_file'))

            else:
                raise Exception(
                    f"Following error while trying to archive file, {form.new_filename.data}:\n{archiving_exception}")

        except Exception as e:
            return exception_handling_pattern(flash_message="Error occurred while trying to move the asset or record asset move info in database: ",
                                              thrown_exception=e, app_obj=flask.current_app)
    return flask.render_template('upload_file.html', title='Upload File to Archive', form=form)


@archiver.route("/inbox_item", methods=['GET', 'POST'])
@utilities.roles_required(['ADMIN', 'ARCHIVIST'])
def inbox_item():

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
        filename = utilities.split_path(filepath)[-1]
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

        preview_image_url = get_no_preview_placeholder_url()
        temp_files_directory = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])

        # create the file preview image if it is a pdf
        arch_file_preview_image_path = None
        arch_file_path = os.path.join(user_inbox_path, arch_file_filename)
        if arch_file_filename.split(".")[-1].lower() in ['pdf']:
            arch_file_preview_image_path = utilities.pdf_preview_image(arch_file_path, temp_files_directory)
            preview_image_url = flask.url_for(r"static", filename = "temp_files/" + utilities.split_path(arch_file_preview_image_path)[-1])

        # copy file as preview of itself if the file is an image
        image_file_extensions = ['jpg', 'tiff', 'jpeg', 'tif']
        if arch_file_filename.split(".")[-1].lower() in image_file_extensions:
            preview_path = os.path.join(temp_files_directory, arch_file_filename)
            shutil.copy2(arch_file_path, preview_path)
            preview_image_url = flask.url_for(r"static",
                                              filename="temp_files/" + utilities.split_path(preview_path)[-1])

        # if we made a preview image, record the path in the session so it can be removed upon logout
        if arch_file_preview_image_path:
            if not flask.session[current_user.email].get('temporary files'):
                flask.session[current_user.email]['temporary files'] = []
            flask.session[current_user.email]['temporary files'].append(arch_file_preview_image_path)

        form = InboxItemForm()
        form.destination_directory.choices = flask.current_app.config.get('DIRECTORY_CHOICES')

        # if the flask.session has data previously entered in this form, then re-enter it into the form before rendering
        # it in html
        if flask.session.get(current_user.email) and flask.session.get(current_user.email).get('inbox_form_data'):
            sesh_data = flask.session.get(current_user.email).get('inbox_form_data')
            form.project_number.data = sesh_data.get('project_number')
            form.destination_path.data = sesh_data.get('destination_path')
            form.notes.data = sesh_data.get('notes')
            form.document_date.data = sesh_data.get('document_date')
            form.new_filename.data = sesh_data.get('new_filename')
            flask.session['inbox_form_data'] = None

    except Exception as e:
        exception_handling_pattern(flash_message="Issue setting up inbox item for archiving: ", thrown_exception=e,
                                   app_obj=flask.current_app)

    try:
        if form.validate_on_submit():

            # if the user clicked the download button, we send the file to the user, save what data the user has entered,
            # and rerender the page.
            if form.download_item.data:
                # boolean for whether to attempt opening the file in the browser
                file_can_be_opened_in_browser = arch_file_filename.split(".")[-1].lower() in ['pdf', 'html']
                flask.session[current_user.email]['inbox_form_data'] = form.data
                return flask.send_file(arch_file_path, as_attachment=not file_can_be_opened_in_browser)

            upload_size = os.path.getsize(arch_file_path)
            archival_filename = arch_file_filename
            if form.new_filename.data:
                archival_filename = utilities.cleanse_filename(form.new_filename.data)
            arch_file = ArchivalFile(current_path=arch_file_path, project=form.project_number.data,
                                     new_filename=archival_filename, notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'),
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     destination_path=form.destination_path.data)

            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data:
                destination_path_list = utilities.split_path(form.destination_path.data)
                if len(destination_path_list) > 1:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    *destination_path_list[1:],
                                                    archival_filename)
                else:
                    destination_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                    archival_filename)
                arch_file.cached_destination_path = destination_path

            archiving_successful, archiving_exception = arch_file.archive_in_destination()
            if archiving_successful:
                try:
                    archived_file = ArchivedFileModel(destination_path=arch_file.get_destination_path(),
                                                      project_number=arch_file.project_number,
                                                      document_date=form.document_date.data,
                                                      destination_directory=arch_file.destination_dir,
                                                      file_code=arch_file.file_code, archivist_id=current_user.id,
                                                      file_size=upload_size, notes=arch_file.notes,
                                                      filename=arch_file.assemble_destination_filename())
                    db.session.add(archived_file)
                    db.session.commit()

                    # make sure that the old file has been removed
                    if os.path.exists(arch_file_path):
                        os.remove(arch_file_path) #TODO having problems deleting old files
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
                exception_handling_pattern(flash_message=message, thrown_exception=archiving_exception,
                                           app_obj=flask.current_app)

        return flask.render_template('inbox_item.html', title='Inbox', form=form, item_filename=arch_file_filename,
                                     preview_image=preview_image_url)

    except Exception as e:
        exception_handling_pattern(flash_message="Issue archiving document: ", thrown_exception=e,
                                   app_obj=flask.current_app)


@archiver.route("/archived_or_not", methods=['GET', 'POST'])
@login_required
def archived_or_not():

    def add_files_to_db(dir_path: str, db_session: db.session, file_server_root_index: int,
                        exclusion_functions: list[Callable[[str], bool]]):
        """
        This function is used to add files and their information to a database using an ORM session. It takes in a directory
         path, the ORM session, and an index for the file server's root directory. The function uses os.walk to iterate
         through the directory and its subdirectories, getting the file paths and adding them to the database with their
         respective file information such as hash, size, and extension. It also adds the file's location on the file server
         to the database, and updates any existing entries with the current existence and hash confirmation dates.
        :param dir_path:
        :param db_session:
        :param file_server_root_index: integer that represents the index of the root directory of the file server in the
        file path.
        :return:
        """
        for root, dirs, files in os.walk(dir_path):
            filepaths = [os.path.join(root, f) for f in files]
            for file in filepaths:

                # if the file is excluded by one of the exclusion functions, move to next file
                if any([fun(file) for fun in exclusion_functions]):
                    continue

                file_hash = utilities.get_hash(filepath=file)
                file_entry = db_session.query(FileModel).filter(FileModel.hash == file_hash).first()
                if not file_entry:
                    file_size = os.path.getsize(file)
                    path_list = utilities.split_path(file)
                    extension = path_list[-1].split(".")[-1].lower()
                    model = FileModel(hash=file_hash,
                                      size=file_size,
                                      extension=extension
                                      )
                    db_session.add(model)
                    db_session.commit()

                    file_entry = db_session.query(FileModel).filter(FileModel.hash == file_hash).first()

                path_list = utilities.split_path(file)
                file_server_dirs = os.path.join(*path_list[file_server_root_index:-1])
                filename = path_list[-1]

                # query to see if the current path is already represented in the database
                path_entry = db_session.query(FileLocationModel).filter(
                    FileLocationModel.file_server_directories == file_server_dirs,
                    FileLocationModel.filename == filename).first()
                confirmed_exists_dt = datetime.now()
                confirmed_hash_dt = datetime.now()

                # if there is a entry for this path in the database update the dates now we have confirmed location and that
                # the file has not changed (hash is same.)
                if path_entry:
                    entry_updates = {"existence_confirmed": confirmed_exists_dt, "hash_confirmed": confirmed_hash_dt}
                    db_session.query(FileLocationModel).filter(
                        FileLocationModel.file_server_directories == file_server_dirs,
                        FileLocationModel.filename == filename).update(entry_updates)

                    db_session.commit()
                    continue

                new_location = FileLocationModel(file_id=file_entry.id, file_server_directories=file_server_dirs,
                                                 filename=filename, existence_confirmed=confirmed_exists_dt,
                                                 hash_confirmed=confirmed_hash_dt)
                db_session.add(new_location)
                db_session.commit()

    def number_of_new_files(dir_path: str, db_session: db.session, file_server_root_index: int):
        path_list = utilities.split_path(dir_path)
        file_server_dirs = os.path.join(*path_list[file_server_root_index:])
        files_in_db = db_session.query(FileLocationModel) \
            .filter(FileLocationModel.file_server_directories.startswith(file_server_dirs)).count()
        files_on_server = 0
        for _, _, files in os.walk(dir_path):
            files_on_server += len(files)

        return files_on_server - files_in_db

    def known_locations(filepath: str, db_session: db.session):
        filehash = utilities.get_hash(filepath=filepath)
        matching_file = db_session.query(FileModel).filter(FileModel.hash == filehash).first()
        if matching_file:
            locations = db_session.query(FileLocationModel).filter(FileLocationModel.file_id == matching_file.id)
            return list(locations)
        return []

    form = ArchivedOrNotForm()
    temp_files_directory = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])
    if form.validate_on_submit():
        try:
            # save file to temporary directory
            archival_filename = form.upload.data.filename
            temp_path = os.path.join(temp_files_directory, archival_filename)
            form.upload.data.save(temp_path)

            # process requires that user has entered a location
            search_location = form.search_path.data
            if not search_location:
                flask.flash(f"Need to specify a search location.", 'warning')
                flask.redirect(flask.url_for('archiver.archived_or_not'))

            # define file_exclusion functions which take a filepath and assess whether it is a file that be considered
            def exclude_extensions(f_path, ext_list=['DS_Store', '.ini']):
                """
                checks filepath to see if it using excluded extensions
                """
                filename = utilities.split_path(f_path)[-1]
                return any([filename.endswith(ext) for ext in ext_list])

            def exclude_filenames(f_path, excluded_names=['Thumbs.db', 'thumbs.db', 'desktop.ini']):
                """
                excludes files with certain names
                """
                filename = utilities.split_path(f_path)[-1]
                return filename in excluded_names

            file_server_root_directory_index = len(
                utilities.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            # TODO how to process entered location to one useful by application
            # search_path_list = utilities.split_path(search_location)
            # search_location = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'), *search_path_list[1:])
            search_location = utilities.user_path_to_server_path(path_from_user=search_location,
                                                                 location_path_prefix=flask.current_app.config.get('ARCHIVES_LOCATION'))
            new_files = number_of_new_files(dir_path=search_location,
                                            db_session=db.session,
                                            file_server_root_index=file_server_root_directory_index)
            if new_files:
                add_files_to_db(dir_path=search_location,db_session=db.session,
                                file_server_root_index=file_server_root_directory_index,
                                exclusion_functions=[exclude_extensions, exclude_filenames])

            locations = known_locations(filepath=temp_path, db_session=db.session)
            # prevent list of locations from getting too long
            if len(locations) > 5:
                locations = locations[:4]


            make_full_path = lambda server_dirs, filename: os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'),
                                                              server_dirs, filename)

            # make newline delimited list of paths that have the folder and exist in the search directory
            locations_in_search = [make_full_path(pth.file_server_directories, pth.filename) for pth in locations if
                                   make_full_path(pth.file_server_directories, pth.filename).startswith(
                                       search_location)]
            locations_str = "\n".join(locations_in_search)
            if not locations_in_search:
                locations_str = "None found."
            else:
                locations_str = "Locations found:\n" + locations_str

            flask.flash(locations_str, 'message')
            flask.redirect(flask.url_for('archiver.archived_or_not'))


        except Exception as e:
            exception_handling_pattern(flash_message="Error looking for instances of file on Server.",
                                       thrown_exception=e,
                                       app_obj=flask.current_app)

    return flask.render_template('archived_or_not.html', title='Determine if File Already Archived', form=form)


