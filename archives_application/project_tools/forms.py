# archives_application/project_tools/forms.py

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import ValidationError

from archives_application.project_tools import caan_search


class CAANSearchForm(FlaskForm):
    enter_caan = StringField("Enter CAAN")
    search_query = StringField("Search Query")
    submit = SubmitField("Search")

    def validate_enter_caan(self, field):
        """Keep failed exact lookups on the form so the user can correct them."""
        value = (field.data or "").strip()
        if value and caan_search.find_exact_caan(value) is None:
            raise ValidationError(
                f'No CAAN found for “{value}”. Check the number, or clear this '
                'field and use Search Query to search by name or description.'
            )
