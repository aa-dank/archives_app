import flask
from flask_wtf import FlaskForm
from archives_application.models import *
from wtforms import SubmitField, TextAreaField, DateField, SelectField, IntegerField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError


class TimekeepingForm(FlaskForm):
    clock_in = SubmitField('Clock In')
    clock_out = SubmitField('Clock Out')
    clock_out_log_out = SubmitField('Clock Out, Log Out')
    journal = TextAreaField('Journal')


class TimeSheetForm(FlaskForm):
    timesheet_begin = DateField('Timesheet Start', validators=[DataRequired()])
    timesheet_end = DateField('Timesheet End', validators=[DataRequired()])
    rolling_avg_window = IntegerField('Rolling Average Window')
    submit = SubmitField('Submit')

    def validate_timesheet_end(self, timesheet_end):
        if not timesheet_end.data > self.timesheet_begin.data:
            raise ValidationError("Must pick a timesheet start that occurs before the timesheet end.")


class TimeSheetAdminForm(FlaskForm):
    employee_email = SelectField('Employee Email', validators=[DataRequired()])
    submit = SubmitField('Submit')