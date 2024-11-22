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
from urllib import parse


# imports from this application
import archives_application.archiver.forms as archiver_forms
from archives_application.archiver.archival_file import ArchivalFile
from archives_application import utils
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
    path_list = utils.FileServerUtils.split_path(file_path)
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


def exclude_extensions(f_path, extensions_list=EXCLUDED_FILE_EXTENSIONS):
    """
    checks filepath to see if it is using excluded extensions
    """
    filename = utils.FileServerUtils.split_path(f_path)[-1]
    return any([filename.endswith(ext) for ext in extensions_list])


def exclude_filenames(f_path, excluded_names=EXCLUDED_FILENAMES):
    """
    excludes files with certain names
    """
    filename = utils.FileServerUtils.split_path(f_path)[-1]
    return filename in excluded_names


@archiver.route("/api/server_change", methods=['GET', 'POST'])
@archiver.route("/server_change", methods=['GET', 'POST'])
def server_change():
    """
    Handles server change requests for the file server, either through a web form submission or an API request.

    This endpoint allows users with the appropriate permissions ('ADMIN' or 'ARCHIVIST') to perform server-side file operations such as deleting, renaming, moving, or creating directories and files.

    Access Methods:
    1. **Web Interface**:
       - Users can access a form to submit server change requests directly via the web interface.
    2. **API Requests**:
       - Users can make API requests by sending parameters in the URL query string, request headers, or form data.

    Supported HTTP Methods:
    - **GET**: Displays the server change form to the user.
    - **POST**: Processes the server change request, either from the submitted form or directly through an API request.

    Authentication:
    - **Web Interface**:
      - Users must be logged in and have the necessary permissions.
    - **API Requests**:
      - Users must provide valid credentials.
      - Parameters `user` and `password` must be provided in the URL parameters, request headers, or form data.

    Permissions:
    - Only users with roles `'ADMIN'` or `'ARCHIVIST'` can perform server changes.
    - Users with `'ADMIN'` role are exempt from limits on the number of files and data size affected by operations.

    Parameters (for API requests and form submissions):
    - `user` (str): The email of the user making the request. Provide in URL parameters, request headers, or form data.
    - `password` (str): The password of the user making the request. Provide in URL parameters, request headers, or form data.
    - `edit_type` (str): Specifies the type of server edit to perform. Must be one of:
        - `'DELETE'`: Deletes the item at `old_path`.
        - `'RENAME'`: Renames an item from `old_path` to `new_path`.
        - `'MOVE'`: Moves an item from `old_path` to `new_path`.
        - `'CREATE'`: Creates a new directory at `new_path`.
      Provide in URL parameters, request headers, or form data.
    - `old_path` (str): The original path for file/directory operations (required for `'DELETE'`, `'RENAME'`, `'MOVE'`). Provide in URL parameters, request headers, or form data.
    - `new_path` (str): The new path for file/directory operations (required for `'RENAME'`, `'MOVE'`, `'CREATE'`). Provide in URL parameters, request headers, or form data.

    Returns:
    - **Web Interface**:
      - On **GET**: Renders the server change form.
      - On successful **POST**: Redirects to the home page with a success message.
      - On error: Redirects to the home page with an error message.
    - **API Requests**:
      - On success: Returns a response with status code `200` and a success message.
      - On error: Returns a response with an appropriate status code and error message.

    Notes:
    - When using the web form, users should only fill in the fields relevant to the operation they wish to perform. The form validation ensures that only one operation is specified per request.
    - For API requests, all parameters (`user`, `password`, `edit_type`, `old_path`, `new_path`) can be supplied via URL parameters, request headers, or form data.
    - Limits on the number of files and total data size affected by operations are configurable in the application settings. Users with `'ADMIN'` role are exempt from these limits.
    - The endpoint uses the `ServerEdit` class to execute the requested file operations.
    - In case of an exception during the server change operation, an appropriate error message is returned, and the error is logged.

    Examples:
    - **Deleting a file via API**:
      - Parameters:
        - `edit_type=DELETE`
        - `old_path=/path/to/file.txt`
      - Provide authentication credentials (`user` and `password`).
    - **Renaming a directory via Web Form**:
      - Fill in `Current Path` with the current directory path.
      - Fill in `New Path` with the desired directory name or path.
      - Select `RENAME` as the edit type.

    Raises:
    - **Unauthorized (401)**: If the user is not authenticated or lacks the necessary permissions.
    - **Bad Request (400)**: If the form validation fails or required parameters are missing.
    - **Exception**: Various exceptions may be raised due to issues like invalid paths, access violations, or server errors.
    """
    
    # imported here to avoid circular import
    from archives_application.archiver.server_edit import ServerEdit

    def validate_single_change(form):
        """
        Checks if too many changes are requested in the form.
        
        Only one of the following pairs of form fields should have data:
        - path_delete
        - (current_path and new_path)
        - (asset_path and destination_path)
        - new_directory
        
        Returns:
        - True if more than one pair has data.
        - False otherwise.
        """
        changes_count = 0

        if form.path_delete.data:
            changes_count += 1

        if form.current_path.data and form.new_path.data:
            changes_count += 1

        if form.asset_path.data and form.destination_path.data:
            changes_count += 1

        if form.new_directory.data:
            changes_count += 1

        return changes_count > 1

    roles_allowed = ['ADMIN', 'ARCHIVIST']
    has_correct_permissions = lambda user: any([role in user.roles.split(",") for role in roles_allowed])
    new_path = None
    old_path = None
    edit_type = None
    user_email = None

    # Check if the request includes user credentials or is from a logged in user. 
    # User needs to have ADMIN role.
    request_is_authenticated = False
    form_request = True
    user_param = utils.FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        form_request = False
        password_param = utils.FlaskAppUtils.retrieve_request_param('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and has_correct_permissions(user=user):
            request_is_authenticated = True
            new_path = parse.unquote(utils.FlaskAppUtils.retrieve_request_param('new_path'))
            old_path = parse.unquote(utils.FlaskAppUtils.retrieve_request_param('old_path'))
            edit_type = utils.FlaskAppUtils.retrieve_request_param('edit_type')
            user_email = user.email

    elif current_user:
        if current_user.is_authenticated and has_correct_permissions(current_user):
            request_is_authenticated = True

    if not request_is_authenticated:
        return flask.Response("Unauthorized", status=401)
    
    # retrieve limits to how much can be changed on the server, but if the user has admin credentials,
    # there are no limits and they are set to zero
    files_limit = flask.current_app.config.get('SERVER_CHANGE_FILES_LIMIT')
    data_limit = flask.current_app.config.get('SERVER_CHANGE_DATA_LIMIT')
    archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
    
    # if this is a user using the form to elicit a server change, we will validate the form
    # and retrieve ServerEdit object params
    if form_request:
        form = archiver_forms.ServerChangeForm()
        if form.validate_on_submit():
            # raise error if multiple edits are submitted on single form
            if validate_single_change(form):
                flask.flash("Too many changes requested. Please submit a single change at a time.", 'error')
                return flask.redirect(flask.url_for('archiver.server_change'))

            user_email = current_user.email
            
            # if the user has admin credentials, there are no limits
            if utils.FlaskAppUtils.has_admin_role(current_user):
                files_limit, data_limit = 0, 0

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
    
    # if we have ServerEdit object params, we will execute the change
    if edit_type:
        try:   
            server_edit = ServerEdit(server_location=archives_location,
                                     change_type=edit_type,
                                     new_path=new_path,
                                     old_path=old_path)
            nq_results = server_edit.execute(files_limit=files_limit, effected_data_limit=data_limit, timeout=1200)


            # record the change in the database
            editor = UserModel.query.filter_by(email=user_email).first()
            change_model = ServerChangeModel(old_path=server_edit.old_path,
                                             new_path=server_edit.new_path,
                                             change_type=server_edit.change_type,
                                             files_effected=server_edit.files_effected,
                                             data_effected=server_edit.data_effected,
                                             date=datetime.now(),
                                             user_id=editor.id)
            db.session.add(change_model)
            db.session.commit()

            # retrieve change_model id and add it to the nq_results
            nq_results['server change index'] = change_model.id

            # if this is a form request, we will flash a message and redirect to the server change form
            if form_request:
                flask.flash(f"Requested '{edit_type}' change executed and recorded.", 'success')
                return flask.redirect(flask.url_for('archiver.server_change'))
            
            # if this is an API request, we will return 200 and enqueing results
            nq_results = utils.serializable_dict(nq_results)
            return flask.Response(json.dumps(nq_results), status=200)

        except Exception as e:
            m = "Error processing or executing change: "
            if form_request:
                return web_exception_subroutine(flash_message=m,
                                                thrown_exception=e,
                                                app_obj=flask.current_app)
            
            # for AttributeError("'NoneType' object has no attribute 'replace'")
            if str(e) == "'NoneType' object has no attribute 'replace'":
                m = "No old_path parameter provided: "

            return api_exception_subroutine(response_message=m, thrown_exception=e)
    
    return flask.render_template('server_change.html', title='Make change to file server', form=form)


@archiver.route("/batch_move", methods=['GET', 'POST'])
def batch_move_edit():
    """
    Handles batch moving of selected files or directories from a source directory to a destination directory.

    This endpoint provides a form that allows users to select multiple files or subdirectories within a specified source directory (`asset_path`) and move them collectively to a destination directory (`destination_path`).

    Workflow:
    1. The user enters the `asset_path`, which is the path to the source directory containing items to move.
    2. The endpoint displays the contents of the specified source directory.
    3. The user selects one or more files or subdirectories (`contents_to_move`) from the displayed contents.
    4. The user specifies the `destination_path` where the selected items will be moved.
    5. Upon form submission, the selected items are moved to the destination directory.

    Supported Methods:
    - **GET**: Renders the batch move form for user input.
    - **POST**: Processes the submitted form and initiates the batch move operation.

    Form Fields:
    - `asset_path` (str): The path to the source directory containing the items to move.
    - `contents_to_move` (List[str]): A list of filenames or subdirectory names selected for moving.
    - `destination_path` (str): The path to the destination directory where items will be moved.

    Returns:
    - **HTML Template**: Renders the batch move form along with success or error messages based on the operation's outcome.

    Notes:
    - Users must be authenticated and have the necessary permissions to perform batch move operations.
    - There may be limits on the number of files and the total data size that can be moved unless the user has administrative privileges.
    - If the user has admin permissions and includes the query parameter `test=true`, the batch move task will execute synchronously for testing purposes.
    - Error messages will be displayed if the operation encounters issues, such as invalid paths or permission errors.

    Examples:
    - Use this endpoint to move multiple project folders from a staging directory to an archive location in a single operation.
    - Organize files by moving selected documents from one directory to another without moving all contents.

    Raises:
    - **Unauthorized**: If the user is not authenticated or lacks the required permissions.
    - **Exception**: Various exceptions may be raised due to issues like invalid paths, access violations, or server errors.
    """
    
    # imported here to avoid circular import
    from archives_application.archiver.server_edit import directory_contents_quantities
    from archives_application.archiver.archiver_tasks import batch_move_edits_task

    # determine if the request is for testing the associated worker task
    # if the testing worker task, the task will be executed on this process and
    # not enqueued to be executed by the worker
    testing = False
    if utils.FlaskAppUtils.retrieve_request_param('test', None) \
        and utils.FlaskAppUtils.retrieve_request_param('test').lower() == 'true' \
        and utils.FlaskAppUtils.has_admin_role(current_user):
        testing = True

    # retrieve limits to how much can be changed on the server, but if the user has admin credentials,
    # there are no limits and they are set to zero
    files_limit = flask.current_app.config.get('SERVER_CHANGE_FILES_LIMIT')
    data_limit = flask.current_app.config.get('SERVER_CHANGE_DATA_LIMIT')
    archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
    choose_contents = False

    form = archiver_forms.BatchMoveEditForm()
    contents_choices = []
    if form.asset_path.data:
        app_asset_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=form.asset_path.data,
                                                                   app=flask.current_app)
        contents_dir_size_tuple = lambda dir: directory_contents_quantities(dir_path=os.path.join(app_asset_path, dir),
                                                                            server_location=archives_location,
                                                                            db=db)
        # Set the choices for contents_to_move based on the asset_path
        user_asset_path = form.asset_path.data
        app_asset_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=user_asset_path, app=flask.current_app)
        contents = os.listdir(app_asset_path)
        
        if not contents:
            flask.flash(f"No contents found in {user_asset_path}", 'error')
            return flask.redirect(flask.url_for('archiver.batch_move_edit'))
        contents_dirs = [c for c in contents if os.path.isdir(os.path.join(app_asset_path, c))]
        contents_files = [c for c in contents if os.path.isfile(os.path.join(app_asset_path, c))]
        # if the user has selected contents to move, do not bother with contents sizes
        if form.contents_to_move.data and form.contents_to_move.data != []:
            contents_choices = [(c, f"{c} (dir)") for c in contents_dirs]
            contents_choices += [(c, f"{c} (file)") for c in contents_files]

        # otherwise provide the contents sizes
        else:
            contents_choices = []
            size_as_mb = lambda size: round(size / 1024 / 1024, 2)
            # add directories to the contents_dict
            for c_dir in contents_dirs:
                c_dir_file_count, c_dir_size = contents_dir_size_tuple(c_dir)
                c_dir_size = size_as_mb(c_dir_size)
                contents_choices.append((c_dir, f"{c_dir} (dir, {c_dir_file_count} files, {c_dir_size} MBs)"))
            
            # add files to the contents_dict
            for content_file in contents_files:
                file_size_mb = size_as_mb(os.path.getsize(os.path.join(app_asset_path, content_file)))
                contents_choices.append((content_file, f"{content_file} (file, {file_size_mb} MBs)"))
        
        form.contents_to_move.choices = contents_choices

    if form.validate_on_submit():
        try:
            # if the user has admin credentials, there are no limits
            if utils.FlaskAppUtils.has_admin_role(current_user):
                files_limit, data_limit = 0, 0

            # if useer entered an asset path, we will convert it to an app path
            # and re-render the page with the contents of the directory as checkboxes
            user_asset_path = form.asset_path.data
            user_destination_path = form.destination_path.data
            if form.asset_path.data:
                
                # if the user has not selected contents to move, we will render the page with the checkboxes
                # so that the user can select the contents to move.
                if not form.contents_to_move.data:

                    # get the contents of the directory and render the page with the checkboxes
                    # distinguis between directories and files in case a directory and file have the same name
                    choose_contents = True

                    
                    choices_form = archiver_forms.BatchMoveEditForm()
                    choices_form.contents_to_move.choices = contents_choices
                    choices_form.asset_path.data = user_asset_path
                    return flask.render_template('batch_move.html', title='Batch Move', form=choices_form, choose_contents=choose_contents)

                # if the user has selected contents to move, we will enqueue the task to move the contents
                if form.contents_to_move.data and form.contents_to_move.data != []:
                    contents_to_move = form.contents_to_move.data
                    batch_move_params = {"user_target_path": user_asset_path,
                                        "user_destination_path": user_destination_path,
                                        "user_id": current_user.id,
                                        "user_contents_to_move": contents_to_move}
                    
                    # if test call, execute the batch task on this process and return the results.
                    # Allows for simpler debugging of the task function.
                    if testing:
                        test_job_id = f"{batch_move_edits_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
                        new_task_record = WorkerTaskModel(task_id=test_job_id,
                                                        time_enqueued=str(datetime.now()),
                                                        origin='test',
                                                        function_name=batch_move_edits_task.__name__,
                                                        status= "queued")
                        db.session.add(new_task_record)
                        db.session.commit()
                        batch_move_params['queue_id'] = test_job_id
                        batch_move_results = batch_move_edits_task(**batch_move_params)
                        batch_move_results = utils.serializable_dict(batch_move_results)
                        return flask.jsonify(batch_move_results)
                    

                    # get total size of files and number of files to be moved to check against limits
                    if files_limit or data_limit:
                        files_num_effected, data_effected = 0, 0
                        for to_move_item in contents_to_move:
                            to_move_item_path = os.path.join(app_asset_path, to_move_item)
                            if os.path.isdir(to_move_item_path):
                                to_move_item_size, to_move_item_file_count = contents_dir_size_tuple(to_move_item)
                                files_num_effected += to_move_item_file_count
                                data_effected += to_move_item_size
                            
                            else:
                                files_num_effected += 1
                                data_effected += os.path.getsize(to_move_item_path)
                    
                        if files_num_effected > files_limit or data_effected > data_limit:
                            e_mssg = f"""
                            The content of the change requested surpasses the limits set.
                            Try splitting the change into smaller parts:
                            {user_asset_path}
                            Files effected: {files_num_effected}
                            Data effected: {data_effected}
                            """
                            raise Exception(e_mssg)

                    # create batch_move info json dictionary
                    batch_move_info = {"parameters": batch_move_params,
                                    "files_limit": files_limit,
                                    "data_limit": data_limit}
                    # enqueue the task to be executed by the worker
                    nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                                    enqueued_function=batch_move_edits_task,
                                                                    task_kwargs=batch_move_params,
                                                                    task_info=batch_move_info,
                                                                    timeout=None)
                    
                    success_message = f"Batch move task enqueued (job id: {nq_results['_id']})\nIt may take some time for the batch operation to complete."
                    flask.flash(success_message, 'success')
                    return flask.redirect(flask.url_for('archiver.batch_move_edit'))
        
        except Exception as e:
            m = "Error processing or executing batch move"
            return web_exception_subroutine(flash_message=m,
                                            thrown_exception=e,
                                            app_obj=flask.current_app)
        
    return flask.render_template('batch_move.html', title='Batch Move', form=form, choose_contents=choose_contents)


