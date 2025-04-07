from flask_wtf import FlaskForm
from archives_application.models import *
from wtforms import SubmitField, TextAreaField, DateField, SelectField, IntegerField, RadioField, TimeField
from wtforms.validators import DataRequired, ValidationError


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

    def validate_rolling_avg_window(self, rolling_avg_window):
        if rolling_avg_window.data and rolling_avg_window.data <= 1:
            raise ValidationError("Rolling average window must be greater than 1.")


class TimeKeeperAdminForm(FlaskForm):
    operation = RadioField('Operation', 
                          choices=[('employee_timesheet', 'View Employee Timesheet'), 
                                   ('who_work_when', 'View Who Worked When')],
                          validators=[DataRequired()],
                          default='employee_timesheet')
    employee_email = SelectField('Employee Email')
    selected_date = DateField('Date')
    selected_time = TimeField('Time (optional)')
    submit = SubmitField('Submit')