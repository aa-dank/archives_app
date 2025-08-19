# archives_application/main/forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, TextAreaField


def form_factory(fields_dict, form_class_name):
    """
    Generates a flask form from a dictionary. Useful for creating a form for changing values stored in json file
    :param fields_dict: Dictionary containing field definitions with 'VALUE' keys
    :param form_class_name: Name for the dynamically created form class
    :return: Dynamically created form class
    """
    form_dict = {}
    for field_key in list(fields_dict.keys()):
        # Check if the field value is a list - if so, use TextAreaField to preserve newlines
        if isinstance(fields_dict[field_key].get('VALUE'), list):
            form_dict[field_key] = TextAreaField(field_key, render_kw={"rows": 10, "cols": 80})
        else:
            form_dict[field_key] = StringField(field_key)
    
    form_dict["submit"] = SubmitField("Submit")
    return type(form_class_name, tuple([FlaskForm]), form_dict)
