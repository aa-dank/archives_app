import os
import flask
import logging # example usage: https://github.com/tenable/flask-logging-demo
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from archives_application.app_config import json_to_config_factory, get_test_config_path
from oauthlib.oauth2 import WebApplicationClient


db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'users.choose_login'
login_manager.login_message_category = 'info'
google_creds_json = r'google_client_secret.json'

# use pound to choose between config json files
config_json = get_test_config_path()
#config_json = r'deploy_config.json'


def create_app(config_class=json_to_config_factory(google_creds_path=google_creds_json, config_json_path=config_json)):
    # logging format
    defaultFormatter = logging.Formatter('[%(asctime)s] %(levelname)s in %(module)s: %(message)s')

    # start app
    app = flask.Flask(__name__)

    # if the app is not being debugged, then we need to use the gunicorn logger handlers when in production
    if not app.debug:
        app.logger.handlers = logging.getLogger('gunicorn.error').handlers

    # set universal format for all logging handlers.
    for handler in app.logger.handlers:
        handler.setFormatter(defaultFormatter)

    app.config.from_object(config_class)
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    # Set a version number
    app.config['VERSION'] = '0.4.4'
    app.config['google_auth_client'] = WebApplicationClient(config_class.GOOGLE_CLIENT_ID)

    # add blueprints
    from archives_application.users.routes import users
    from archives_application.archiver.routes import archiver
    from archives_application.main.routes import main
    from archives_application.timekeeper.routes import timekeeper
    app.register_blueprint(users)
    app.register_blueprint(archiver)
    app.register_blueprint(main)
    app.register_blueprint(timekeeper)

    # This sets an environmental variable to allow oauth authentication flow to use http requests (vs https)
    if hasattr(config_class, 'OAUTHLIB_INSECURE_TRANSPORT'):
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    # Useful for creating the database tables during development
    #with app.app_context():
    #    db.create_all()

    return app