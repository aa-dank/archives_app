import flask
import io
import json
import logging
import os
import subprocess
import traceback
import archives_application.app_config as app_config
import pandas as pd
from flask_login import current_user
from archives_application.main import forms
from archives_application.utils import html_table_from_df, FlaskAppUtils, RQTaskUtils
from archives_application import db, bcrypt
from archives_application.models import *

# This dictionary is used to determine how long to keep task records in the database
TASK_RECORD_LIFESPANS = {'add_file_to_db_task': 90,
                         'scrape_file_data_task': 365,
                         'confirm_file_locations_task': 365,
                         'add_deletion_to_db_task':180,
                         'add_move_to_db_task': 180,
                         'add_renaming_to_db_task': 180,
                         'db_backup_clean_up_task': 90,
                         'task_records_clean_up_task': 90,
                         'temp_file_clean_up_task': 90,
                         'db_backup_task': 180,
                         'fmp_caan_project_reconciliation_task': 365,
                         'confirm_project_locations_task': 365,
                         'consolidation_target_removal_task': 365,
                         'consolidate_dirs_edit_task': 365,
                         'batch_move_edits_task': 365,
                         'batch_process_inbox_task': 365}

main = flask.Blueprint('main', __name__)



def web_exception_subroutine(flash_message, thrown_exception, app_obj):
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


@main.route("/")
@main.route("/home")
def home():
    """
    Renders the home page.

    Returns:
        Response: The rendered 'home.html' template.
    """
    return flask.render_template('home.html')


@main.route("/about")
def about():
    """Placeholder for an about page"""
    return flask.render_template('about.html', title='About')


@main.route("/admin/db_backup", methods=['GET', 'POST'])
def backup_database():
    """
    Endpoint for backing up the database.

    This endpoint can be used for manual backups by navigating to the URL as an admin user
    or by providing credentials via query parameters for scheduled processes.
    Request parameters can be sent in either th url or request headers.

    Query Parameters:
        user (str): The username for authentication.
        password (str): The password for authentication.

    Returns:
        Response: A Flask Response object with the result of the backup operation,
        or an error message with the appropriate HTTP status code.
    """
    
    # import task here to avoid circular import
    from archives_application.main.main_tasks import db_backup_task

    
    try:
        
        # first determine if the request is being made by an admin user
        authenticated_to_make_request = False
        user_param = FlaskAppUtils.retrieve_request_param('user', None)
        if user_param:
            password_param = FlaskAppUtils.retrieve_request_param('password')
            user = UserModel.query.filter_by(email=user_param).first()

            # If there is a matching user to the request parameter, the password matches and that account has admin role...
            if user and bcrypt.check_password_hash(user.password, password_param) and FlaskAppUtils.has_admin_role(user):
                authenticated_to_make_request = True

        elif current_user:
            if current_user.is_authenticated and FlaskAppUtils.has_admin_role(current_user):
                authenticated_to_make_request = True

        if authenticated_to_make_request:
            nk_result = RQTaskUtils.enqueue_new_task(db=db,
                                                     enqueued_function=db_backup_task,
                                                     timeout=60)
            job_id = nk_result["task_id"]
            if FlaskAppUtils.retrieve_request_param('user'):
                return flask.Response(f"Database Back-up Task Enqueued. Job ID: {job_id}", status=200)
            
            flask.flash("Database Back-up Task Enqueued.", 'info')
            return flask.redirect(flask.url_for('main.home'))

        return flask.Response("Unauthorized", status=401)

    except Exception as e:
        return FlaskAppUtils.api_exception_subroutine("Database Backup Failed", str(e))


