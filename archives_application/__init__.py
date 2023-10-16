import os
import flask
import glob
import logging
import redis
import rq
import archives_application.app_config as app_config
import pandas as pd
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from oauthlib.oauth2 import WebApplicationClient
from werkzeug.middleware.proxy_fix import ProxyFix

VERSION = '1.3.6'

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'users.choose_login'
login_manager.login_message_category = 'info'
google_creds_json = r'google_client_secret.json'

# These lines are used to set the config file for the app. If it is not set correctly,
# the first error will LIKELY be issues with connecting to the database.
#config_json = next(glob.iglob('test_config*'), None)  # get the first test_config file
#config_json = r'deploy_app_config.json'

def create_app(config_class=app_config.json_to_config_factory(google_creds_path=google_creds_json,
                                                              config_json_path=config_json)):

    # logging format
    # example usage: https://github.com/tenable/flask-logging-demo
    default_formatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')
    
    # start app
    app = flask.Flask(__name__)

    # Suppress SettingWithCopyWarning
    # https://stackoverflow.com/questions/20625582/how-to-deal-with-settingwithcopywarning-in-pandas
    pd.options.mode.chained_assignment = None

    # if the app is not being debugged, then we need to use the gunicorn logger handlers when in production.
    # also need to do something so that it can accept proxy calls
    if not app.debug:
        app.logger.handlers = logging.getLogger('gunicorn.error').handlers

        # https://flask.palletsprojects.com/en/2.3.x/deploying/proxy_fix/
        # https://werkzeug.palletsprojects.com/en/1.0.x/middleware/proxy_fix/
        app.wsgi_app = ProxyFix(app.wsgi_app)


    # set universal format for all logging handlers.
    app.config['DEFAULT_LOGGING_FORMATTER'] = default_formatter
    for handler in app.logger.handlers:
        handler.setFormatter(default_formatter)

    # config app from config class
    app.config.from_object(config_class)

    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    # add version number to config
    app.config['VERSION'] = VERSION

    # If the SQLALCHEMY_ECHO parameter is true, need to set up logs for logging sql.
    # This is useful for debugging sql queries and postgresql errors.
    # https://docs.sqlalchemy.org/en/13/core/engines.html#sqlalchemy.create_engine.params.echo
    if app.config.get("SQLALCHEMY_ECHO"):
        log_path = os.path.join(app.config.get("DATABASE_BACKUP_LOCATION"), app.config.get("SQLALCHEMY_LOG_FILE"))
        app_config.setup_sql_logging(log_filepath=log_path)

    # Create Oauth client for using google services
    app.config['google_auth_client'] = WebApplicationClient(config_class.GOOGLE_CLIENT_ID)

    # add redis queue for asynchronous tasks
    if app.config.get("REDIS_URL"):
        app.q = rq.Queue(connection=redis.from_url(app.config.get("REDIS_URL")))

    # add blueprints
    # https://flask.palletsprojects.com/en/1.1.x/blueprints/
    from archives_application.users.routes import users
    from archives_application.archiver.routes import archiver
    from archives_application.main.routes import main
    from archives_application.timekeeper.routes import timekeeper
    from archives_application.project_tools.routes import project_tools

    app.register_blueprint(users)
    app.register_blueprint(archiver)
    app.register_blueprint(main)
    app.register_blueprint(timekeeper)
    app.register_blueprint(project_tools)

    # This sets an environmental variable to allow oauth authentication flow to use http requests (vs https)
    if hasattr(config_class, 'OAUTHLIB_INSECURE_TRANSPORT'):
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    return app