@archiver.route("/batch_edit", methods=['GET', 'POST'])  # TODO remove
@archiver.route("/api/consolidate_dirs", methods=['GET', 'POST'])
@archiver.route("/consolidate_dirs", methods=['GET', 'POST'])
def consolidate_dirs():
    """
    Consolidates directories with identical names within a specified parent directory into single directories.
    Uses a worker process to perform the consolidation operation.
    This endpoint allows users to merge the contents of two directories. This is particularly useful for organizing files when duplicate directories exist.

    Workflow:
    1. The user specifies the 'Path to Target Directory', the directory with the contents to be moved.
    2. The user enters a 'Destination Directory Path', the directory where the contents will be moved.
    3. The user can choose to remove empty directories after consolidation.
    4. Upon form submission, the contents of the target directory are moved to the destination directory. Any duplicate directories are consolidated.

    Supported Methods:
    - **GET**: Renders the consolidation form for user input.
    - **POST**: Processes the submitted form and initiates the consolidation operation.

    Form Fields:
    - `asset_path` (str): The path to the target directory containing the contents to be moved.
    - `destination_path` (str): The path to the destination directory where the contents will be moved.
    - `remove_empty_dirs` (bool): Option to remove the now empty asset_path location after consolidation.

    Returns:
    - **HTML Template**: Renders the consolidation form along with success or error messages based on the operation's outcome.

    Notes:
    - Users must be authenticated and have the necessary permissions to perform consolidation operations.
    - Limits on the number of files and total data size affected by operations are configurable in the application settings. Users with `'ADMIN'` role are exempt from these limits.
    - If the user has admin permissions and includes the query parameter `test=true`, the consolidation task will execute synchronously for testing purposes.
    - The endpoint uses the `consolidate_dirs_edit_task` function to perform the consolidation operation.
    - Error messages will be displayed if the operation encounters issues, such as invalid paths or permission errors.

    Raises:
    - **Unauthorized**: If the user is not authenticated or lacks the required permissions.
    - **Exception**: Various exceptions may be raised due to issues like invalid paths, access violations, or server errors.
    """
    # imported here to avoid circular import
    from archives_application.archiver.server_edit import directory_contents_quantities
    from archives_application.archiver.archiver_tasks import consolidate_dirs_edit_task
    form = archiver_forms.BatchServerEditForm()
    testing = False

    # determine if the request is for testing the associated worker task
    # if the testing worker task, the task will be executed on this process and
    # not enqueued to be executed by the worker
    if utils.FlaskAppUtils.retrieve_request_param('test', None) \
        and utils.FlaskAppUtils.retrieve_request_param('test').lower() == 'true' \
        and utils.FlaskAppUtils.has_admin_role(current_user):
        testing = True
    
    # retrieve limits to how much can be changed on the server, but if the user has admin credentials,
    # there are no limits and they are set to zero
    files_limit = flask.current_app.config.get('SERVER_CHANGE_FILES_LIMIT')
    data_limit = flask.current_app.config.get('SERVER_CHANGE_DATA_LIMIT')
    archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
    
    if form.validate_on_submit():
        try:
            
            # if the user has admin credentials, there are no limits
            if utils.FlaskAppUtils.has_admin_role(current_user):
                files_limit, data_limit = 0, 0
            
            user_asset_path = form.asset_path.data
            app_asset_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=user_asset_path,
                                                                       app=flask.current_app)
            user_destination_path = form.destination_path.data
            remove_asset = form.remove_asset.data
            files_num_effected, data_effected = directory_contents_quantities(dir_path=app_asset_path,
                                                                              server_location=archives_location,
                                                                              db = db)
            if files_limit or data_limit:
                if files_num_effected > files_limit or data_effected > data_limit:
                    e_mssg = f"""
                    The content of the change requested surpasses the limits set.
                    Try splitting the change into smaller parts:
                    {user_asset_path}
                    Files effected: {files_num_effected}
                    Data effected: {data_effected}
                    """
                    raise Exception(e_mssg)
            
            batch_move_params = {"user_target_path": user_asset_path,
                                 "user_destination_path": user_destination_path,
                                 "user_id": current_user.id,
                                 "remove_target": remove_asset,
                                 "removal_timeout": 1200}
            
            # if test call, execute the batch task on this process and return the results.
            # Allows for simpler debugging of the task function.
            if testing:
                test_job_id = f"{consolidate_dirs_edit_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
                new_task_record = WorkerTaskModel(task_id=test_job_id,
                                                  time_enqueued=str(datetime.now()),
                                                  origin='test',
                                                  function_name=consolidate_dirs_edit_task.__name__,
                                                  status= "queued")
                db.session.add(new_task_record)
                db.session.commit()
                batch_move_params['queue_id'] = test_job_id
                batch_move_results = consolidate_dirs_edit_task(**batch_move_params)
                batch_move_results = utils.serializable_dict(batch_move_results)
                return flask.jsonify(batch_move_results)
            
            # create batch_move info json dictionary
            batch_move_info = {"parameters": batch_move_params,
                               "files_limit": files_limit,
                               "data_limit": data_limit,
                               "data_effected": data_effected,
                               "files_effected": files_num_effected}
            # enqueue the task to be executed by the worker
            nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                            enqueued_function=consolidate_dirs_edit_task,
                                                            task_kwargs=batch_move_params,
                                                            task_info=batch_move_info,
                                                            timeout=None)
            
            success_message = f"Batch move task enqueued (job id: {nq_results['_id']})\nIt may take some time for the batch operation to complete."
            flask.flash(success_message, 'success')
            return flask.redirect(flask.url_for('archiver.consolidate_dirs'))

        except Exception as e:
            m = "Error processing or executing batch change"
            return web_exception_subroutine(flash_message=m,
                                            thrown_exception=e,
                                            app_obj=flask.current_app)

    return flask.render_template('consolidate_edit.html', title='Consolidate Directories', form=form)
            

