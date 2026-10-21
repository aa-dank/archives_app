"""Regression coverage for calendar-day metrics and the interactive dashboard."""

from datetime import date, datetime, timedelta
from io import BytesIO
import json
import re

from flask import render_template
from openpyxl import load_workbook
import pandas as pd
from plotly.io import to_html
import pytest

from archives_application import create_app, db
from archives_application.models import ArchivedFileModel, UserModel
from archives_application.timekeeper import routes
from archives_application.timekeeper.charts import (
    PLOTLY_CDN_URL, aggregate_bars, build_daily_metrics, build_metrics_figure, dashboard_chart, date_bounds,
)
from archives_application.timekeeper.forms import TimeSheetForm


def records(*rows):
    """Make archive inputs from timestamp/MB pairs."""
    return pd.DataFrame([(timestamp, mb * 1024**2) for timestamp, mb in rows],
                        columns=["date_archived", "file_size"])


def test_calendar_averages_include_weekends_zeros_and_prior_history():
    source = records(("2026-10-01", 1000), ("2026-10-09", 7),
                     ("2026-10-10", 14), ("2026-10-13", 1000))
    daily = build_daily_metrics(source, date(2026, 10, 10), date(2026, 10, 12), 7)
    assert daily.index.tolist() == list(pd.date_range("2026-10-10", "2026-10-12"))
    assert daily.total_files.tolist() == [1, 0, 0]
    assert daily.volume_mb.tolist() == [14, 0, 0]
    assert daily.files_rolling_avg.tolist() == pytest.approx([2/7, 2/7, 2/7])
    assert daily.volume_rolling_avg.tolist() == [3, 3, 3]
    assert aggregate_bars(daily).volume_mb.sum() == 14


def test_first_archive_and_zero_byte_files_have_full_window_and_valid_volume_axis():
    daily = build_daily_metrics(records(("2026-10-10", 0)), date(2026, 10, 10), date(2026, 10, 11), 7)
    assert daily.files_rolling_avg.iloc[0] == pytest.approx(1/7)
    assert daily.volume_rolling_avg.tolist() == [0, 0]
    fig = build_metrics_figure(daily, aggregate_bars(daily), 7)
    assert list(fig.data[1].y) == [0, 0]
    assert fig.layout.yaxis2.autorange is True


@pytest.mark.parametrize("span,interval", [(90, 1), (91, 3), (270, 3), (271, 7)])
def test_bins_use_displayed_span_preserve_totals_and_keep_partial_last_bin(span, interval):
    start = date(2026, 1, 2)
    end = start + timedelta(days=span - 1)
    daily = build_daily_metrics(records(*[(day, 2) for day in pd.date_range(start, end)]), start, end, 7)
    bars = aggregate_bars(daily)
    assert len(bars) == (span + interval - 1) // interval
    assert bars.index[0].date() == start
    assert bars.period_end.iloc[-1].date() == end
    assert bars.total_files.sum() == span
    assert bars.volume_mb.sum() == span * 2
    assert bars.total_files.iloc[0] == interval
    assert bars.total_files.iloc[-1] == ((span - 1) % interval) + 1
    fig = build_metrics_figure(daily, bars, 7)
    assert len(fig.data[2].x) == len(fig.data[3].x) == span


def test_figure_uses_real_values_independent_axes_and_grouped_bars():
    source = records(*[(day, 20) for day in pd.date_range("2026-01-01", periods=91)])
    daily = build_daily_metrics(source, date(2026, 1, 1), date(2026, 4, 1), 7)
    fig = build_metrics_figure(daily, aggregate_bars(daily), 7)
    assert [trace.type for trace in fig.data] == ["bar", "bar", "scatter", "scatter"]
    assert [trace.yaxis for trace in fig.data] == ["y", "y2", "y", "y2"]
    assert max(fig.data[0].y) == 3
    assert max(fig.data[1].y) == 60
    assert fig.layout.yaxis.range is fig.layout.yaxis2.range is None
    assert fig.layout.yaxis.rangemode == fig.layout.yaxis2.rangemode == "tozero"
    assert fig.layout.xaxis.type == "date"
    assert fig.layout.barmode == "group"
    assert fig.data[0].offsetgroup != fig.data[1].offsetgroup
    assert fig.data[0].alignmentgroup == fig.data[1].alignmentgroup
    assert list(fig.data[0].customdata[0]) == ["2026-01-01", "2026-01-03"]


