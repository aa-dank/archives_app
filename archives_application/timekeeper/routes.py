import flask
from datetime import datetime
from ..utilities import roles_required
from flask_login import login_required, current_user

timekeeper = flask.Blueprint('timekeeper', __name__)

def last_timekeeper_event(user_email = current_user.email):
    
    pass


@timekeeper.route("/timekeeper", methods=['GET', 'POST'])
@login_required
@roles_required(['ADMIN', 'ARCHIVIST'])
def timekeeper_event():

    pass
