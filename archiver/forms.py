import os.path

import archiver.helpers as helpers
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField
from wtforms.validators import DataRequired, Length, Email, EqualTo, ValidationError
from flask_wtf.file import FileField, FileRequired
from flask_login import current_user
from archiver.models import UserModel


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
        user = UserModel.query.filter_by(email=email.data).first()
        if user:
            raise ValidationError('Account registered to this email already exists.')

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])
    remember = BooleanField('Remember Me')
    submit = SubmitField('Login')

class UploadFileForm(FlaskForm):
    project_number = StringField('Project Number', validators=[DataRequired()])
    new_filename = StringField('New Filename')
    document_date = StringField('Document Date')
    destination_directory = SelectField('Destination Directory', validators=[DataRequired()], choices=DIRECTORY_CHOICES)
    notes = StringField('Notes')
    upload = FileField('File Upload', validators=[FileRequired()]) #TODO should we use filetype validation
    submit = SubmitField('Archive File')

class ServerChange(FlaskForm):
    # Place to enter path to asset to be deleted
    path_delete = StringField('Path to asset to delete')

    # Enter a path to be changed, current_path, and then the path that it should be changed to
    current_path = StringField('Path to Change')
    new_path = StringField('New Path')

    # Fields for moving a file
    asset_path = StringField('Path to Asset')
    destination_path = StringField('Destination Directory Path')

    # Form field for adding a new directory
    new_directory = StringField('New Directory Path')
    submit = SubmitField('Execute Change(s)')

    def validate_new_directory(self, new_directory):
        if new_directory:
            network_directory = helpers.mounted_path_to_networked_path(mounted_path=new_directory.data)
            if os.path.exists(os.path.join(network_directory)):
                raise ValidationError(f"Directory already exists:\n{new_directory.data}")

    def validate_destination_path(self, destination_path):
        if destination_path.data:
            network_path = helpers.mounted_path_to_networked_path(destination_path.data)
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{destination_path.data}")

            elif not os.path.isdir(network_path):
                raise ValidationError(f"Destination location is not a directory: \n{destination_path.data}")

    def validate_asset_path(self, asset_path):
        if asset_path.data:
            network_path = helpers.mounted_path_to_networked_path(asset_path.data)
            if not os.path.exists(network_path):
                raise ValidationError(f"Asset doesn't exist to move: \n{asset_path.data}")