@archiver.route("/upload_file", methods=['GET', 'POST'])
@login_required
def upload_file():
    """
    Handles the upload of a single file to the archive by authenticated users.
    This endpoint allows users to upload a file to the server and archive it in a specified directory. Users must be logged in to access this functionality.

    Workflow:
    1. The user navigates to the upload page and is presented with a form to upload a file.
    2. The user selects a file and fills in required metadata such as the destination directory and project number.
    3. Upon form submission, the file and form data are validated.
    4. If validation passes, the file is saved to a temporary location and then moved to the archive destination.
    5. The file's metadata is recorded in the database.
    6. The user is redirected to the home page with a success message.

    Supported Methods:
    - **GET**: Renders the upload file form for the user.
    - **POST**: Processes the uploaded file and form data.

    Form Fields:
    - `project_number` (str, optional): The project number associated with the file.
    - `destination_directory` (str, optional): The filing code directory where the file will be archived.
    - `destination_path` (str, optional): The full path to the destination directory. If provided, `destination_directory` and `project_number` are ignored.
    - `document_date` (Date, optional): The date of the document associated with the file.
    - `new_filename` (str, optional): The new filename for the file. If not provided, the original filename is used.
    - `upload` (File): The file to upload. Required.
    - `notes` (str, optional): Any additional notes or comments about the file.

    Returns:
    - **HTML Template**: Renders the upload form on GET requests.
    - On successful **POST**: Redirects to the home page with a success message.
    - On error: Renders the form again with error messages.

    Notes:
    - Users must be authenticated to access this endpoint.
    - The maximum allowed file size is defined in the application configuration.
    - Only certain file types may be allowed, as defined by the application's settings.
    - The `destination_directory` must be one of the predefined choices in the application configuration.

    Raises:
    - **Unauthorized**: If the user is not authenticated.
    - **ValidationError**: If the form data is invalid or missing required fields.
    - **Exception**: Various exceptions may be raised due to issues like file save errors or database errors.
    """
    # import task function here to avoid circular import
    from archives_application.archiver.archiver_tasks import add_file_to_db_task

    form = archiver_forms.UploadFileForm()
    # set filing code choices from app config
    form.destination_directory.choices = flask.current_app.config.get('DIRECTORY_CHOICES')
    if form.validate_on_submit():
        try:
            archival_filename = form.upload.data.filename
            temp_path = utils.FlaskAppUtils.create_temp_filepath(archival_filename)
            form.upload.data.save(temp_path)

            # raise exception if there is not the required fields filled out in the submitted form.
            if not ((form.project_number.data and form.destination_directory.data) or form.destination_path.data):
                raise Exception(
                    "Missing required fields -- either project_number and Destination_directory or destination_path")

            if form.new_filename.data:
                archival_filename = utils.FilesUtils.cleanse_filename(form.new_filename.data)

            # cleanse the project number value
            project_num = form.project_number.data
            if project_num:
                project_num = utils.sanitize_unicode(project_num.strip())
            
            arch_file = ArchivalFile(current_path=temp_path,
                                     project=project_num,
                                     new_filename=archival_filename,
                                     notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'))
            
            destination_filename = arch_file.assemble_destination_filename()
            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data:
                app_destination_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=form.destination_path.data,
                                                                                 app=flask.current_app)
                arch_file.cached_destination_path = os.path.join(app_destination_path, arch_file.new_filename)
                destination_filename = archival_filename
                
            
            upload_size = os.path.getsize(temp_path)
            archiving_successful, archiving_exception = arch_file.archive_in_destination()

            # If the file was successfully moved to its destination, we will save the data to the database
            if archiving_successful:
                
                # if a location path was provided we do not record the filing code
                recorded_filing_code = arch_file.file_code if not form.destination_path.data else None
                
                # add the archiving event to the database
                archived_file = ArchivedFileModel(destination_path=arch_file.get_destination_path(),
                                                  project_number=arch_file.project_number,
                                                  date_archived=datetime.now(),
                                                  document_date=form.document_date.data,
                                                  destination_directory=arch_file.destination_dir,
                                                  file_code=recorded_filing_code,
                                                  archivist_id=current_user.id,
                                                  file_size=upload_size,
                                                  notes=arch_file.notes,
                                                  filename=destination_filename)
                db.session.add(archived_file)
                db.session.commit()
                
                
                # enqueue the task of adding the file to the database
                add_file_kwargs = {'filepath': arch_file.get_destination_path(), 'archiving': False} #TODO add archiving functionality for if the file is being uploaded by archivist
                nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
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


