from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from flask_wtf.file import FileField, FileRequired
from archiver.models import User

DIRECTORY_CHOICES = ['A - General', 'B - Administrative Reviews and Approvals', 'C - Consultants',
                     'D - Environmental Review Process', 'E - Program and Design',
                     'F - Bid Documents and Contract Award', 'G - Construction', "H - Submittals and O&M's",
                     'A1 - Miscellaneous', 'A2 - Working File', 'A3 - Project Directory Matrix & Project Chronology',
                     "B1 - CPS and Chancellor's Approvals", 'B100 - Other', 'B11 - LEED',
                     'B12 - Outside Regulatory Agencies', 'B13 - Coastal Commission',
                     'B2 - Office of the President UC Regents', 'B3 - State Public Works Board',
                     'B4 - Department of Finance', 'B5 - Legislative Submittals', 'B6 - State Fire Marshal',
                     'B7 - Office of State Architect  (DSA)', 'B8 -  General Counsel'
                     ]

class RegistrationForm(FlaskForm):
    # username = StringField('Username', validators= [DataRequired(), Length(min=2)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    password = PasswordField('password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators= [DataRequired(), EqualTo('password')])
    submit = SubmitField('Sign Up')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Account registered to this email already exists.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class uploadFileForm(FlaskForm):
    project_number = StringField('Project Number', validators=[DataRequired()])
    new_filename = StringField('New Filename')
    document_date = StringField('Document Date')
    destination_directory = SelectField('Destination Directory', validators=[DataRequired()], choices=DIRECTORY_CHOICES)
    notes = StringField('Notes')
    upload = FileField('File Upload', validators=[FileRequired()])
    submit = SubmitField('Archive File')

