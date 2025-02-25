import errno
import os
import flask
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, SelectField, BooleanField, SelectMultipleField, widgets
from wtforms.validators import DataRequired, ValidationError
from flask_wtf.file import FileField, FileRequired
from .. import utils


def path_validation_subroutine(path_form_field: StringField, path_type: str = None):
    """
    Ensures that the path exists and matches the type requirement
    :param path_form_field: The form field that contains the path string
    :param path_type: The type of path that is required. Either "file" or "dir"
    """
    if path_type not in ["file", "dir", None]:
        raise ValueError("path_type must be either 'file' or 'dir'")
    
    if path_form_field.data:
        path_validation_error = lambda mssg: ValidationError(f"{mssg}: \n{path_form_field.data}")
        try:
            network_path = utils.FlaskAppUtils.user_path_to_app_path(
                path_from_user=path_form_field.data,
                app=flask.current_app
            )
        except Exception as e:
            raise ValidationError(f"Error converting user path to network path: \n{path_form_field.data}\n{e}")
        
        try:
            os.stat(network_path)
        except OSError as e:
            if e.errno == errno.ENOENT:
                valid_error = f"Path doesn't exist on server:\n{network_path}\nEntered path"
                raise path_validation_error(valid_error)
            elif e.errno == errno.EACCES:
                valid_error = f"Permission denied accessing path:\n{network_path}\nEntered path"
                raise path_validation_error(valid_error)
            else:
                valid_error = f"Error accessing path:\n{network_path}\nError: {e}\nEntered path"
                raise path_validation_error(valid_error)
        
        if path_type == "file" and not os.path.isfile(network_path):
            valid_error = f"Path is not a file:\n{network_path}\nEntered path"
            raise path_validation_error(valid_error)

        if path_type == "dir" and not os.path.isdir(network_path):
            valid_error = f"Path is not a directory:\n{network_path}\nEntered path"
            raise path_validation_error(valid_error)


def validate_str_path(form: FlaskForm, field: StringField):
    """
    Universal simple file and directory path validation function
    """
    path_validation_subroutine(field)


class MultiCheckboxField(SelectMultipleField):
    """
    A multiple-select, except displays a list of checkboxes.

    Iterating the field will produce subfields, allowing custom rendering of
    the enclosed checkbox fields.
    https://gist.github.com/ectrimble20/468156763a1389a913089782ab0f272e
    """
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


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

class BatchInboxItemsForm(FlaskForm):
    items_to_archive = MultiCheckboxField('Items to archive', choices=[])
    project_number = StringField('Project Number')
    destination_directory = SelectField('Destination Directory')
    destination_path = StringField('Destination Path')
    notes = StringField('Notes')
    submit = SubmitField('Archive Items')

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
                                                                          app=flask.current_app)
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
    asset_path = StringField('Path to Target Directory')
    destination_path = StringField('Destination Directory Path')
    remove_asset = BooleanField('Remove (empty) target directory', default=True)
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


class BatchMoveEditForm(FlaskForm):
    asset_path = StringField('Path to Target Directory')
    contents_to_move = MultiCheckboxField('Contents to Move', choices=[])
    destination_path = StringField('Destination Directory Path')
    submit = SubmitField('Execute Change')

    def validate_destination_path(self, destination_path):
        """
        Ensures that the destination path exists and is a directory
        """
        # if nothing is selected, then just return
        if destination_path.data:
            path_validation_subroutine(destination_path, path_type="dir")
    
    def validate_asset_path(self, asset_path):
        """
        Ensures that the asset path exists
        """
        if asset_path.data:
            path_validation_subroutine(asset_path, path_type="dir")