import flask
import json
import logging
import os
import subprocess
import archives_application.app_config as app_config
from flask_login import current_user
from archives_application.main import forms
from archives_application.utils import *
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
                        'batch_move_edits_task': 365}

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
    return flask.render_template('home.html')


@main.route("/about")
def about():
    return flask.render_template('about.html', title='About')


@main.route("/admin")
def main_admin():
    #TODO add page of links to admin pages
    flask.flash("Admin enpoint hit.")
    return flask.redirect(flask.url_for('main.home'))


@main.route("/admin/db_backup", methods=['GET', 'POST'])
def backup_database():
    """
    This endpoint is for backing up the databases. It can be used for manual backups by navigating to the url as a user
    or one can pass credentials to it in the request which is useful for a scheduled process
    @return:
    """
    
    # import task here to avoid circular import
    from archives_application.main.main_tasks import db_backup_task


    def api_exception_subroutine(response_message, thrown_exception):
        """
        Subroutine for handling an exception and returning response code to api call
        @param response_message: message sent with response code
        @param thrown_exception: exception that broke the 'try' conditional
        @return:
        """
        flask.current_app.logger.error(thrown_exception, exc_info=True)
        return flask.Response(response_message + "\n" + thrown_exception, status=500)
    
    try:
        
        # first determine if the request is being made by an admin user
        authenticated_to_make_request = False

        if flask.request.args.get('user'):
            user_param = flask.request.args.get('user')
            password_param = flask.request.args.get('password')
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
            if flask.request.args.get('user'):
                return flask.Response(f"Database Back-up Task Enqueued. Job ID: {job_id}", status=200)
            
            flask.flash("Database Back-up Task Enqueued.", 'info')
            return flask.redirect(flask.url_for('main.home'))

        return flask.Response("Unauthorized", status=401)

    except Exception as e:
        return api_exception_subroutine("Database Backup Failed", str(e))


@main.route("/admin/maintenance", methods=['GET', 'POST'])
def app_maintenance():
    """
    This endpoint is used to perform regular maintenance tasks on the application and database.
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

    if flask.request.args.get('user'):
        user_param = flask.request.args.get('user')
        password_param = flask.request.args.get('password')
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
    """
    Endpoint for changing the configuration settings of the application.
    After editing the configuration json file, the application will restart.
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

                        # To process into a list we remove
                        new_val = [x.strip() for x in new_val.split(",") if x != '']
                    config_dict[k]['VALUE'] = new_val

            with open(config_filepath, 'w') as config_file:
                json.dump(config_dict, config_file)
            
            # restart the application workers
            worker_restart_results = restart_app_workers()
            # if the worker restart command failed, raise an error
            print(worker_restart_results) # TODO remove this line
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
    """
    endpoint for seeing how the system responds to different logging events
    @return:
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
def get_db_uri():
    """
    Display the current database uri and the status of the database connection pool.
    """
    status = db.engine.pool.status()
    info = {
        "database_url": flask.current_app.config.get("SQLALCHEMY_DATABASE_URI"),
        "status": status
    }
    return info


@main.route("/test/see_config")
@FlaskAppUtils.roles_required(['ADMIN'])
def get_app_config():
    """
    Endpoint function to see the current configuration of the runnning application.
    Useful for debugging and checking the current state of the application.
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
    return info

@main.route("/test/rq", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def test_rq_connection():
    """
    Endpoint for testing the rq task queue.
    """
    try:
        redis_client = flask.current_app.q.connection
        redis_client.ping()
        redis_info = redis_client.info()
        return redis_info
    
    except Exception as e:
        m = "Error connecting to the redis queue: "
        return web_exception_subroutine(flash_message=m, thrown_exception=e, app_obj=flask.current_app)

@main.route("/admin/sql_logging", methods=['GET', 'POST'])
@FlaskAppUtils.roles_required(['ADMIN'])
def toggle_sql_logging():
    """
    The purpose of this endpoint is to toggle the logging of sql statements to the console.
    This is useful for debugging, but should not be left on in production.
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
    """
    This endpoint is used to display all the endpoints of the application.
    """
    # get all the endpoints of the application
    output = []
    for rule in sorted(flask.current_app.url_map.iter_rules(), key=lambda r: r.rule):
        methods = ', '.join(sorted(rule.methods))
        endpoint = rule.endpoint
        view_func = flask.current_app.view_functions.get(endpoint)
        doc = view_func.__doc__ or 'No documentation available.'
        line = f"{rule.rule} [{methods}] --> {endpoint}<br>{doc}<br><br>"
        output.append(line)
    return '<br>'.join(output)



