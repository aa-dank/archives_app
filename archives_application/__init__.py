from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager
from archives_application.config import DefaultConfig

db = SQLAlchemy()
bcrypt = Bcrypt()
login_manager = LoginManager()
login_manager.login_view = 'users.login'
login_manager.login_message_category = 'info'

def create_app(config_class = DefaultConfig):
    app = Flask(__name__)
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
    return app