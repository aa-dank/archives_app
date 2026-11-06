"""Contract timeline dates, fallbacks, embedding, and API isolation."""

from datetime import date, timedelta
import json
import re
from types import SimpleNamespace

import pytest
from sqlalchemy import Date

from archives_application import create_app, db
from archives_application.models import CAANModel, ContractModel, ProjectCaanModel, ProjectModel, UserModel
from archives_application.plotting import PLOTLY_CDN_URL
from archives_application.project_tools import project_info as service
from archives_application.project_tools import contract_timeline as timeline_module
from archives_application.project_tools.contract_dates import (
    CONTRACT_DATE_FIELDS, recorded_contract_dates,
)
from archives_application.project_tools.contract_timeline import (
    build_contract_timeline, prepare_contract_timeline, MILLISECONDS_PER_DAY,
)


START = date(2026, 9, 1)
TODAY = date(2026, 11, 2)


def contract_values(**overrides):
    return {
        **{field: None for field, *_ in CONTRACT_DATE_FIELDS},
        "ntp_start_date": START, "original_project_duration": 60,
        "change_order_time_total": 15, "change_order_revised_duration": 75,
        "change_order_revised_expected_end": START + timedelta(days=75),
        "noc_completion_date": None, "termination_date": None, **overrides,
    }


def timeline_data(**overrides):
    contract = SimpleNamespace(**contract_values(**overrides))
    return prepare_contract_timeline(contract, service.contract_schedule(contract), today=TODAY)


def timeline(**overrides):
    return build_contract_timeline(timeline_data(**overrides))


def bars(result):
    return [trace for trace in result["figure"]["data"] if trace["type"] == "bar"]


def test_tables_prepare_without_constructing_a_plotly_figure(monkeypatch):
    def fail_figure(*args, **kwargs):
        raise AssertionError("Preparing table data must not construct a figure")

    monkeypatch.setattr(timeline_module.go, "Figure", fail_figure)
    data = timeline_data(bid_date=START - timedelta(days=30))
    assert [row["display_days"] for row in data["duration_rows"]] == ["30", "60", "+15", "75"]
    assert [row["trace_index"] for row in data["duration_rows"]] == [0, 1, 2, None]
    calculated = [row for row in data["date_rows"] if row["calculation"]]
    assert [(row["label"], row["iso_date"]) for row in calculated] == [
        ("Original end (calculated)", "2026-10-31"),
    ]


def test_duration_controls_keep_their_segments_when_display_labels_are_duplicated():
    data = timeline_data()
    for segment in data["segments"]:
        segment["label"] = "Duration"
    figure = build_contract_timeline(data)
    keyed_rows = [row for row in data["duration_rows"] if row["trace_index"] is not None]
    assert [row["id"] for row in keyed_rows] == ["original_project_duration", "change_order_time_total"]
    for row in keyed_rows:
        trace = figure["figure"]["data"][row["trace_index"]]
        assert trace["name"] == "Duration"
        assert trace["x"] == [row["days"] * MILLISECONDS_PER_DAY]
        assert trace["marker"]["color"] == row["color"]


def test_original_and_extension_have_real_date_bases_and_calendar_day_lengths():
    result = timeline()
    original, extension = bars(result)
    assert original["base"] == ["2026-09-01"]
    assert original["x"] == [60 * MILLISECONDS_PER_DAY]
    assert extension["base"] == ["2026-10-31"]
    assert extension["x"] == [15 * MILLISECONDS_PER_DAY]
    assert extension["customdata"] == [["2026-10-31", "2026-11-15", 15]]
    calculated = [row for row in timeline_data()["date_rows"] if row["calculation"]]
    assert [(row["label"], row["date_value"]) for row in calculated] == [
        ("Original end (calculated)", date(2026, 10, 31)),
    ]
    assert result["figure"]["layout"]["xaxis"]["type"] == "date"
    today = next(note for note in result["figure"]["layout"]["annotations"] if note["text"].startswith("Today"))
    assert today["x"] == TODAY.isoformat()
    assert today["y"] == 0
    assert result["figure"]["layout"]["yaxis"]["visible"] is False
    assert all(trace["y"] == [0] for trace in result["figure"]["data"])


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
    traces = {trace["name"]: trace for trace in result["figure"]["data"]}
    assert traces["Revised expected completion date"]["x"] == [recorded.isoformat()]
    assert traces["Current end (NTP + current duration)"]["x"] == ["2026-11-15"]
    assert bars(result)[1]["customdata"][0][1] == "2026-11-15"


