"""Contract timeline dates, fallbacks, embedding, and API isolation."""

from datetime import date, timedelta
import json
import re
from types import SimpleNamespace

import pytest

from archives_application import create_app, db
from archives_application.models import CAANModel, ContractModel, ProjectCaanModel, ProjectModel, UserModel
from archives_application.plotting import PLOTLY_CDN_URL
from archives_application.project_tools import project_info as service
from archives_application.project_tools.contract_timeline import build_contract_timeline, MILLISECONDS_PER_DAY


START = date(2026, 9, 1)
TODAY = date(2026, 11, 2)


def contract_values(**overrides):
    return {
        "ntp_start_date": START, "original_project_duration": 60,
        "change_order_time_total": 15, "change_order_revised_duration": 75,
        "change_order_revised_expected_end": START + timedelta(days=75),
        "noc_completion_date": None, "termination_date": None, **overrides,
    }


def timeline(**overrides):
    contract = SimpleNamespace(**contract_values(**overrides))
    return build_contract_timeline(contract, service.contract_schedule(contract), today=TODAY)


def bars(result):
    return [trace for trace in result["figure"]["data"] if trace["type"] == "bar"]


def test_original_and_extension_have_real_date_bases_and_calendar_day_lengths():
    result = timeline()
    original, extension = bars(result)
    assert original["base"] == ["2026-09-01"]
    assert original["x"] == [60 * MILLISECONDS_PER_DAY]
    assert extension["base"] == ["2026-10-31"]
    assert extension["x"] == [15 * MILLISECONDS_PER_DAY]
    assert extension["customdata"] == [["2026-10-31", "2026-11-15", 15]]
    assert result["original_end"] == date(2026, 10, 31)
    assert result["duration_end"] is None
    assert result["figure"]["layout"]["xaxis"]["type"] == "date"
    today = result["figure"]["layout"]["shapes"][0]
    assert today["x0"] == today["x1"] == TODAY.isoformat()


@pytest.mark.parametrize("overrides", [
    {"change_order_time_total": -15, "change_order_revised_duration": 45,
     "change_order_revised_expected_end": START + timedelta(days=45)},
    {"change_order_revised_duration": 90,
     "change_order_revised_expected_end": START + timedelta(days=90)},
    {"original_project_duration": None},
])
def test_negative_missing_or_inconsistent_components_use_current_duration_fallback(overrides):
    result = timeline(**overrides)
    assert len(bars(result)) == 1
    assert bars(result)[0]["name"] == "Current contractual duration"
    assert bars(result)[0]["x"] == [contract_values(**overrides)["change_order_revised_duration"] * MILLISECONDS_PER_DAY]


def test_recorded_end_stays_distinct_from_duration_derived_end():
    recorded = START + timedelta(days=85)
    result = timeline(change_order_revised_expected_end=recorded)
    assert result["duration_end"] == START + timedelta(days=75)
    traces = {trace["name"]: trace for trace in result["figure"]["data"]}
    assert traces["Current expected end (recorded)"]["x"] == [recorded.isoformat()]
    assert traces["Current end (NTP + current duration)"]["x"] == ["2026-11-15"]
    assert bars(result)[1]["customdata"][0][1] == "2026-11-15"


def test_coincident_milestones_remain_on_distinct_rows_and_zero_extension_is_omitted():
    result = timeline(change_order_time_total=0, change_order_revised_duration=60,
                      change_order_revised_expected_end=START + timedelta(days=60),
                      noc_completion_date=START + timedelta(days=60))
    assert len(bars(result)) == 1
    markers = [trace for trace in result["figure"]["data"] if trace["type"] == "scatter"]
    endpoints = [trace for trace in markers if trace["x"] == ["2026-10-31"]]
    assert len(endpoints) == 3
    assert len({trace["y"][0] for trace in endpoints}) == 3
    assert result["today"] is None
    assert "shapes" not in result["figure"]["layout"]


def test_termination_without_noc_completion_does_not_show_today():
    result = timeline(termination_date=date(2026, 10, 1))
    assert result["today"] is None


def test_partial_dates_show_only_supported_milestones_and_no_invented_start():
    result = timeline(ntp_start_date=None)
    assert not bars(result)
    assert result["original_end"] is None
    assert [trace["name"] for trace in result["figure"]["data"]] == ["Current expected end (recorded)"]
    result = timeline(change_order_revised_expected_end=None)
    assert result["duration_end"] == START + timedelta(days=75)
    assert all(trace["name"] != "Current expected end (recorded)" for trace in result["figure"]["data"])


