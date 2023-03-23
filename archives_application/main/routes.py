import flask
import json
import os
import subprocess
import shutil
import sys
from celery.result import AsyncResult
from flask_login import current_user
from . import forms,tasks
from .. utilities import roles_required
from archives_application import db, bcrypt
from archives_application.models import *


main = flask.Blueprint('main', __name__)


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


def make_sqlite_backup():
    def clean_url(u: str):
        """
        Turns the sqlite url into a path for shutil.copy
        @param u: sqlite url
        @return:  sqlite path
        """
        u = u[7:]
        for idx, c in enumerate(u):
            if c not in [r"\\",r"/"]:
                u = u[idx:]
        return u

    db_url = flask.current_app.config.get("SQLALCHEMY_DATABASE_URI")
    db_backup_destination = flask.current_app.config.get("DATABASE_BACKUP_LOCATION")
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    db_backup_destination = os.path.join(db_backup_destination, f"db_backup_{timestamp}.sql")
    db_url = clean_url(db_url)
    if not os.path.exists(db_url):
        raise Exception("Cannot use sqlite db location to create a backup; os.path.exists failed. ")

    try:
        shutil.copyfile(db_url, db_backup_destination)
    except Exception as e:
        raise Exception(f"Shutil.copyfile failed to copy the database and threw this error:\n{e}")

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
            # if using a postgresql database
            if flask.current_app.config.get("POSTGRESQL_DATABASE"):
                try:
                    make_postgresql_backup()
                except Exception as e:
                    msg = "Error during function to backup the database:\n"
                    return api_exception_subroutine(msg, e)

            # if using a sqlite database
            else:
                try:
                    make_sqlite_backup()
                except Exception as e:
                    msg = "Error during function to backup the database:\n"
                    return api_exception_subroutine(msg, e)

            return flask.Response("Database Back Up Successful", status=200)

    elif current_user:
        if current_user.is_authenticated and has_admin_role(current_user):
            if flask.current_app.config.get("POSTGRESQL_DATABASE"):
                try:
                    make_postgresql_backup()
                except Exception as e:
                    msg = "Error during function to backup the database:\n"
                    return api_exception_subroutine(msg, e)
            else:
                try:
                    make_sqlite_backup()
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
        return exception_handling_pattern(flash_message=m, thrown_exception=e, app_obj=flask.current_app)

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
            return exception_handling_pattern(flash_message="Error processing form responses into json config file: ",
                                       thrown_exception=e, app_obj=flask.current_app)

    return flask.render_template('change_config_settings.html', title='Change Config File', form=form, settings_dict=config_dict)


@main.route("/test/logging", methods=['GET', 'POST'])
@roles_required(['ADMIN'])
def test_logging():
    """
    endpoint for seeing how the system responds to errors
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


@main.route("/test/celery", methods=['GET', 'POST'])
def test_celery():
    celery = flask.current_app.extensions["celery"]
    result = tasks.test_task.delay(3, 4)
    return {"result_id": result.id}

@main.route("/test/<id>")
def test_task_results(id: str):
    result = AsyncResult(id)
    return {
        "ready": result.ready(),
        "successful": result.successful(),
        "value": result.result if result.ready() else None,
    }