def test_coincident_milestones_share_callout_and_zero_extension_is_omitted():
    values = dict(change_order_time_total=0, change_order_revised_duration=60,
                  change_order_revised_expected_end=START + timedelta(days=60),
                  noc_completion_date=START + timedelta(days=60))
    result = timeline(**values)
    assert len(bars(result)) == 1
    markers = [trace for trace in result["figure"]["data"] if trace["type"] == "scatter"]
    endpoints = [trace for trace in markers if trace["x"] == ["2026-10-31"]]
    assert len(endpoints) == 1
    assert endpoints[0]["y"] == [0]
    assert all(label in endpoints[0]["hovertemplate"] for label in (
        "Original end", "Revised expected completion date", "Actual completion",
    ))
    callouts = [note for note in result["figure"]["layout"]["annotations"] if note["x"] == "2026-10-31"]
    assert len(callouts) == 1
    assert all(label in callouts[0]["text"] for label in ("Original end (calc.)", "Revised expected<br>completion date", "Completed"))
    rows = timeline_data(**values)["date_rows"]
    coincident = [row for row in rows if row["iso_date"] == "2026-10-31"]
    assert len(coincident) == 3
    assert all(row["marker_color"] == endpoints[0]["marker"]["color"] for row in coincident)
    assert all("Today" not in trace["name"] for trace in result["figure"]["data"])
    assert "shapes" not in result["figure"]["layout"]


def test_termination_without_noc_completion_does_not_show_today():
    result = timeline(termination_date=date(2026, 10, 1))
    assert all("Today" not in trace["name"] for trace in result["figure"]["data"])


def test_partial_dates_show_only_supported_milestones_and_no_invented_start():
    result = timeline(ntp_start_date=None)
    assert not bars(result)
    assert not any(row["calculation"] for row in timeline_data(ntp_start_date=None)["date_rows"])
    assert {trace["name"] for trace in result["figure"]["data"]} == {"Revised expected completion date"}
    result = timeline(change_order_revised_expected_end=None)
    assert all(trace["name"] != "Revised expected completion date" for trace in result["figure"]["data"])


def test_completely_missing_dates_do_not_create_a_today_only_chart():
    assert timeline(ntp_start_date=None, change_order_revised_expected_end=None) is None


def test_shared_event_list_covers_every_contract_date_field_and_preserves_ties():
    date_fields = {column.name for column in ContractModel.__table__.columns if isinstance(column.type, Date)}
    assert {field for field, *_ in CONTRACT_DATE_FIELDS} == date_fields
    contract = SimpleNamespace(**{field: START for field in date_fields})
    events = recorded_contract_dates(contract)
    assert len(events) == len(date_fields) == 10
    assert {event["source_field"] for event in events} == date_fields
    assert all(event["date_value"] == START for event in events)


def test_timeline_includes_every_recorded_date_in_chronological_range():
    values = {field: START + timedelta(days=index - 5) for index, (field, *_) in enumerate(CONTRACT_DATE_FIELDS)}
    contract = SimpleNamespace(**contract_values(**values))
    events = recorded_contract_dates(contract)
    data = prepare_contract_timeline(contract, service.contract_schedule(contract), today=TODAY)
    result = build_contract_timeline(data)
    markers = [trace for trace in result["figure"]["data"] if trace["type"] == "scatter"]
    for event in events:
        assert any(trace["x"] == [event["iso_date"]] and event["label"] in trace["hovertemplate"] for trace in markers)
    assert data["first_recorded_date"] == min(values.values())
    assert date.fromisoformat(result["figure"]["layout"]["xaxis"]["range"][0]) <= min(values.values())


def test_before_ntp_interval_starts_at_earliest_recorded_date():
    result = timeline(bid_date=START - timedelta(days=180), contract_date=START - timedelta(days=120))
    before = bars(result)[0]
    assert before["name"] == "Before NTP"
    assert before["base"] == [(START - timedelta(days=180)).isoformat()]
    assert before["x"] == [180 * MILLISECONDS_PER_DAY]
    assert before["customdata"][0][1] == START.isoformat()
    before_row = timeline_data(bid_date=START - timedelta(days=180))["duration_rows"][0]
    assert before_row["days"] == 180


def test_history_only_still_plots_without_ntp_and_cannot_invent_before_ntp_duration():
    result = timeline(ntp_start_date=None, change_order_revised_expected_end=None,
                      bid_date=START - timedelta(days=100), contract_date=START - timedelta(days=50))
    assert result is not None
    data = timeline_data(ntp_start_date=None, change_order_revised_expected_end=None,
                         bid_date=START - timedelta(days=100), contract_date=START - timedelta(days=50))
    assert data["duration_rows"][0]["display_days"] == "Unavailable"
    assert not bars(result)
    assert any(trace["name"] == "Bid" for trace in result["figure"]["data"])


def test_today_does_not_extend_the_timeline_before_future_contract_records():
    result = timeline(ntp_start_date=None, change_order_revised_expected_end=None,
                      bid_date=TODAY + timedelta(days=60))
    assert all("Today" not in trace["name"] for trace in result["figure"]["data"])