def test_completely_missing_dates_do_not_create_a_today_only_chart():
    assert timeline(ntp_start_date=None, change_order_revised_expected_end=None) is None


class TestConfig:
    TESTING = True
    SECRET_KEY = "contract-timeline-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "contract-timeline-test"
    KEY_VALUE_STORE_URL = "redis://localhost:6379/0"


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    tables = [UserModel.__table__, ProjectModel.__table__, CAANModel.__table__,
              ProjectCaanModel.__table__, ContractModel.__table__]
    with app.app_context():
        db.engine.raw_connection().create_function(
            "btrim", -1, lambda value, chars=None: value.strip(chars) if value else value,
            deterministic=True,
        )
        for table in tables:
            table.create(db.engine)
        db.session.add(UserModel(id=1, email="viewer@example.test", first_name="Viewer", roles="USER"))
        db.session.add(ProjectModel(id=1, number="1000", name="Contract timeline", closed=False))
        db.session.add(ContractModel(id=1, project_id=1, contract_number="C-1", **contract_values()))
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        for table in reversed(tables):
            table.drop(db.engine)


def page_figure(response):
    source = re.search(r'<script type="application/json" data-plotly-chart[^>]*>(.*?)</script>', response.text, re.S)
    return json.loads(source[1]) if source else None


def test_page_embeds_one_cdn_script_before_initializer_and_preserves_text_summary(app):
    response = app.test_client().get("/project_info?project_id=1")
    assert response.status_code == 200
    assert page_figure(response) is not None
    assert response.text.count(f'src="{PLOTLY_CDN_URL}"') == 1
    assert response.text.index(f'src="{PLOTLY_CDN_URL}"') < response.text.index('src="/static/plotly_charts.js"')
    assert 'class="contract-schedule-bar"' not in response.text
    assert "60 calendar days" in response.text and "+15 calendar days" in response.text
    assert "Original end (NTP + original duration)" in response.text
    assert "Actual Completion Date:" in response.text
    assert "Duration-derived reference dates" in response.text
    assert "NaN" not in response.text


def test_inconsistent_dates_and_components_keep_original_warnings(app):
    with app.app_context():
        contract = db.session.get(ContractModel, 1)
        contract.change_order_time_total = -15
        contract.change_order_revised_expected_end = START + timedelta(days=85)
        db.session.commit()
    response = app.test_client().get("/project_info?project_id=1")
    assert "duration do not reconcile" in response.text
    assert "expected end does not match" in response.text
    assert "Nov 25, 2026" in response.text
    assert "Nov 15, 2026" in response.text
    figure = page_figure(response)
    assert [trace["name"] for trace in figure["data"] if trace["type"] == "bar"] == ["Current contractual duration"]


@pytest.mark.parametrize("state", ["no_dates", "no_contracts", "multiple_contracts"])
def test_no_figure_or_cdn_for_unavailable_or_non_single_contract_schedule(app, state):
    with app.app_context():
        contract = db.session.get(ContractModel, 1)
        if state == "no_dates":
            contract.ntp_start_date = None
            contract.change_order_revised_expected_end = None
        elif state == "no_contracts":
            db.session.delete(contract)
        else:
            db.session.add(ContractModel(id=2, project_id=1, contract_number="C-2"))
        db.session.commit()
    response = app.test_client().get("/project_info?project_id=1")
    assert response.status_code == 200
    assert page_figure(response) is None
    assert PLOTLY_CDN_URL not in response.text
    assert 'src="/static/plotly_charts.js"' not in response.text


def test_api_retains_recorded_schedule_contract_without_building_a_figure(app, monkeypatch):
    def fail_figure(*args, **kwargs):
        raise AssertionError("Project API must not build a chart")

    monkeypatch.setattr(service, "build_contract_timeline", fail_figure)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    response = client.get("/api/project_info?project_id=1")
    assert response.status_code == 200
    schedule = response.json["contracts"][0]["schedule"]
    assert schedule["revised_expected_end_date"] == "2026-11-15"
    assert schedule["original_duration_days"] == 60
    assert schedule["duration_reconciles"] is True
    assert schedule["expected_end_reconciles"] is True
    assert "figure" not in response.text and "plotly" not in response.text.lower()
