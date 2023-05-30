import flask
import json
import logging
import os
import subprocess
import shutil
import sys
import archives_application.app_config as app_config
from flask_login import current_user
from archives_application.main import forms
from archives_application.utilities import roles_required
from archives_application import db, bcrypt
from archives_application.models import *



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


def make_postgresql_backup():

    """
    Subroutine for sending pg_dump command to shell
    Resources:
    https://stackoverflow.com/questions/63299534/backup-postgres-from-python-on-win10
    https://stackoverflow.com/questions/43380273/pg-dump-pg-restore-password-using-python-module-subprocess
    https://medium.com/poka-techblog/5-different-ways-to-backup-your-postgresql-database-using-python-3f06cea4f51

    An example of desired command:
    pg_dump postgresql://archives:password@localhost:5432/archives > /opt/app/data/Archive_Data/backup101.sql
    """
    db_url = flask.current_app.config.get("SQLALCHEMY_DATABASE_URI")
    db_backup_destination = flask.current_app.config.get("DATABASE_BACKUP_LOCATION")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    db_backup_destination = db_backup_destination + f"/db_backup_{timestamp}.sql"
    db_backup_cmd = fr"""sudo pg_dump {db_url} > {db_backup_destination}"""

    # If running on windows, remove sudo from command...
    if sys.platform.lower() not in ['linux', 'linux2', 'darwin']:
        db_backup_cmd = db_backup_cmd[5:]

    cmd_result = subprocess.run(db_backup_cmd, shell=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    # if passing the pg_dump command to the shell failed...
    if cmd_result.stderr:
        raise Exception(
            f"Backup command failed: Stderr from attempt to call pg_dump back-up command:\n{cmd_result.stderr}")
    return cmd_result.stdout, cmd_result.stderr

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

    def api_exception_subroutine(response_message, thrown_exception):
        """
        Subroutine for handling an exception and returning response code to api call
        @param response_message: message sent with response code
        @param thrown_exception: exception that broke the 'try' conditional
        @return:
        """
        flask.current_app.logger.error(thrown_exception, exc_info=True)
        return flask.Response(response_message + "\n" + thrown_exception, status=500)

    has_admin_role = lambda usr: any([admin_str in usr.roles.split(",") for admin_str in ['admin', 'ADMIN']])

    if flask.request.args.get('user'):
        user_param = flask.request.args.get('user')
        password_param = flask.request.args.get('password')
        user = UserModel.query.filter_by(email=user_param).first()

        # If there is a matching user to the request parameter, the password matches and that account has admin role...
        if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):

            try:
                make_postgresql_backup()
            except Exception as e:
                msg = "Error during function to backup the database:\n"
                return api_exception_subroutine(msg, e)

            return flask.Response("Database Back Up Successful", status=200)

    elif current_user:
        if current_user.is_authenticated and has_admin_role(current_user):
            try:
                make_postgresql_backup()
            except Exception as e:
                msg = "Error during function to backup the database:\n"
                return api_exception_subroutine(msg, e)

            flask.flash("Database backup successs.", 'info')
            return flask.redirect(flask.url_for('main.home'))


@main.route("/admin/config", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
def change_config_settings():

    config_dict = {}
    config_filepath = flask.current_app.config.get('CONFIG_JSON_PATH')
    form = None
    try:
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

            flask.flash("Values entered were stored in the config file.", 'success')
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

def test_task(a):
    return (a + 4) * 3

@main.route("/test_rq", methods=['GET', 'POST'])
def queue_test():
    result = flask.current_app.q.enqueue(test_task, 2)
    return {"test task id": result.id, "Redis URL": flask.current_app.config.get("REDIS_URL")}

@main.route("/test_rq/<id>", methods=['GET', 'POST'])
def check_task(id):
    job = flask.current_app.q.fetch_job(id)
    if job is None:
        return {"status": "error", "message": f"No job found with id {id}"}
    elif job.is_finished:
        result = job.result
        return {"status": "success", "result": result}
    else:
        return {"status": "pending"}