@pytest.mark.parametrize("overrides,colored_labels", [
    ({}, ["Original contract duration", "Approved change-order time"]),
    ({"change_order_time_total": 0, "change_order_revised_duration": 60,
      "change_order_revised_expected_end": START + timedelta(days=60)}, ["Original contract duration"]),
    ({"change_order_time_total": -15, "change_order_revised_duration": 45,
      "change_order_revised_expected_end": START + timedelta(days=45)}, ["Current contractual duration"]),
])
def test_duration_key_colors_only_plotted_segments_and_keeps_recorded_values(overrides, colored_labels):
    contract = SimpleNamespace(**contract_values(**overrides))
    data = timeline_data(**overrides)
    figure = build_contract_timeline(data)
    rows = data["duration_rows"]
    assert [row["label"] for row in rows if row["color"]] == colored_labels
    assert [row["days"] for row in rows] == [
        0, contract.original_project_duration, contract.change_order_time_total,
        contract.change_order_revised_duration,
    ]
    for row in rows:
        if row["trace_index"] is not None:
            trace = figure["figure"]["data"][row["trace_index"]]
            assert trace["type"] == "bar" and trace["name"] == row["label"]
            assert trace["marker"]["color"] == row["color"]
    assert figure["figure"]["layout"]["showlegend"] is False


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
    key = re.search(r'<table[^>]*id="contract-duration-key"[^>]*>(.*?)</table>', response.text, re.S)[1]
    assert "Calendar days" in key
    assert '<td class="text-right">60</td>' in key and '<td class="text-right">+15</td>' in key
    assert "contract-schedule-components" not in response.text
    assert page_figure(response)["layout"]["showlegend"] is False
    assert "Original end (calculated)" in response.text
    assert "NTP + original duration" in response.text
    assert "Actual Completion Date:" in response.text
    assert "Duration-derived reference dates" not in response.text
    assert "contract-schedule-references" not in response.text
    assert "NaN" not in response.text


def test_inconsistent_dates_and_components_keep_original_warnings(app):
    with app.app_context():
        contract = db.session.get(ContractModel, 1)
        contract.change_order_time_total = -15
        contract.change_order_revised_expected_end = START + timedelta(days=85)
        db.session.commit()
    response = app.test_client().get("/project_info?project_id=1")
    assert "duration do not reconcile" in response.text
    assert "expected completion date does not match" in response.text
    assert "Nov 25, 2026" in response.text
    assert "Nov 15, 2026" in response.text
    assert "Revised end (calculated)" in response.text
    assert "NTP + current duration" in response.text
    figure = page_figure(response)
    assert [trace["name"] for trace in figure["data"] if trace["type"] == "bar"] == ["Current contractual duration"]


def test_all_recorded_dates_appear_in_table_before_plot_with_matching_markers(app):
    values = {field: START + timedelta(days=index - 5) for index, (field, *_) in enumerate(CONTRACT_DATE_FIELDS)}
    with app.app_context():
        contract = db.session.get(ContractModel, 1)
        for field, value in values.items():
            setattr(contract, field, value)
        db.session.commit()
    response = app.test_client().get("/project_info?project_id=1")
    assert response.status_code == 200
    table = re.search(r'<table[^>]*id="contract-dates-table"[^>]*>(.*?)</table>', response.text, re.S)[1]
    calculated = [values["ntp_start_date"] + timedelta(days=days) for days in (60, 75)]
    assert re.findall(r'<time datetime="([^"]+)"', table) == sorted(
        value.isoformat() for value in [*values.values(), *calculated]
    )
    assert "Original end (calculated)" in table and "Revised end (calculated)" in table
    figure = page_figure(response)
    for row in re.findall(r'<tr>(.*?)</tr>', table, re.S):
        timestamp = re.search(r'<time datetime="([^"]+)"', row)
        if timestamp:
            marker = next(trace for trace in figure["data"]
                          if trace["type"] == "scatter" and trace["x"] == [timestamp[1]])
            assert f'background-color: {marker["marker"]["color"]};' in row
    for field, label, *_ in CONTRACT_DATE_FIELDS:
        assert label in table
        assert any(trace.get("x") == [values[field].isoformat()] and label in trace.get("hovertemplate", "")
                   for trace in figure["data"])
    assert response.text.index('id="contract-dates-table"') < response.text.index('id="contract-timeline"')
    assert 'other-contract-dates-heading' not in response.text
    key = re.search(r'<table[^>]*id="contract-duration-key"[^>]*>(.*?)</table>', response.text, re.S)[1]
    assert "Before NTP" in key and '<td class="text-right">2</td>' in key
    assert "Before NTP:</strong>" not in response.text


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
    monkeypatch.setattr(service, "prepare_contract_timeline", fail_figure)
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
