"""Shared archiving form validation, actions, and template rendering."""

from io import BytesIO

import pytest
from flask import render_template
from werkzeug.datastructures import FileStorage, MultiDict

from archives_application import create_app
from archives_application.archiver.forms import BatchInboxItemsForm, InboxItemForm, UploadFileForm


FORMS = (
    ("upload_file", UploadFileForm),
    ("inbox_item", InboxItemForm),
    ("batch_process_inbox", BatchInboxItemsForm),
)
FILING_CODE = "E5 - Correspondence"


class TestConfig:
    TESTING = True
    SECRET_KEY = "archive-forms-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "archive-forms-test"
    KEY_VALUE_STORE_URL = "redis://localhost:6379/0"
    WTF_CSRF_ENABLED = False


@pytest.fixture(scope="module")
def app():
    return create_app(TestConfig)


def archive_form(form_class, **overrides):
    values = MultiDict({
        "project_number": "1234-001", "destination_directory": FILING_CODE,
        "destination_path": "", "notes": "Notes", **overrides,
    })
    form = form_class(values, data={
        "upload": FileStorage(stream=BytesIO(b"document"), filename="document.pdf"),
    })
    form.destination_directory.choices = [FILING_CODE]
    return form


@pytest.mark.parametrize("template,form_class", FORMS)
@pytest.mark.parametrize("project_number,valid", [("1234-001", True), ("", True), ("custom-number", False)])
def test_shared_project_validation_keeps_direct_path_option(app, template, form_class, project_number, valid):
    with app.test_request_context():
        form = archive_form(form_class, project_number=project_number, destination_path=r"N:\Records\Custom")
        assert form.validate() is valid
        assert bool(form.project_number.errors) is not valid
        assert form.destination_path.data == r"N:\Records\Custom"


@pytest.mark.parametrize("template,form_class", FORMS)
def test_filing_code_choices_still_reject_unknown_values(app, template, form_class):
    with app.test_request_context():
        form = archive_form(form_class, destination_directory="Unknown filing code")
        assert not form.validate()
        assert form.destination_directory.errors


def test_upload_keeps_required_file_and_inbox_keeps_download_action(app):
    with app.test_request_context():
        upload = archive_form(UploadFileForm)
        upload.upload.data = None
        assert not upload.validate()
        assert upload.upload.errors
        inbox = archive_form(InboxItemForm, download_item="Download Copy")
        assert inbox.validate()
        assert inbox.download_item.data is True
        assert inbox.submit.data is False


@pytest.mark.parametrize("template,form_class", FORMS)
def test_templates_preserve_field_values_and_escape_errors(app, template, form_class):
    unsafe = '<script>alert("example")</script>'
    with app.test_request_context():
        form = archive_form(form_class, notes=unsafe)
        form.project_number.errors = [unsafe]
        html = render_template(
            template + ".html", form=form, title="Archive", item_filename="document.pdf",
            preview_image="/preview.svg", indexed_archive_locations=[],
        )
        for field in ("project_number", "destination_directory", "destination_path", "notes"):
            assert html.count(f'name="{field}"') == 1
            assert f'for="{field}"' in html
        assert f'value="{FILING_CODE}"' in html
        assert unsafe not in html
        assert "&lt;script&gt;" in html
        assert 'class="form-control form-control-lg is-invalid"' in html
        assert 'class="invalid-feedback"' in html
        assert "Option A: Project + Filing Code" in html
        assert "Option B: Direct destination path" in html
        if template == "upload_file":
            assert "archive_loading.js" not in html
        else:
            assert html.count('src="/static/archive_loading.js"') == 1
            assert "data-archive-submit" in html and "data-archive-loading" in html
        if template == "inbox_item":
            assert 'name="download_item"' in html
            assert "Use notes to document file issues or archiving context." in html
        elif template == "batch_process_inbox":
            assert "Use this when files need a non-standard destination path." in html