@archiver.route("/api/upload_file", methods=['POST'])
def upload_file_api():
    """
    Function to handle the upload of a single file to the file server via an API request.
    Requires the user to provide the file to upload in the request. Credentials are provided as URL parameters.
    Available URL parameters:
    - user (str): The email of the user making the request.
    - password (str): The password of the user making the request.
    - filing_code (str): The filing code for the file to be archived.
    - destination (str): The path to the destination directory for the file.
    - project_number (str): The project number for the file to be archived.
    - notes (str): Any notes or comments to be associated with the file.
    """

    # import task function here to avoid circular import
    from archives_application.archiver.archiver_tasks import add_file_to_db_task

    request_authenticated = False
    user_param = utils.FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        password_param = utils.FlaskAppUtils.retrieve_request_param('password', None)
        user = UserModel.query.filter_by(email=user_param).first()

        if user and bcrypt.check_password_hash(user.password, password_param):
            request_authenticated = True
    
    if not request_authenticated:
        return flask.Response("Unauthorized", status=401)
    
    try:
        if 'file' not in flask.request.files:
            return flask.Response("No file in request", status=400)
        
        uploaded_file = flask.request.files['file']
        if uploaded_file.filename == '':
            return flask.Response("No file selected", status=400)

        filing_code = utils.FlaskAppUtils.retrieve_request_param('filing_code')
        destination = utils.FlaskAppUtils.retrieve_request_param('destination')

        # raise exception if there is not the required parameters in the submitted request.
        project_number = utils.FlaskAppUtils.retrieve_request_param('project_number')
        if not (destination or (project_number and filing_code)):
            response_args = flask.request.args.copy()
            response_header_args = flask.request.headers.copy()
            
            # combine request and header args into single dict
            response_args.update(response_header_args)
             
            password_val = utils.FlaskAppUtils.retrieve_request_param('password')
            if password_val:
                response_args['password'] = ''.join(['*' for _ in range(len(password_val))])
            
            response_text = f"""
            Need either a destination or project_number and filing_code to archive the file.
            Request args: {response_args}
            """
            return flask.Response(response_text, status=400)

        # Save file to temporary directory
        filename = utils.FilesUtils.cleanse_filename(uploaded_file.filename)
        temp_path = utils.FlaskAppUtils.create_temp_filepath(filename)
        uploaded_file.save(temp_path)

        if project_number:
            project_number = utils.sanitize_unicode(project_number.strip())

        arch_file = ArchivalFile(current_path = temp_path,
                                 project = project_number,
                                 new_filename = filename,
                                 notes = utils.FlaskAppUtils.retrieve_request_param('notes'),
                                 destination_dir = filing_code,
                                 directory_choices = flask.current_app.config.get('DIRECTORY_CHOICES'),
                                 archives_location = flask.current_app.config.get('ARCHIVES_LOCATION'))
        
        if destination:
            app_destination_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=destination,
                                                                             app=flask.current_app)
            arch_file.cached_destination_path = os.path.join(app_destination_path, arch_file.new_filename)
        
        archiving_successful, archiving_exception = arch_file.archive_in_destination()
        if archiving_successful:
            # enqueue the task of adding the file to the database
            add_file_kwargs = {'filepath': arch_file.get_destination_path(), 'archiving': False} #TODO add archiving functionality for if the file is being uploaded by archivist
            nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                            enqueued_function=add_file_to_db_task,
                                                            task_kwargs=add_file_kwargs,
                                                            timeout=None)
            nq_results = utils.serializable_dict(nq_results)
            return flask.Response(json.dumps(nq_results), status=200)
        
        else:
            raise Exception(
                f"Following error while trying to archive file, {filename}:\nException: {archiving_exception}")
        
    except Exception as e:
        return api_exception_subroutine(response_message="Error processing archiving request:",
                                        thrown_exception=e)


