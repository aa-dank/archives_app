import os
import flask
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField
from wtforms.validators import DataRequired, ValidationError
from flask_wtf.file import FileField, FileRequired
from .. import utils


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


class FileSearchForm(FlaskForm):
    search_location = StringField('Search Location')
    search_term = StringField('Search Term', validators=[DataRequired()])
    submit = SubmitField('Search')

    def validate_search_location(self, search_location):
        """
        Ensures that the search location exists
        """
        if search_location.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=search_location.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{search_location.data}\n{e}")
            
            if not os.path.exists(network_path):
                raise ValidationError(f"Search location doesn't exist: \n{search_location.data}")


class ScrapeLocationForm(FlaskForm):
    scrape_location = StringField('Scrape Location', validators=[DataRequired()])
    recursive = SelectField('Recursive', choices=[('True', 'True'), ('False', 'False')], default='True')
    submit = SubmitField('Scrape')

    def validate_scrape_location(self, scrape_location):
        """
        Ensures that the scraping location exists
        """
        if scrape_location.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=scrape_location.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{scrape_location.data}\n{e}")
            
            if not os.path.exists(network_path):
                raise ValidationError(f"Search location doesn't exist: \n{scrape_location.data}")


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
        """
        Ensures that the path to delete exists
        """
        if path_delete.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=path_delete.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{path_delete.data}\n{e}")
            
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{path_delete.data}")

    def validate_new_directory(self, new_directory):
        """
        Ensures that the new directory doesn't already exist and that the parent directory does exist
        """
        if new_directory.data:
            network_directory = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=new_directory.data,
                                                                          location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if os.path.exists(os.path.join(network_directory)):
                raise ValidationError(f"Directory already exists:\n{new_directory.data}")
            
            #check that the parent directory for new directory exists
            path_list = utils.FileServerUtils.split_path(network_directory)
            parent_directory = os.path.join(*path_list[:-1])
            if not os.path.exists(parent_directory):
                raise ValidationError(f"Parent directory doesn't exist:\n{parent_directory}")

            if utils.contains_unicode(path_list[-1]):
                raise ValidationError(f"Directory name contains unicode characters:\n{path_list[-1]}")

    def validate_current_path(self, current_path):
        """
        Ensures that the current path exists
        """
        if current_path.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=current_path.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{current_path.data}\n{e}")
            
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{current_path.data}")

    def validate_destination_path(self, destination_path):
        """
        Ensures that the destination path exists and is a directory
        """
        if destination_path.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=destination_path.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{destination_path.data}\n{e}")
            
            if not os.path.exists(network_path):
                raise ValidationError(f"Destination location doesn't exist: \n{destination_path.data}")

            elif not os.path.isdir(network_path):
                raise ValidationError(f"Destination location is not a directory: \n{destination_path.data}")

    def validate_asset_path(self, asset_path):
        """
        Ensures that the asset path exists
        """
        if asset_path.data:
            try:
                network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=asset_path.data,
                                                                         location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            except Exception as e:
                raise ValidationError(f"Error converting user path to network path: \n{asset_path.data}\n{e}")

            if not os.path.exists(network_path):
                raise ValidationError(f"Asset doesn't exist to move: \n{asset_path.data}")
