# archives_application/project_tools/forms.py

import flask
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField

class CAANSearchForm(FlaskForm):
    enter_caan = StringField("Enter CAAN")
    search_query = StringField("Search Query")
    submit = SubmitField("Search")