import os
import flask
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField, BooleanField
from wtforms.validators import DataRequired, ValidationError
from flask_wtf.file import FileField, FileRequired
from .. import utils


def path_validation_subroutine(path_form_field: StringField, path_type: str = None):
    """
    Ensures that the path exists and matches the type requirment
    :param path_form_field: The form field that contains the path string
    :param path_type: The type of path that is required. Either "file" or "dir"
    """
    if path_type not in ["file", "dir", None]:
        raise ValueError("path_type must be either 'file' or 'dir'")
    
    if path_form_field.data:
        path_validation_error = lambda mssg: ValidationError(f"{mssg}: \n{path_form_field.data}")
        try:
            network_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=path_form_field.data,
                                                                     location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
        except Exception as e:
            raise ValidationError(f"Error converting user path to network path: \n{path_form_field.data}\n{e}")
        
        if not os.path.exists(network_path):
            raise path_validation_error("Path doesn't exist")
        
        if path_type == "file" and not os.path.isfile(network_path):
            raise path_validation_error("path is not a file")

        if path_type == "dir" and not os.path.isdir(network_path):
            raise path_validation_error("path is not a directory")


def validate_str_path(form: FlaskForm, field: StringField):
    """
    Universal simple file and directory path validation function
    """
    path_validation_subroutine(field)


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
    download_item = SubmitField('Download Copy')
    project_number = StringField('Project Number')
    new_filename = StringField('New Filename')
    document_date = StringField('Document Date')
    destination_directory = SelectField('Destination Directory')
    destination_path = StringField('Destination Path')
    notes = StringField('Notes')
    submit = SubmitField('Archive File')


class FileSearchForm(FlaskForm):
    search_location = StringField('Limit Search to Location')
    search_term = StringField('Search Term', validators=[DataRequired()])
    filename_only = BooleanField('Search Filenames Only', default=True)
    submit = SubmitField('Search')

    def validate_search_location(self, search_location):
        """
        Ensures that the search location exists
        """
        path_validation_subroutine(search_location, path_type="dir")


class ScrapeLocationForm(FlaskForm):
    scrape_location = StringField('Scrape Location', validators=[DataRequired()])
    recursive = SelectField('Recursive', choices=[('True', 'True'), ('False', 'False')], default='True')
    submit = SubmitField('Scrape')

    def validate_scrape_location(self, scrape_location):
        """
        Ensures that the scraping location exists
        """
        path_validation_subroutine(scrape_location, path_type="dir")


class ServerChangeForm(FlaskForm):
    # Place to enter path to asset to be deleted
    path_delete = StringField('Path to asset to delete', validators=[validate_str_path])

    # Enter a path to be changed, current_path, and then the path that it should be changed to
    current_path = StringField('Path to Change', validators=[validate_str_path])
    new_path = StringField('New Path')

    # Fields for moving a file
    asset_path = StringField('Path to Asset', validators=[validate_str_path])
    destination_path = StringField('Destination Directory Path')

    # Form field for adding a new directory
    new_directory = StringField('New Directory Path')
    submit = SubmitField('Execute Change')

    def validate_new_directory(self, new_directory):
        """
        Ensures that the new directory doesn't already exist and that the parent directory does exist
        """
        if new_directory.data:
            network_directory = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=new_directory.data,
                                                                          location_path_prefix=flask.current_app.config["ARCHIVES_LOCATION"])
            if os.path.exists(os.path.join(network_directory)):
                raise ValidationError(f"Directory already exists:\n{new_directory.data}")
            
            # check that the parent directory for new directory exists
            path_list = utils.FileServerUtils.split_path(network_directory)
            parent_directory = os.path.join(*path_list[:-1])
            if not os.path.exists(parent_directory):
                raise ValidationError(f"Parent directory doesn't exist:\n{parent_directory}")

            # ensure that the new directory name doesn't contain unicode characters
            if utils.contains_unicode(path_list[-1]):
                raise ValidationError(f"Directory name contains unicode characters:\n{path_list[-1]}")

    def validate_destination_path(self, destination_path):
        """
        Ensures that the destination path exists and is a directory
        """
        path_validation_subroutine(destination_path, path_type="dir")


class BatchServerEditForm(FlaskForm):
    asset_path = StringField('Path to Asset')
    destination_path = StringField('Destination Directory Path')
    remove_asset = BooleanField('Remove Asset', default=True)
    submit = SubmitField('Execute Change')

    def validate_destination_path(self, destination_path):
        """
        Ensures that the destination path exists and is a directory
        """
        path_validation_subroutine(destination_path, path_type="dir")


    def validate_asset_path(self, asset_path):
        """
        Ensures that the asset path exists
        """
        path_validation_subroutine(asset_path, path_type="dir")