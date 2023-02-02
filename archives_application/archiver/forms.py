import os
import flask
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField
from wtforms.validators import DataRequired, ValidationError
from flask_wtf.file import FileField, FileRequired
from .. import utilities


class UploadFileForm(FlaskForm):
    project_number = StringField('Project Number')
    new_filename = StringField('New Filename')
    document_date = StringField('Document Date')
    destination_directory = SelectField('Destination Directory')
    destination_path = StringField('Destination Path')
    notes = StringField('Notes')
    upload = FileField('File Upload', validators=[FileRequired()])
    submit = SubmitField('Archive File')


class ArchivedOrNotForm(FlaskForm):
    project_number = StringField('Project Number')
    search_path = StringField('Search Location Path')
    upload = FileField('File Upload', validators=[FileRequired()])
    submit = SubmitField('Submit')


class InboxItemForm(FlaskForm):
    download_item = SubmitField('Download')
    project_number = StringField('Project Number')
    new_filename = StringField('New Filename')
    document_date = StringField('Document Date')
    destination_directory = SelectField('Destination Directory')
    destination_path = StringField('Destination Path')
    notes = StringField('Notes')
    submit = SubmitField('Archive File')


class ServerChangeForm(FlaskForm):
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

    def validate_path_delete(self, path_delete):
        if path_delete.data:
            network_path = utilities.user_path_to_server_path(path_from_user=path_delete.data,
                                                              location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{path_delete.data}")

    def validate_new_directory(self, new_directory):
        if new_directory.data:
            network_directory = utilities.user_path_to_server_path(path_from_user=new_directory.data,
                                                                   location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if os.path.exists(os.path.join(network_directory)):
                raise ValidationError(f"Directory already exists:\n{new_directory.data}")
            #TODO check that the parent directory for new directory exists

    def validate_current_path(self, current_path):
        if current_path.data:
            network_path = utilities.user_path_to_server_path(path_from_user=current_path.data,
                                                              location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{current_path.data}")

    def validate_destination_path(self, destination_path):
        if destination_path.data:
            network_path = utilities.user_path_to_server_path(path_from_user=destination_path.data,
                                                              location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{destination_path.data}")

            elif not os.path.isdir(network_path):
                raise ValidationError(f"Destination location is not a directory: \n{destination_path.data}")

    def validate_asset_path(self, asset_path):
        if asset_path.data:
            network_path = utilities.user_path_to_server_path(path_from_user=asset_path.data,
                                                              location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if not os.path.exists(network_path):
                raise ValidationError(f"Asset doesn't exist to move: \n{asset_path.data}")
