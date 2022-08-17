import os
import flask
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from archives_application.app_config import json_to_config_factory
from oauthlib.oauth2 import WebApplicationClient


db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'users.login'
login_manager.login_message_category = 'info'
google_creds_json = r'google_client_secret.json'
config_json = r'test_app_config.json'
#config_json = r'deploy_app_config.json'

def create_app(config_class=json_to_config_factory(google_creds_path=google_creds_json,config_json_path=config_json)):
#def create_app(config_class = DefaultTestConfig):
    app = flask.Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    # Set a version number
    app.config['VERSION'] = '0.1.2'
    app.config['google_auth_client'] = WebApplicationClient(config_class.GOOGLE_CLIENT_ID)

    from archives_application.users.routes import users
    from archives_application.archiver.routes import archiver
    from archives_application.main.routes import main
    app.register_blueprint(users)
    app.register_blueprint(archiver)
    app.register_blueprint(main)

    # This sets an environmental variable to allow oauth authentication flow to use http requests (vs https)
    if hasattr(config_class, 'OAUTHLIB_INSECURE_TRANSPORT'):
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'


    return app