import flask
import json
import logging
import os
import subprocess
import archives_application.app_config as app_config
from flask_login import current_user
from archives_application.main import forms
from archives_application.utils import roles_required, enqueue_new_task
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
                        'fmp_caan_project_reconciliation_task': 365}

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
        has_admin_role = lambda usr: any([admin_str in usr.roles.split(",") for admin_str in ['admin', 'ADMIN']])

        if flask.request.args.get('user'):
            user_param = flask.request.args.get('user')
            password_param = flask.request.args.get('password')
            user = UserModel.query.filter_by(email=user_param).first()

            # If there is a matching user to the request parameter, the password matches and that account has admin role...
            if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):
                authenticated_to_make_request = True

        elif current_user:
            if current_user.is_authenticated and has_admin_role(current_user):
                authenticated_to_make_request = True

        if authenticated_to_make_request:
            nk_result = enqueue_new_task(db=db,
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
    
    has_admin_role = lambda usr: any([admin_str in usr.roles.split(",") for admin_str in ['admin', 'ADMIN']])

    if flask.request.args.get('user'):
        user_param = flask.request.args.get('user')
        password_param = flask.request.args.get('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):
            task_enqueueing_result = custodian.enqueue_maintenance_tasks(db=db)
            task_enqueueing_result = str_dictionary_values(task_enqueueing_result)
            return flask.Response(response=json.dumps(task_enqueueing_result),
                                  status=200,
                                  mimetype="application/json")
        
    elif current_user:
        if current_user.is_authenticated and has_admin_role(current_user):
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
@roles_required(['ADMIN'])
def change_config_settings():

    # import task here to avoid circular import
    from archives_application.main.main_tasks import restart_app_task


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

            restart_params = {'delay': 15}
            nk_result = enqueue_new_task(db=db,
                                         enqueued_function=restart_app_task,
                                         task_kwargs=restart_params)
            
            flask.flash("Values entered were stored in the config file. Application restart is immenent.", 'success')
            return flask.redirect(flask.url_for('main.home'))

        except Exception as e:
            return web_exception_subroutine(flash_message="Error processing form responses into json config file: ",
                                       thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('change_config_settings.html', title='Change Config File', form=form, settings_dict=config_dict)


@main.route("/test/logging", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
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
def get_db_uri():
    status = db.engine.pool.status()
    info = {
        "database_url": flask.current_app.config.get("SQLALCHEMY_DATABASE_URI"),
        "status": status
    }
    return info


@main.route("/admin/sql_logging", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
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