@archiver.route("/inbox_item", methods=['GET', 'POST'])
@utils.FlaskAppUtils.roles_required(['ADMIN', 'ARCHIVIST'])
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
        filename = utils.FileServerUtils.split_path(filepath)[-1]
        file_ext = filename.split(".")[-1]

        if filename.lower() in filenames_to_ignore:
            return True

        if file_ext in file_extensions_to_ignore:
            return True

        return False

    try:
        # Setup User inbox
        # First test for existence of inbox
        inbox_path = flask.current_app.config.get("ARCHIVIST_INBOX_LOCATION")
        if not os.path.exists(inbox_path):
            m = "The archivist inbox directory does not exist."
            return web_exception_subroutine(flash_message=m,
                                            thrown_exception=FileNotFoundError(f"Missing path: {inbox_path}"),
                                            app_obj=flask.current_app)
        
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
        str_filepath_extension = lambda pth: pth.split(".")[-1].lower() # function to get the file extension from a path string
        if str_filepath_extension(arch_file_filename) in ['pdf']:
            try:
                arch_file_preview_image_path = utils.FilesUtils.pdf_preview_image(pdf_path=arch_file_path,
                                                                              image_destination=utils.FlaskAppUtils.create_temp_filepath(''))
                preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.FileServerUtils.split_path(arch_file_preview_image_path)[-1])
                preview_generated = True
            
            except Exception as e:
                m = f"Issue creating preview image for the pdf file, {arch_file_filename}\n(hint: Use 'Upload File' and/or check if the pdf is corrupted.)"
                return web_exception_subroutine(flash_message=m,
                                                thrown_exception=e,
                                                app_obj=flask.current_app)

        # if the file is a tiff, create a preview image that can be rendered in html
        tiff_file_extensions = ['tif', 'tiff']
        if str_filepath_extension(arch_file_filename) in tiff_file_extensions:
            arch_file_preview_image_path = utils.FilesUtils.convert_tiff(tiff_path=arch_file_path,
                                                                         destination_directory=utils.FlaskAppUtils.create_temp_filepath(''),
                                                                         output_file_type='png')
            preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.FileServerUtils.split_path(arch_file_preview_image_path)[-1])

        # If the filetype can be rendered natively in html, copy file as preview of itself if the file is an image
        image_file_extensions = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']
        if str_filepath_extension(arch_file_filename) in image_file_extensions:
            preview_path = utils.FlaskAppUtils.create_temp_filepath(arch_file_filename)
            shutil.copy2(arch_file_path, preview_path)
            preview_image_url = flask.url_for(r"static", filename="temp_files/" + utils.FileServerUtils.split_path(preview_path)[-1])
            preview_generated = True
        
        # If we made a preview image, record the path in the session so it can be removed upon logout
        if preview_generated:
            if not flask.session[current_user.email].get('temporary files'):
                flask.session[current_user.email]['temporary files'] = []
            flask.session[current_user.email]['temporary files'].append(preview_image_url)

        form = archiver_forms.InboxItemForm()
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
                file_can_be_opened_in_browser = str_filepath_extension(arch_file_filename) in ['pdf', 'html']
                flask.session[current_user.email]['inbox_form_data'] = form.data
                return flask.send_file(arch_file_path, as_attachment=not file_can_be_opened_in_browser)


            # raise exception if there is not the required fields filled out in the submitted form.
            if not ((form.project_number.data and form.destination_directory.data) or form.destination_path.data):
                raise Exception(
                    "Missing required fields -- either project_number and destination_directory or just a destination_path")

            upload_size = os.path.getsize(arch_file_path)
            archival_filename = arch_file_filename
            if form.new_filename.data:
                archival_filename = utils.FilesUtils.cleanse_filename(form.new_filename.data)
            
            # make sure the archival filename includes the file extension
            file_ext = arch_file_filename.split(".")[-1]
            if not archival_filename.lower().endswith(file_ext.lower()):
                archival_filename = archival_filename + "." + file_ext    

            # strip the project number of any whitespace (in case an archivist adds a space after the project number)
            project_num = form.project_number.data
            if project_num:
                project_num = utils.sanitize_unicode(project_num.strip())
                
            arch_file = ArchivalFile(current_path=arch_file_path, project=project_num,
                                     new_filename=archival_filename, notes=form.notes.data,
                                     destination_dir=form.destination_directory.data,
                                     archives_location=flask.current_app.config.get('ARCHIVES_LOCATION'),
                                     directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                     destination_path=form.destination_path.data)
            
            destination_filename = arch_file.assemble_destination_filename()
            # If a user enters a path to destination directory instead of File code and project number...
            if form.destination_path.data:
                app_destination_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=form.destination_path.data,
                                                                                 app=flask.current_app)
                arch_file.cached_destination_path = os.path.join(app_destination_path, arch_file.new_filename)
                arch_file.destination_dir = None
                destination_filename = archival_filename

            # archive the file in the destination and attempt to record the archival in the database    
            archiving_successful, archiving_exception = arch_file.archive_in_destination()
            
            # if the file was successfully archived, add the archiving event and the file to the application database
            if archiving_successful:
                try:
                    # if a location path was provided we do not record the filing code
                    recorded_filing_code = arch_file.file_code if not form.destination_path.data else None

                    # add the archiving event to the database
                    archived_file = ArchivedFileModel(destination_path=arch_file.get_destination_path(),
                                                      archivist_id=current_user.id,
                                                      project_number=arch_file.project_number,
                                                      date_archived=datetime.now(),
                                                      document_date=form.document_date.data,
                                                      destination_directory=arch_file.destination_dir,
                                                      file_code=recorded_filing_code,
                                                      file_size=upload_size, notes=arch_file.notes,
                                                      filename=destination_filename)
                    db.session.add(archived_file)
                    db.session.commit()

                    # add the file to the database
                    add_file_kwargs = {'filepath': arch_file.get_destination_path(), 'archiving': True}
                    nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
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


