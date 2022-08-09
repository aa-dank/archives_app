import os
import flask
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from archives_application.app_config import DefaultTestConfig, json_to_config_factory
from oauthlib.oauth2 import WebApplicationClient


db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'users.login'
login_manager.login_message_category = 'info'

#def create_app(config_class = json_to_config_factory):
def create_app(config_class = DefaultTestConfig):
    app = flask.Flask(__name__)
    app.config.from_object(config_class)
    db.init_app(app)
    bcrypt.init_app(app)
    login_manager.init_app(app)

    from archives_application.users.routes import users
    from archives_application.archiver.routes import archiver
    from archives_application.main.routes import main
    app.register_blueprint(users)
    app.register_blueprint(archiver)
    app.register_blueprint(main)

    # This sets an environmental variable to allow oauth authentication flow to use http requests (vs https)
    if hasattr(config_class, 'OAUTHLIB_INSECURE_TRANSPORT'):
        os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

    #load google credentials and client into the app config
    app.config['GOOGLE_DISCOVERY_URL'] = config_class.GOOGLE_DISCOVERY_URL
    app.config['GOOGLE_CLIENT_SECRET'] = config_class.GOOGLE_CLIENT_SECRET
    app.config['GOOGLE_CLIENT_ID'] = config_class.GOOGLE_CLIENT_ID
    app.config['google_auth_client'] = WebApplicationClient(config_class.GOOGLE_CLIENT_ID)

    return app