@pytest.mark.parametrize("source", [records(), records(("2026-10-09", 7))])
def test_empty_selected_interval_does_not_plot_history(source):
    chart = dashboard_chart(source, date(2026, 10, 10), date(2026, 10, 11), 7, "Activity", "activity")
    assert chart["figure"] is None


def test_date_bounds_include_end_day_and_reject_reversed_range():
    assert date_bounds(date(2026, 10, 10), date(2026, 10, 11)) == (
        datetime(2026, 10, 10), datetime(2026, 10, 12))
    with pytest.raises(ValueError):
        date_bounds(date(2026, 10, 11), date(2026, 10, 10))


class TestConfig:
    TESTING = True
    SECRET_KEY = "archiving-charts-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "archiving-charts-test-client"
    REDIS_URL = "redis://localhost:6379/0"
    WTF_CSRF_ENABLED = False
    ARCHIVES_LOCATION = "/srv/archives"
    USER_ARCHIVES_LOCATION = r"Z:\Archives"


@pytest.fixture()
def app(monkeypatch):
    """Use isolated archive records; swallowed dashboard exceptions fail the test."""
    app = create_app(TestConfig)

    def raise_error(**kwargs):
        raise kwargs["thrown_exception"]

    monkeypatch.setattr(routes.utils.FlaskAppUtils, "web_exception_subroutine", raise_error)
    with app.app_context():
        UserModel.__table__.create(db.engine)
        ArchivedFileModel.__table__.create(db.engine)
        db.session.add_all([
            UserModel(id=1, email="admin@example.test", first_name="Admin", roles="ADMIN"),
            UserModel(id=2, email="pat@example.test", first_name="Pat", roles="ARCHIVIST"),
            UserModel(id=3, email="user@example.test", first_name="User", roles="USER"),
        ])
        db.session.add_all([
            ArchivedFileModel(archivist_id=owner, date_archived=timestamp,
                              file_size=1024**2, destination_path=f"1000/{name}.pdf", filename=name)
            for owner, timestamp, name in [
                (2, datetime(2026, 10, 3), "outside-history"),
                (2, datetime(2026, 10, 9, 23, 59, 59, 999999), "history"),
                (2, datetime(2026, 10, 10), "start"),
                (2, datetime(2026, 10, 11, 23, 59, 59, 999999), "end"),
                (2, datetime(2026, 10, 12), "after"),
                (1, datetime(2026, 10, 10, 12), "other-archivist"),
            ]
        ])
        db.session.commit()
    yield app
    with app.app_context():
        db.session.remove()
        ArchivedFileModel.__table__.drop(db.engine)
        UserModel.__table__.drop(db.engine)


def login(client, user_id=2):
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True


def submit(client, **overrides):
    return client.post("/archiving_dashboard/2", data={
        "timesheet_begin": "2026-10-10", "timesheet_end": "2026-10-11",
        "rolling_avg_window": "7", "submit": "Submit", **overrides,
    })


def figures(response):
    return [json.loads(source) for source in re.findall(
        r'<script type="application/json"[^>]*>(.*?)</script>', response.text, re.S)]


def test_dashboard_chart_boundaries_shared_bundle_totals_and_no_temp_files(app, monkeypatch):
    def fail_temp_file(*args, **kwargs):
        raise AssertionError("Dashboard must not create chart files")

    monkeypatch.setattr(routes.utils.FlaskAppUtils, "create_temp_filepath", fail_temp_file)
    client = app.test_client()
    login(client)
    response = submit(client)
    assert response.status_code == 200
    collective, individual = figures(response)
    assert collective["data"][0]["y"] == [2, 1]
    assert individual["data"][0]["y"] == [1, 1]
    assert individual["data"][2]["y"] == pytest.approx([2/7, 3/7])
    assert b"archived (all time): 5" in response.data
    assert response.text.count(f'src="{PLOTLY_CDN_URL}"') == 1
    assert response.text.index(f'src="{PLOTLY_CDN_URL}"') < response.text.index('src="/static/archiving_charts.js"')
    assert "<img" not in response.text
    assert "Open image in new tab" not in response.text
    with client.session_transaction() as session:
        assert "temporary files" not in str(dict(session))


