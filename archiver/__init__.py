from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from archiver.forms import *
from archiver import routes #TODO may need to be moved after db=SQLAlchemy

# app setup
app = Flask(__name__)
app.config['SECRET_KEY'] = 'ABC'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///archiver.db'
db = SQLAlchemy(app)