import flask
from flask_wtf import FlaskForm
from archives_application.models import *
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError

class TimekeepingForm(FlaskForm):
    hour_break = SubmitField('One Hour Break')
    clock_in = SubmitField('Clock In')
    clock_out = SubmitField('Clock Out')
    journal = TextAreaField('Journal')