def cleanse_locations_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # New df is only the columns we want, 'file_server_directories' and 'filename'
    df = df[['file_server_directories', 'filename']]
    # New row  'filepath' which joins the directories and the filename
    df['filepath'] = df.apply(lambda row: (row['file_server_directories'] + "/" + row['filename']), axis=1)
    return df[['filepath']]


@archiver.route("/api/archived_or_not", methods=['POST'])
def archived_or_not_api():
    """
    API endpoint to determine if the uploaded file via the form exists in the app database.
    
    This function requires a POST request with a file part and 'user' and
    'password' as query parameters for authentication. It processes the uploaded file,
    calculates its hash, and checks against the database to see if the file with the
    same hash is already archived. If authenticated and found, it returns the locations
    of the archived file in JSON format.

    Returns:
        flask.Response: A JSON response containing the locations if the file is found,
                        a "No file found" with 404 status if the file is not in the database,
                        "Unauthorized" with 401 status if authentication fails,
                        or "No file in request"/"No file selected" with 400 status if the file is missing.
    Raises:
        Exception: An exception is raised when an error occurs during file processing or database querying.
    """
    request_authenticated = False
    user_param = utils.FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        password_param = utils.FlaskAppUtils.retrieve_request_param('password')
        user = UserModel.query.filter_by(email=user_param).first()

        if user and bcrypt.check_password_hash(user.password, password_param):
            request_authenticated = True
    
    if not request_authenticated:
        return flask.Response("Unauthorized", status=401)
    
    try:
        if 'file' not in flask.request.files:
            return flask.Response("No file in request", status=400)
        
        uploaded_file = flask.request.files['file']
        if uploaded_file.filename == '':
            return flask.Response("No file selected", status=400)

        # Save file to temporary directory
        filename = uploaded_file.filename
        temp_path = utils.FlaskAppUtils.create_temp_filepath(filename)
        uploaded_file.save(temp_path)
        
        file_hash = utils.FilesUtils.get_hash(filepath=temp_path)
        os.remove(temp_path)
        matching_file = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
        if not matching_file:
            return flask.Response("File not found in database.", status=404)
        
        locations_query = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == matching_file.id)
        locations_df = utils.FlaskAppUtils.db_query_to_df(locations_query)
        if locations_df.empty:
            return flask.Response("No locations found in database for file.", status=404)
        
        locations_df = cleanse_locations_dataframe(locations_df)
        return flask.jsonify(locations_df['filepath'].to_list())

    except Exception as e:
        return api_exception_subroutine(response_message="Error processing request: ",
                                        thrown_exception=e)


