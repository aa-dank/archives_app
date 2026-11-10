"""CAAN lookup feedback, direct-link recovery, and projectless detail pages."""

from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import pytest

from archives_application import create_app, db
from archives_application.models import CAANModel, ProjectCaanModel, ProjectModel


class TestConfig:
    TESTING = True
    SECRET_KEY = "caan-lookup-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "caan-lookup-test-client"
    KEY_VALUE_STORE_URL = "redis://localhost:6379/0"
    WTF_CSRF_ENABLED = False
    USER_ARCHIVES_LOCATION = r"Z:\Archives"
    GOOGLE_MAPS_EMBED_API_KEY = "test-map-key"


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.engine.raw_connection().create_function(
            "btrim", 1, lambda value: value.strip() if value else value,
            deterministic=True,
        )
        tables = [CAANModel.__table__, ProjectModel.__table__, ProjectCaanModel.__table__]
        for table in tables:
            table.create(db.engine)
        db.session.add_all([
            CAANModel(
                id=1, caan="7110", name="Library", description="Campus library",
                address_street="100 Campus Drive", address_city="Santa Cruz",
                address_zip="95064", area="Main campus", latitude=37, longitude=-122,
            ),
            CAANModel(id=2, caan="70A", name="Annex"),
        ])
        db.session.commit()
        yield app
        db.session.remove()
        for table in reversed(tables):
            table.drop(db.engine)


@pytest.fixture()
def client(app):
    return app.test_client()


class Inputs(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.fields = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and "name" in attrs:
            self.fields[attrs["name"]] = attrs


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("value", ["not-a-caan", "9999", '<script>alert("x")</script>'])
def test_missing_search_stays_on_form_with_values_and_accessible_error(client, method, value):
    values = {"enter_caan": value, "search_query": "library"}
    response = client.open(
        "/caan_search", method=method,
        **({"query_string": values} if method == "GET" else {"data": values}),
    )
    assert response.status_code == 200
    assert "Location" not in response.headers
    assert "No CAAN found for" in response.text
    assert "clear this field and use Search Query" in response.text
    assert '<script>alert("x")</script>' not in response.text
    fields = Inputs(response.text).fields
    assert fields["enter_caan"]["value"] == value
    assert fields["search_query"]["value"] == "library"
    assert "is-invalid" in fields["enter_caan"]["class"]
    assert fields["enter_caan"]["aria-invalid"] == "true"
    assert fields["enter_caan"]["aria-describedby"] == "enter-caan-errors"


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("value", [" 7110 ", "70A"])
def test_existing_exact_search_redirects_with_trimmed_value(client, method, value):
    values = {"enter_caan": value, "search_query": "ignored"}
    response = client.open(
        "/caan_search", method=method,
        **({"query_string": values} if method == "GET" else {"data": values}),
    )
    assert response.status_code == 302
    assert urlsplit(response.location).path == f"/caan_info/{value.strip()}"
    detail = client.get(response.location)
    assert detail.status_code == 200
    assert "No associated projects found" in detail.text


@pytest.mark.parametrize("value", ["missing", '<img src=x onerror="alert(1)">'])
def test_missing_direct_link_renders_styled_404_with_editable_search(client, value):
    response = client.get(f"/caan_info/{quote(value, safe='')}")
    assert response.status_code == 404
    assert "<h2>CAAN not found</h2>" in response.text
    assert 'class="alert alert-warning"' in response.text
    assert 'href="/caan_search">Back to CAAN Search</a>' in response.text
    assert 'method="GET" action="/caan_search"' in response.text
    assert Inputs(response.text).fields["enter_caan"]["value"] == value
    assert '<img src=x onerror="alert(1)">' not in response.text


def test_projectless_caan_shows_metadata_and_map(client):
    response = client.get("/caan_info/7110")
    assert response.status_code == 200
    for text in (
        "CAAN 7110: Library", "Campus library", "100 Campus Drive",
        "Santa Cruz", "95064", "Main campus", "No associated projects found",
        'title="Map showing the approximate center of CAAN 7110"',
    ):
        assert text in response.text
    assert "Associated Projects:" not in response.text


def test_caan_project_table_preserves_sorting_links_and_escaped_content(app, client):
    with app.app_context():
        db.session.add_all([
            ProjectModel(id=1, number="6300-11", name="Later", drawings=True),
            ProjectModel(id=2, number="6300-7A", name="<b>Earlier</b>", drawings=True,
                         file_server_location="6300-7A/Library"),
            ProjectModel(id=3, number="1000", name="Unknown drawings", drawings=None),
            ProjectModel(id=4, number="0001", name="No drawings", drawings=False),
        ])
        db.session.add_all([
            ProjectCaanModel(project_id=project_id, caan_id=1)
            for project_id in range(1, 5)
        ])
        db.session.commit()
    response = client.get("/caan_info/7110")
    assert response.status_code == 200
    assert "No associated projects found" not in response.text
    assert response.text.index(">6300-7A</a>") < response.text.index(">6300-11</a>")
    assert response.text.index(">6300-11</a>") < response.text.index(">1000</a>")
    assert response.text.index(">1000</a>") < response.text.index(">0001</a>")
    assert 'href="/project_info?project_id=2"' in response.text
    assert "View summary</a>" in response.text
    assert r"Z:\Archives\6300-7A\Library" in response.text
    assert "Not recorded in database" in response.text
    assert "&lt;b&gt;Earlier&lt;/b&gt;" in response.text
    assert "<b>Earlier</b>" not in response.text


def test_get_lookup_feedback_works_with_csrf_enabled(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.get("/caan_search?enter_caan=missing").status_code == 200
    assert client.get("/caan_search?enter_caan=7110").status_code == 302
    response = client.get("/caan_info/missing")
    assert response.status_code == 404
    assert 'name="csrf_token"' not in response.text
