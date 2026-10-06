from urllib.parse import parse_qs, urlsplit
from datetime import datetime

import flask
import pandas as pd
import pytest

from archives_application import create_app, db
from archives_application.models import CAANModel
from archives_application.archiver import routes as archive_routes
from archives_application.archiver import archive_search as archive_search_service


class TestConfig:
    SECRET_KEY = "shareable-search-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "shareable-search-test-client"
    REDIS_URL = "redis://localhost:6379/0"
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        CAANModel.__table__.create(db.engine)
        db.session.add(CAANModel(id=1, caan="7110", name="Windows"))
        db.session.commit()
        yield app
        db.session.remove()
        CAANModel.__table__.drop(db.engine)


@pytest.fixture()
def client(app):
    return app.test_client()


def test_caan_url_reruns_search_and_legacy_post_redirects(app, client):
    url = "/caan_search?search_query=Windows"
    first = client.get(url)
    assert first.status_code == 200
    assert b"7110" in first.data

    with app.app_context():
        db.session.add(CAANModel(id=2, caan="7111", name="Windows"))
        db.session.commit()

    second = client.get(url)
    assert second.status_code == 200
    assert b"7111" in second.data

    submitted = client.post("/caan_search", data={"search_query": "Windows"})
    assert submitted.status_code == 302
    assert urlsplit(submitted.location).query == "search_query=Windows"
    form_submission = client.get("/caan_search?enter_caan=&search_query=Windows")
    assert form_submission.status_code == 302
    assert urlsplit(form_submission.location).query == "search_query=Windows"
    assert client.get("/caan_search?enter_caan=7110").status_code == 302


@pytest.mark.parametrize("url", [
    "/caan_search?search_query=", "/caan_search?search_query=x&search_query=y",
    "/caan_search?unexpected=x",
])
def test_caan_url_rejects_invalid_parameters(client, url):
    assert client.get(url).status_code == 400


def test_archive_url_reruns_search_with_location_scope(app, client, monkeypatch, tmp_path):
    calls = []
    workbook_ids = []

    class FakeRun:
        def __init__(self, search_request, **kwargs):
            calls.append(search_request)

        def execute(self):
            return {"results": [], "limit_hit": False, "warnings": []}

    original_render = flask.render_template

    def render_template(template_name, **kwargs):
        if template_name == "archive_search_results.html":
            workbook_ids.append(kwargs["timestamp"])
            return f"fresh run {len(calls)}"
        return original_render(template_name, **kwargs)

    monkeypatch.setattr(archive_routes.archive_search_service, "ArchiveSearchRun", FakeRun)
    monkeypatch.setattr(
        archive_routes.archive_search_service, "build_archive_search_workbook",
        lambda **kwargs: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(archive_routes.flask, "render_template", render_template)
    monkeypatch.setattr(
        archive_routes.utils.FlaskAppUtils, "create_temp_filepath",
        lambda filename, **kwargs: str(tmp_path / filename),
    )

    parameters = {
        "search_term": "window plans",
        "search_mode": "filepath",
        "scope_type": "location",
        "location_scope": r"N:\PPDO\Records\12xx Hahn",
        "file_extension": "pdf, docx",
    }
    first = client.get("/archives_search", query_string=parameters)
    second = client.get("/archives_search", query_string=parameters)
    assert first.status_code == second.status_code == 200
    assert first.data == b"fresh run 1"
    assert second.data == b"fresh run 2"
    assert [request.query_text for request in calls] == ["window plans", "window plans"]
    assert calls[0].requested_scope_value == parameters["location_scope"]
    assert calls[0].search_mode == "filepath"
    assert calls[0].extensions == ("pdf", "docx")
    assert len(workbook_ids[0]) == len(workbook_ids[1]) == 20
    assert workbook_ids[0] != workbook_ids[1]
    download = client.get("/archives_search", query_string={"timestamp": workbook_ids[0]})
    assert download.status_code == 200
    assert download.data.startswith(b"PK")

    submitted = client.post("/archives_search", data=parameters)
    assert submitted.status_code == 302
    redirect_parameters = parse_qs(urlsplit(submitted.location).query)
    assert redirect_parameters["location_scope"] == [parameters["location_scope"]]
    assert redirect_parameters["search_term"] == ["window plans"]
    form_submission = client.get("/archives_search", query_string={
        **parameters, "project_number": "", "caan": "",
    })
    assert form_submission.status_code == 200
    assert form_submission.data == b"fresh run 3"
    assert len(calls) == 3


@pytest.mark.parametrize("url", [
    "/archives_search?search_term=",
    "/archives_search?search_term=window&search_term=plans",
    "/archives_search?search_term=window&unknown=x",
    "/archives_search?search_term=window&scope_type=location",
    "/archives_search?timestamp=../../etc/passwd",
])
def test_archive_url_rejects_invalid_parameters(client, url):
    assert client.get(url).status_code == 400


def test_archive_search_form_uses_get(client):
    response = client.get("/archives_search")
    assert response.status_code == 200
    assert b'method="GET"' in response.data
    assert b'name="csrf_token"' not in response.data


def test_caan_search_form_uses_get(client):
    response = client.get("/caan_search")
    assert response.status_code == 200
    assert b'method="GET"' in response.data
    assert b'name="csrf_token"' not in response.data


def test_get_forms_work_with_site_csrf_enabled(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.get("/caan_search?search_query=Windows").status_code == 200
    assert client.get("/archives_search").status_code == 200


def test_archive_workbook_failure_preserves_page_results(app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        archive_search_service, "build_archive_search_workbook",
        lambda **kwargs: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame()),
    )
    monkeypatch.setattr(
        archive_search_service.utils.FlaskAppUtils, "create_temp_filepath",
        lambda filename, **kwargs: str(tmp_path / filename),
    )

    def fail_excel_writer(*args, **kwargs):
        raise OSError("Workbook unavailable")

    monkeypatch.setattr(archive_search_service.pd, "ExcelWriter", fail_excel_writer)
    search_data = {"warnings": []}
    with app.app_context():
        timestamp = archive_search_service.write_archive_search_workbook(
            search_data, datetime(2026, 9, 29), app
        )
    assert timestamp is None
    assert "Excel export was unavailable" in search_data["warnings"][0]