@main.route("/admin/maintenance", methods=['GET', 'POST'])
def app_maintenance():
    """Performs routine maintenance tasks on the application and database.

    This endpoint enqueues tasks like cleaning up temporary files,
    removing old task records, and deleting outdated database backups.
    A helper process (`AppCustodian`) defines and manages these tasks.
    Meant to be used for regular, scheduled application maintenance.
    Request parameters can be sent in either th url or request headers.

    Query Parameters:
        user (str): Username for authentication.
        password (str): Password for authentication.

    Returns:
        Response: A JSON response with the results of the maintenance tasks,
        or an error message with the appropriate HTTP status code.
    """

    # import task here to avoid circular import
    from archives_application.main.main_tasks import AppCustodian
    
    def str_dictionary_values(some_dict):
        """
        This function takes a dictionary and converts all values to strings.
        """
        for key, value in some_dict.items():
            if isinstance(value, dict):
                str_dictionary_values(value)
            elif isinstance(value, list):
                some_dict[key] = [str(item) for item in value]
            else:
                some_dict[key] = str(value)
        return some_dict
    
    custodian = AppCustodian(temp_file_lifespan=3,
                             task_records_lifespan_map=TASK_RECORD_LIFESPANS,
                             db_backup_file_lifespan=2)
    user_param = FlaskAppUtils.retrieve_request_param('user', None)
    if user_param:
        password_param = FlaskAppUtils.retrieve_request_param('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and FlaskAppUtils.has_admin_role(user):
            task_enqueueing_result = custodian.enqueue_maintenance_tasks(db=db)
            task_enqueueing_result = str_dictionary_values(task_enqueueing_result)
            return flask.Response(response=json.dumps(task_enqueueing_result),
                                  status=200,
                                  mimetype="application/json")
        
    elif current_user:
        if current_user.is_authenticated and FlaskAppUtils.has_admin_role(current_user):
            task_enqueueing_result = custodian.enqueue_maintenance_tasks(db=db)
            task_enqueueing_result = str_dictionary_values(task_enqueueing_result)
            return flask.Response(response=json.dumps(task_enqueueing_result),
                                  status=200,
                                  mimetype="application/json")
    
    no_user_msg = {"error":"You must be logged in as an admin to perform maintenance."}
    return flask.Response(response=flask.jsonify(no_user_msg),
                          status=401,
                          mimetype="application/json")


@main.route("/admin/config", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def change_config_settings():
    """Allows admin users to change application configuration settings within the app.

    This endpoint provides a form to modify the configuration file. After submitting
    new settings, a helper process restarts the application to apply the changes.

    Returns:
        Response: Renders the configuration form or redirects with a success/error message.
    """

    # import task here to avoid circular import
    from archives_application.main.main_tasks import restart_app_task

    def restart_app_workers():
        """
        Function to restart the application workers.
        """
        cmd = flask.current_app.config.get("APP_WORKERS_RESTART_COMMAND")
        if not cmd:
            raise ValueError("APP_WORKERS_RESTART_COMMAND not found in app config.")
        cmd_result = subprocess.run(cmd,
                                    shell=True,
                                    stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, # This is necessary to capture the output of the command
                                    stderr=subprocess.PIPE,
                                    text=True)
        return cmd_result
    
    def clean_new_val_list_entry(list_val):
        """
        Cleans and normalizes a string entry that will become part of a list.
        
        This function removes unwanted characters from the beginning and end of a string
        that was extracted from user input intended to be converted into a list. It handles
        common formatting artifacts like quotes and newline characters that may
        appear when users enter list data in various formats.
        
        Args:
            list_val (str): The string value to be cleaned. Typically represents one
                        item from a comma-separated or newline-separated list.
        
        Returns:
            str: The cleaned string with leading/trailing unwanted characters removed.
        
        Example:
            >>> clean_new_val_list_entry('"A - General"')
            'A - General'
            >>> clean_new_val_list_entry("'B - Administrative Reviews'\\n")
            'B - Administrative Reviews'
            >>> clean_new_val_list_entry('C - Consultants')
            'C - Consultants'
        
        Note:
            This function is specifically designed for processing configuration list
            entries like DIRECTORY_CHOICES where users may input data with various
            formatting styles (quoted strings, trailing newlines, etc.).
            Commas are preserved as they may be part of the actual content.
        """
        # Remove quotes and newlines, but preserve commas as they're part of the content
        chars_to_remove = ['"', "'", "\n", "\r"]
        list_val = list_val.strip()
        
        # Remove leading unwanted characters
        while list_val and list_val[0] in chars_to_remove:
            list_val = list_val[1:]
        
        # Remove trailing unwanted characters  
        while list_val and list_val[-1] in chars_to_remove:
            list_val = list_val[:-1]
        
        return list_val.strip()

    config_dict = {}
    config_filepath = flask.current_app.config.get('CONFIG_JSON_PATH')
    form = None
    try:
        # open the config file and create a form from it
        with open(config_filepath) as config_json_file:
            config_dict = json.load(config_json_file)
        dynamic_form_class = forms.form_factory(fields_dict=config_dict, form_class_name="ConfigChange")
        form = dynamic_form_class()
    except Exception as e:
        m = 'An error occurred opening the config file and creating a form from it:'
        return web_exception_subroutine(flash_message=m, thrown_exception=e, app_obj=flask.current_app)

    if form.validate_on_submit():
        try:
            # for each key in the config, we replace it with the value from the form if a value was entered in the form
            for k in list(config_dict.keys()):
                if getattr(form, k).data:
                    new_val = getattr(form, k).data
                    # if the value for this setting is a list, process input string into a list
                    if type(config_dict[k]['VALUE']) == type([]):
                        
                        #remove leading and trailing brackets if they exist
                        new_val = new_val.strip()
                        new_val = new_val[1:] if new_val[0] == '[' else new_val
                        new_val = new_val[:-1] if new_val[-1] == ']' else new_val

                        # Check if input contains newlines - if so, split on newlines instead of commas
                        if '\n' in new_val:
                            # split on newlines
                            new_val = [clean_new_val_list_entry(x) for x in new_val.split("\n") if x != '']
                        else:
                            # may need to remove leading and trailing quotes from each element
                            new_val = [clean_new_val_list_entry(x) for x in new_val.split(",") if x != '']
                    
                    config_dict[k]['VALUE'] = new_val

            with open(config_filepath, 'w') as config_file:
                json.dump(config_dict, config_file)
            
            # restart the application workers
            worker_restart_results = restart_app_workers()
            # if the worker restart command failed, raise an error
            if worker_restart_results.returncode != 0:
                raise ValueError(f"Error restarting application workers: {worker_restart_results.stderr}")

            # restart the application
            restart_params = {'delay': 15}
            app_restart_nq_result = RQTaskUtils.enqueue_new_task(db=db,
                                                                 enqueued_function=restart_app_task,
                                                                 task_kwargs=restart_params)
            
            flask.flash("Values entered were stored in the config file. Application restart is immenent.", 'success')
            return flask.redirect(flask.url_for('main.home'))

        except Exception as e:
            return web_exception_subroutine(flash_message="Error processing form responses into json config file: ",
                                            thrown_exception=e,
                                            app_obj=flask.current_app)

    return flask.render_template('change_config_settings.html', title='Change Config File', form=form, settings_dict=config_dict)


@main.route("/test/logging", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def test_logging():
    """Generates test log messages at various levels for debugging purposes.

    This endpoint logs messages at DEBUG, INFO, WARNING, ERROR, and CRITICAL levels.
    It can be useful for verifying that the logging configuration is working as expected.

    Examples:
        - Use this endpoint to ensure that log messages appear in log files or consoles.
        - Test different log handlers or troubleshoot logging issues.

    Returns:
        Response: Redirects to the home page after logging the messages.
    """
    flask.current_app.logger.debug("I'm a test DEBUG message")
    flask.current_app.logger.info("I'm an test INFO message")
    flask.current_app.logger.warning("I'm a test WARNING message")
    flask.current_app.logger.error("I'm a test ERROR message")
    flask.current_app.logger.critical("I'm a test CRITICAL message")
    flask.flash("A series of test logging events have been logged.", 'info')
    return flask.redirect(flask.url_for('main.home'))


@main.route("/test/database_info")
@FlaskAppUtils.roles_required(['ADMIN'])
def get_db_info():
    """Displays the current database URI, connection pool status, and tests connectivity.
    
    This endpoint combines database information and connectivity testing in one place:
    - Shows the database URI for debugging and verification
    - Displays connection pool status to identify connection management issues
    - Tests connectivity by performing a simple SELECT query
    
    Returns:
        Response: A JSON response containing the database URL, pool status, and connection test results.
    """
    # Get database URI and pool status
    status = db.engine.pool.status()
    info = {
        "database_url": flask.current_app.config.get("SQLALCHEMY_DATABASE_URI"),
        "status": status
    }
    
    # Test database connection
    try:
        # Run a simple SELECT 1 query via SQLAlchemy's engine
        result = db.engine.execute("SELECT 1").scalar()
        
        if result == 1:
            info["connection_test"] = {
                "status": "success",
                "message": "Database connection successful. SELECT 1 returned 1."
            }
        else:
            info["connection_test"] = {
                "status": "error",
                "message": f"Unexpected result from SELECT 1: {result}"
            }
            
    except Exception as e:
        info["connection_test"] = {
            "status": "error",
            "message": str(e)
        }
        
    return flask.jsonify(info)


@main.route("/test/see_config")
@FlaskAppUtils.roles_required(['ADMIN'])
def get_app_config():
    """Endpoint function to see the current configuration of the runnning application.
    Useful for debugging and checking the current state of the application, sanity checks, etc.
    
    Returns:
        Response: A JSON response containing various configuration parameters of the application.
    """
    info = {
        "database_url": flask.current_app.config.get("SQLALCHEMY_DATABASE_URI"),
        "archives_location": flask.current_app.config.get("ARCHIVES_LOCATION"),
        "database_backup_location": flask.current_app.config.get("DATABASE_BACKUP_LOCATION"),
        "archivist_inbox_location": flask.current_app.config.get("ARCHIVIST_INBOX_LOCATION"),
        "directory_choices": flask.current_app.config.get("DIRECTORY_CHOICES"),
        "server_change_files_limit": flask.current_app.config.get("SERVER_CHANGE_FILES_LIMIT"),
        "server_change_data_limit": flask.current_app.config.get("SERVER_CHANGE_DATA_LIMIT"),
        "redis_url": flask.current_app.config.get("REDIS_URL"),
        "user_archives_location": flask.current_app.config.get("USER_ARCHIVES_LOCATION"),
        "filemaker_host_location": flask.current_app.config.get("FILEMAKER_HOST_LOCATION"),
        "filemaker_user": flask.current_app.config.get("FILEMAKER_USER"),
        "filemaker_password": flask.current_app.config.get("FILEMAKER_PASSWORD"),
        "filemaker_database": flask.current_app.config.get("FILEMAKER_DATABASE_NAME"),
        "app_workers_restart_command": flask.current_app.config.get("APP_WORKERS_RESTART_COMMAND"),
        "app_restart_command": flask.current_app.config.get("APP_RESTART_COMMAND"),

    }
    return flask.jsonify(info)

@main.route("/test/rq", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def test_rq_connection():
    """Tests the connection to the RQ (Redis Queue) task queue.

    Returns:
        Response: A JSON response indicating the status of the RQ connection.
    """
    try:
        redis_client = flask.current_app.q.connection
        redis_client.ping()
        redis_info = redis_client.info()
        return redis_info
    
    except Exception as e:
        m = "Error connecting to the redis queue: "
        return web_exception_subroutine(flash_message=m, thrown_exception=e, app_obj=flask.current_app)
    

@main.route("/test/file_server_access")
@FlaskAppUtils.roles_required(['ADMIN'])
def test_file_server_access():
    """
    Tests file server access permissions for various locations.

    This function checks read, write, edit, and delete permissions for
    the following locations:
    - DATABASE_BACKUP_LOCATION: write, delete
    - ARCHIVES_LOCATION: read, write, edit, delete (full permissions)
    - ARCHIVIST_INBOX_LOCATION: read, write, edit, delete (full permissions)

    Returns:
        A JSON response indicating success or failure for each permission test.
    """
    import os
    import tempfile
    result = {}

    # Get the paths from application config
    db_backup_location = flask.current_app.config.get('DATABASE_BACKUP_LOCATION')
    archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
    inbox_location = flask.current_app.config.get('ARCHIVIST_INBOX_LOCATION')

    # Test DATABASE_BACKUP_LOCATION (write, delete)
    db_backup_results = {'write': False, 'delete': False}
    temp_name = None
    try:
        # Write test
        with tempfile.NamedTemporaryFile(dir=db_backup_location, delete=False) as tmp_file:
            tmp_file.write(b"Test")
            temp_name = tmp_file.name
        db_backup_results['write'] = True

        # Delete test
        if temp_name:
            os.remove(temp_name)
            db_backup_results['delete'] = True
    except Exception as e:
        flask.current_app.logger.error(f"DATABASE_BACKUP_LOCATION permission error: {e}")
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

    result['DATABASE_BACKUP_LOCATION'] = db_backup_results

    # Test ARCHIVES_LOCATION (full permissions)
    archives_results = {'read': False, 'write': False, 'edit': False, 'delete': False}
    temp_name = None
    try:
        # Read test
        if os.access(archives_location, os.R_OK):
            archives_results['read'] = True

        # Write test
        with tempfile.NamedTemporaryFile(dir=archives_location, delete=False) as tmp_file:
            tmp_file.write(b"Test")
            temp_name = tmp_file.name
        archives_results['write'] = True

        # Edit test
        try:
            with open(temp_name, 'a') as f:
                f.write("Edit")
            archives_results['edit'] = True
        except Exception as e:
            flask.current_app.logger.error(f"ARCHIVES_LOCATION edit permission error: {e}")

        # Delete test
        if temp_name:
            os.remove(temp_name)
            archives_results['delete'] = True
    except Exception as e:
        flask.current_app.logger.error(f"ARCHIVES_LOCATION permission error: {e}")
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

    result['ARCHIVES_LOCATION'] = archives_results

    # Test ARCHIVIST_INBOX_LOCATION (full permissions)
    inbox_results = {'read': False, 'write': False, 'edit': False, 'delete': False}
    temp_name = None
    try:
        # Read test
        if os.access(inbox_location, os.R_OK):
            inbox_results['read'] = True

        # Write test
        with tempfile.NamedTemporaryFile(dir=inbox_location, delete=False) as tmp_file:
            tmp_file.write(b"Test")
            temp_name = tmp_file.name
        inbox_results['write'] = True

        # Edit test
        try:
            with open(temp_name, 'a') as f:
                f.write("Edit")
            inbox_results['edit'] = True
        except Exception as e:
            flask.current_app.logger.error(f"ARCHIVIST_INBOX_LOCATION edit permission error: {e}")

        # Delete test
        if temp_name:
            os.remove(temp_name)
            inbox_results['delete'] = True
    except Exception as e:
        flask.current_app.logger.error(f"ARCHIVIST_INBOX_LOCATION permission error: {e}")
        if temp_name and os.path.exists(temp_name):
            os.remove(temp_name)

    result['ARCHIVIST_INBOX_LOCATION'] = inbox_results

    return flask.jsonify(result)

@main.route("/admin/sql_logging", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def toggle_sql_logging():
    """The purpose of this endpoint is to toggle the logging of sql statements to the console.
    This is useful for debugging, but should not be left on in production.

    Returns:
        Response: A JSON response indicating the new logging status and log file location.
    """
    
    # "If set to True SQLAlchemy will log all the statements issued to stderr which can be useful for debugging"
    # https://flask-sqlalchemy.palletsprojects.com/en/2.x/config/
    current_echo = flask.current_app.config.get("SQLALCHEMY_ECHO", False)
    log_path = os.path.join(flask.current_app.config.get("DATABASE_BACKUP_LOCATION"),
                            flask.current_app.config.get("SQLALCHEMY_LOG_FILE"))
    flask.current_app.config['SQLALCHEMY_ECHO'] = not current_echo
    db_logger = logging.getLogger('sqlalchemy.engine')
    if not current_echo:
        db_logger = app_config.setup_sql_logging(log_filepath=log_path)
        db_logger.disabled = False
    else:
        db_logger.handlers.clear()
        db_logger.disabled = True
    return flask.jsonify(**{"sql logging":flask.current_app.config['SQLALCHEMY_ECHO'], "log location":log_path})


@main.route("/endpoints_index")
def endpoints_index():
    """Displays all the endpoints of the application or returns an Excel file.

    Returns:
        Response: Renders the 'endpoints_index.html' template with the endpoints table,
                  or returns an Excel file if 'spreadsheet' parameter is set to 'True'.
    """
    try:
        # Get all the endpoints of the application
        data = []
        for rule in sorted(flask.current_app.url_map.iter_rules(), key=lambda r: r.rule):
            methods = ', '.join(sorted(rule.methods - {'HEAD', 'OPTIONS'}))
            endpoint = rule.endpoint
            view_func = flask.current_app.view_functions.get(endpoint)
            doc = (view_func.__doc__ or 'No documentation available.').strip()
            data.append({
                'URL': f'<a href="{rule.rule}">{rule.rule}</a>',
                'Methods': methods,
                'Endpoint': endpoint,
                'Docstring': doc
            })

        df = pd.DataFrame(data)

        # Check if 'spreadsheet' parameter is set to 'True'
        spreadsheet_param = flask.request.args.get('spreadsheet', 'False')
        if spreadsheet_param.lower() == 'true':
            # Return Excel file
            output = io.BytesIO()
            try:
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Endpoints')

                    # Adjust column widths
                    workbook = writer.book
                    worksheet = writer.sheets['Endpoints']

                    for column_cells in worksheet.columns:
                        max_length = 0
                        column = column_cells[0].column_letter  # Get the column name
                        for cell in column_cells:
                            try:
                                if cell.value:
                                    cell_length = len(str(cell.value))
                                    if cell_length > max_length:
                                        max_length = cell_length
                            except Exception:
                                pass
                        adjusted_width = (max_length + 2)
                        worksheet.column_dimensions[column].width = adjusted_width

                # Seek to the beginning of the stream
                output.seek(0)

                # Send the Excel file as a response
                return flask.send_file(
                    output,
                    download_name='endpoints.xlsx',  # Use 'attachment_filename' if using Flask < 2.0
                    as_attachment=True,
                    mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
            except Exception as e:
                # Handle exceptions during Excel file generation
                flash_message = 'An error occurred while generating the Excel file'
                return web_exception_subroutine(flash_message, e, flask.current_app)
        else:
            # Continue with original behavior
            try:
                column_widths = {
                    'URL': '10%',
                    'Methods': '10%',
                    'Endpoint': '15%',
                    'Docstring': '65%'
                }
                df_html = html_table_from_df(
                    df,
                    column_widths=column_widths,
                    html_columns=['Docstring', 'URL']
                )
                return flask.render_template(
                    'endpoints_index.html',
                    title='Endpoints Index',
                    endpoints_html_table=df_html,
                    hide_sidebar=True
                )
            except Exception as e:
                # Handle exceptions during HTML rendering
                flash_message = 'An error occurred while rendering the endpoints index page'
                return web_exception_subroutine(flash_message, e, flask.current_app)
    
    except Exception as e:
        # Handle any other exceptions
        flash_message = 'An unexpected error occurred'
        return web_exception_subroutine(flash_message, e, flask.current_app)