@archiver.route("/archived_or_not", methods=['GET', 'POST'])
def archived_or_not():
    """
    Web endpoint for checking if a file is archived, intended for form submissions.

    GET requests render an upload form where users can submit a file to check if it's archived.
    POST requests take the submitted file, save it temporarily, calculate its hash, and query
    the database to check for its existence. If the file is found, an HTML table with file
    locations is returned. Otherwise, a flash message is displayed and the user is redirected.

    Returns:
        flask.Response: A rendered template of the upload form on GET,
                        a rendered template with file locations on successful POST,
                        or a redirect with a flash message if the file is not found.
    Raises:
        Exception: An exception is raised when an error occurs during file processing,
                    querying the database, or if the file is found in the database but
                    no locations are associated with it.
    """
    form = archiver_forms.ArchivedOrNotForm()
    if form.validate_on_submit():
        try:
            # Save file to temporary directory
            filename = form.upload.data.filename
            temp_path = utils.FlaskAppUtils.create_temp_filepath(filename)
            form.upload.data.save(temp_path)
            file_hash = utils.FilesUtils.get_hash(filepath=temp_path)

            matching_file = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
            if not matching_file:
                flask.flash(f"No file found with hash {file_hash}", 'info')
                return flask.redirect(flask.url_for('archiver.archived_or_not'))
            
            # Create html table of all locations that match the hash
            locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == matching_file.id)
            locations_df = utils.FlaskAppUtils.db_query_to_df(locations)
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


@archiver.route("/scrape_files", methods=['GET', 'POST'])
@archiver.route("/api/scrape_files", methods=['GET', 'POST'])
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
    user_param = utils.FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        password_param = utils.FlaskAppUtils.retrieve_request_param('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user \
            and bcrypt.check_password_hash(user.password, password_param) \
            and utils.FlaskAppUtils.has_admin_role(user):
            request_is_authenticated = True

    elif current_user:
        if current_user.is_authenticated \
            and utils.FlaskAppUtils.has_admin_role(current_user):
            request_is_authenticated = True

    # If the request is authenticated, we can proceed to enqueue the task.
    if request_is_authenticated:
        try:
            # Retrieve scrape parameters
            scrape_location = retrieve_location_to_start_scraping()
            scrape_time = 8
            file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get("ARCHIVES_LOCATION")))
            if utils.FlaskAppUtils.retrieve_request_param('scrape_time'):
                scrape_time = int(utils.FlaskAppUtils.retrieve_request_param('scrape_time'))
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
            
            # set the result_ttl to 12 hours (43200 seconds) so that the results are not deleted from Redis
            nq_call_kwargs = {'result_ttl': 43200}
            nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                            enqueued_function=scrape_file_data_task,
                                                            task_kwargs=scrape_params,
                                                            enqueue_call_kwargs=nq_call_kwargs,
                                                            timeout=scrape_time.seconds + 60)
            
            nq_results = {"enqueueing_results": utils.serializable_dict(nq_results),
                          "scrape_task_params": utils.serializable_dict(scrape_params)}
            return flask.Response(json.dumps(nq_results), status=200)

        except Exception as e:
            mssg = "Error enqueuing task"
            if e.__class__.__name__ == "ConnectionError":
                mssg = "Error connecting to Redis. Is Redis running?"
            return api_exception_subroutine(response_message=mssg, thrown_exception=e)   
        
    return flask.Response("Unauthorized", status=401)


