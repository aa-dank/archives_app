import flask
from flask import Blueprint

main = Blueprint('main', __name__)

@main.route("/")
@main.route("/home")
def home():
    return flask.render_template('home.html')