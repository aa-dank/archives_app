from flask_wtf import FlaskForm
from archives_application.models import *
import archives_application.config as config
from wtforms import StringField, PasswordField, SubmitField, BooleanField, SelectField, SelectMultipleField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError


class RegistrationForm(FlaskForm):
    # username = StringField('Username', validators= [DataRequired(), Length(min=2)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    roles = SelectMultipleField('Role(s)', validators=[DataRequired()], choices=config.ROLES)
    password = PasswordField('password', validators=[DataRequired()])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
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