def test_export_uses_same_inclusive_dates_and_preserves_columns_and_paths(app):
    client = app.test_client()
    login(client)
    response = submit(client, export_spreadsheet="Export Spreadsheet")
    assert response.status_code == 200
    assert "20261010_to_20261011.xlsx" in response.headers["Content-Disposition"]
    rows = list(load_workbook(BytesIO(response.data)).active.values)
    headers = rows[0]
    assert not {"id", "archivist_id", "file_id", "document_date"}.intersection(headers)
    assert {row[headers.index("filename")] for row in rows[1:]} == {"start", "end"}
    assert all(row[headers.index("destination_path")].startswith("Z:\\Archives\\1000\\") for row in rows[1:])


@pytest.mark.parametrize("start,end", [("2026-10-13", "2026-10-14"), ("2026-12-01", "2026-12-02")])
def test_empty_range_has_messages_without_loading_plotly(app, start, end):
    client = app.test_client()
    login(client)
    response = submit(client, timesheet_begin=start, timesheet_end=end)
    assert response.status_code == 200
    assert response.text.count("No archiving activity in the selected date range.") == 2
    assert not figures(response)
    assert PLOTLY_CDN_URL not in response.text


def test_default_get_uses_31_displayed_days_and_includes_last_fractional_second(app, monkeypatch):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 10, 11, 12)

    monkeypatch.setattr(routes, "datetime", FixedDatetime)
    client = app.test_client()
    login(client)
    response = client.get("/archiving_dashboard/2")
    assert response.status_code == 200
    collective, individual = figures(response)
    assert len(individual["data"][0]["x"]) == 31
    assert individual["data"][0]["x"][0].startswith("2026-09-11")
    assert individual["data"][0]["x"][-1].startswith("2026-10-11")
    assert sum(individual["data"][0]["y"]) == 4
    assert sum(collective["data"][0]["y"]) == 5


def test_collective_chart_can_render_without_individual_activity(app):
    client = app.test_client()
    login(client, 1)
    response = client.post("/archiving_dashboard/3", data={
        "timesheet_begin": "2026-10-10", "timesheet_end": "2026-10-11", "submit": "Submit",
    })
    assert response.status_code == 200
    assert len(figures(response)) == 1
    assert response.text.count("No archiving activity in the selected date range.") == 1
    assert response.text.count(f'src="{PLOTLY_CDN_URL}"') == 1


def test_access_controls_and_missing_archivist(app):
    client = app.test_client()
    assert client.get("/archiving_dashboard/2").status_code == 302
    login(client, 3)
    assert client.get("/archiving_dashboard/2").status_code == 302
    login(client, 2)
    assert client.get("/archiving_dashboard/1").status_code == 401
    login(client, 1)
    assert client.get("/archiving_dashboard/9999").status_code == 404
    assert submit(client).status_code == 200


def test_figure_json_and_titles_cannot_break_out_of_script_or_heading(app):
    payload = '</script><script>alert("name")</script>&\u2028'
    chart = dashboard_chart(records(("2026-10-10", 2)), date(2026, 10, 10),
                            date(2026, 10, 11), 7, payload, "activity")
    chart["figure"]["layout"]["meta"] = payload
    with app.test_request_context():
        rendered = render_template("archivist_dashboard.html", form=TimeSheetForm(),
                                   archivist_name=payload, charts=[chart],
                                   plotly_js_url=PLOTLY_CDN_URL)
    assert payload not in rendered
    serialized = re.search(r'<script type="application/json"[^>]*>(.*?)</script>', rendered, re.S)[1]
    assert json.loads(serialized)["layout"]["meta"] == payload
    assert "NaN" not in serialized


def test_cdn_version_matches_plotlys_own_html_export_and_other_pages_do_not_load_it(app):
    reference_html = to_html({"data": [], "layout": {}}, include_plotlyjs="cdn")
    assert f'src="{PLOTLY_CDN_URL}"' in reference_html
    assert "latest" not in PLOTLY_CDN_URL
    response = app.test_client().get("/about")
    assert response.status_code == 200
    assert PLOTLY_CDN_URL not in response.text
