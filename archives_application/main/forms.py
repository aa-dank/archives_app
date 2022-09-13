import flask
import json
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField
from wtforms.validators import DataRequired, ValidationError

def form_factory(fields_dict,  form_class_name):
    """
    Generates a flask form from a dictionary. Useful for creating a form for changing values stored in json file
    :param fields_dict:
    :param form_class_name:
    :return:
    """
    form_dict = {}
    for field_key in list(fields_dict.keys()):
        form_dict[field_key] = StringField(field_key)
    form_dict["submit"] = SubmitField("Submit")
    return type(form_class_name, tuple([FlaskForm]), form_dict)
