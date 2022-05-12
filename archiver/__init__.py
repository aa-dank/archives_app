from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_bcrypt import Bcrypt
from flask_login import LoginManager

# app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'ABC'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///archiver.db'
db = SQLAlchemy(app)
bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'info'

# This needs to be imported down here for important reasons beyond me.
from archiver import routes