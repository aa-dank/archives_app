# archives_application/timekeeper/forms.py

from flask_wtf import FlaskForm
from archives_application.models import *
from wtforms import SubmitField, TextAreaField, DateField, SelectField, IntegerField, RadioField, TimeField
from wtforms.validators import DataRequired, Optional, ValidationError

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
    selected_date = DateField('Date', validators=[Optional()])  # Add Optional validator
    selected_time = TimeField('Time (optional)', validators=[Optional()])  # Add Optional validator
    submit = SubmitField('Submit')

    def validate(self, extra_validators=None):
        """Custom validation based on selected operation"""
        # Store original validation result but continue execution
        is_valid = super().validate(extra_validators=extra_validators)
        
        # Custom conditional validation
        if self.operation.data == 'who_work_when' and not self.selected_date.data:
            self.selected_date.errors = ["Date is required when viewing who worked when"]
            return False
        
        if self.operation.data == 'employee_timesheet' and not self.employee_email.data:
            self.employee_email.errors = ["Employee email is required when viewing timesheets"]
            return False
        
        # Return the original validation result after applying our custom rules
        return is_valid