@archiver.route("/test/scrape_files", methods=['GET', 'POST'])
@utils.FlaskAppUtils.roles_required(['ADMIN'])
def test_scrape_files():
    """
    Endpoint for testing archiver_tasks.scrape_file_data function in development.
    """
    # import task here to avoid circular import
    from archives_application.archiver.archiver_tasks import scrape_file_data_task

    # Retrieve scrape parameters
    scrape_location = retrieve_location_to_start_scraping()
    scrape_time = 8
    file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get("ARCHIVES_LOCATION")))
    if utils.FlaskAppUtils.retrieve_request_param('scrape_time'):
        scrape_time = int(utils.FlaskAppUtils.retrieve_request_param('scrape_time'))
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
    user_param = utils.FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        password_param = utils.FlaskAppUtils.retrieve_request_param('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user \
            and bcrypt.check_password_hash(user.password, password_param) \
            and utils.FlaskAppUtils.has_admin_role(user):
            request_is_authenticated = True

    elif current_user:
        if current_user.is_authenticated \
            and utils.FlaskAppUtils.has_admin_role(current_user):
            request_is_authenticated = True
    
    if request_is_authenticated:
        try:
            confirming_time = 10
            if utils.FlaskAppUtils.retrieve_request_param('confirming_time'):
                confirming_time = int(utils.FlaskAppUtils.retrieve_request_param('confirming_time'))
            
            confirming_time = timedelta(minutes=confirming_time)
            confirm_params = {"archive_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
                              "confirming_time": confirming_time}
            nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                            enqueued_function=confirm_file_locations_task,
                                                            task_kwargs=confirm_params,
                                                            timeout=confirming_time.seconds + 600)
            
            # prepare task enqueueing info for JSON serialization
            nq_results = utils.serializable_dict(nq_results)
            confirm_params['confirming_time'] = str(confirm_params['confirming_time'])
            confirm_dict = {"enqueueing_results": nq_results, "confirmation_task_params": confirm_params}
            return flask.Response(json.dumps(confirm_dict), status=200)

        except Exception as e:
            mssg = "Error enqueuing task"
            if e.__class__.__name__ == "ConnectionError":
                mssg = "Error connecting to Redis. Is Redis running?"
            return api_exception_subroutine(response_message=mssg, thrown_exception=e)
    
    return flask.Response("Unauthorized", status=401)


@archiver.route("/test/confirm_files", methods=['GET', 'POST'])
@utils.FlaskAppUtils.roles_required(['ADMIN'])
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
    Endpoint for searching the file locations in the database for files that match the search term.
    """

    form = archiver_forms.FileSearchForm()
    csv_filename_prefix = "search_results_"
    timestamp_format = r'%Y%m%d%H%M%S'
    html_table_row_limit = 1000
    
    # if the request includes a timestamp for a previous search results, then we will return the spreadsheet of the search results.
    # If there is not a corresponding file, then we will raise an error.
    if utils.FlaskAppUtils.retrieve_request_param('timestamp'):
        try:
            timestamp = utils.FlaskAppUtils.retrieve_request_param('timestamp')
            csv_filepath = utils.FlaskAppUtils.create_temp_filepath(filename=f'{csv_filename_prefix}{timestamp}.csv',
                                                                     unique_filepath=False)
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
            user_archives_location = flask.current_app.config.get('USER_ARCHIVES_LOCATION')
            search_query = None
            search_term = str(form.search_term.data)
            search_full_filepath = not bool(form.filename_only.data)
            search_query = FileLocationModel.filepath_search_query(query_str=search_term, full_path=search_full_filepath)
            if form.search_location.data:
                search_location = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=form.search_location.data,
                                                                            app=flask.current_app)
                search_location_list = utils.FileServerUtils.split_path(search_location)
                mount_path_index = len(utils.FileServerUtils.split_path(archives_location))
                search_location_search_term = os.path.join(*search_location_list[mount_path_index:])
                search_query = search_query.filter(FileLocationModel.file_server_directories.like(f"%{search_location_search_term}%"))

            search_df = utils.FlaskAppUtils.db_query_to_df(search_query)
            if search_df.empty:
                flask.flash(f"No files found matching search term: {search_term}", 'warning')
                return flask.redirect(flask.url_for('archiver.file_search'))
            
            user_usable_path = lambda row: utils.FileServerUtils.user_path_from_db_data(file_server_directories=row['file_server_directories'],
                                                                                        user_archives_location=user_archives_location)
            search_df['Location'] = search_df.apply(user_usable_path, axis=1)
            cols_to_remove = ['id', 'file_id', 'file_server_directories', 'existence_confirmed', 'hash_confirmed']
            search_df.drop(columns=cols_to_remove, inplace=True)
            search_df.rename(columns={'filename': 'Filename'}, inplace=True)
            timestamp = datetime.now().strftime(r'%Y%m%d%H%M%S')
            too_many_results = len(search_df) > html_table_row_limit
            csv_filepath = utils.FlaskAppUtils.create_temp_filepath(filename=f'{csv_filename_prefix}{timestamp}.csv')
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
    
    try:
        testing = False

        # determine if the request is for testing the associated worker task
        # if the testing worker task, the task will be executed on this process and
        # not enqueued to be executed by the worker
        if utils.FlaskAppUtils.retrieve_request_param('test') \
            and utils.FlaskAppUtils.retrieve_request_param('test').lower() == 'true' \
            and utils.FlaskAppUtils.has_admin_role(current_user):
            testing = True

        form = archiver_forms.ScrapeLocationForm()
        if form.validate_on_submit():
            search_location = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=form.scrape_location.data,
                                                                        app=flask.current_app)
            
            scrape_params = {'scrape_location': search_location,
                            'recursively': form.recursive.data,
                            'confirm_data': True}
            scrape_info = {'paprameters': scrape_params}
            
            # if the request is for testing the worker task, we will execute the task on this process
            if testing:
                test_job_id = f"{scrape_location_files_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
                new_task_record = WorkerTaskModel(task_id=test_job_id,
                                                time_enqueued=str(datetime.now()),
                                                origin='test',
                                                function_name=scrape_location_files_task.__name__,
                                                status= "queued")
                db.session.add(new_task_record)
                db.session.commit()
                
                scrape_params['queue_id'] = test_job_id
                scrape_results = scrape_location_files_task(**scrape_params)
                scrape_info['results'] = scrape_results
                scrape_info['task_id'] = test_job_id
                return flask.jsonify(scrape_info)
            
            
            nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                            enqueued_function=scrape_location_files_task,
                                                            task_kwargs=scrape_params,
                                                            task_info=scrape_info,
                                                            timeout=3600)
            id = nq_results.get("_id")
            function_call = nq_results.get("description")
            m = f"Scraping task has been successfully enqueued. Function Enqueued: {function_call}"
            flask.flash(m, 'success')
            testing_params = {} if not testing else {'test': str(bool(testing))}
            return flask.redirect(flask.url_for('archiver.scrape_location', values=testing_params))
        
        return flask.render_template('scrape_location.html', form=form)
    
    except Exception as e:
        return web_exception_subroutine(flash_message="Error scraping location: ",
                                        thrown_exception=e,
                                        app_obj=flask.current_app)