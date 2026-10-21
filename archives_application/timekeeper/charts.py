"""Calendar-day archiving metrics and interactive figures, independent of database IO."""

from datetime import date, datetime, time, timedelta
import json

import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs_version
from plotly.io import to_json
from plotly.subplots import make_subplots


PLOTLY_CDN_URL = f"https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"


def date_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Return midnight bounds for an inclusive date selection; the upper bound is exclusive."""
    if end < start:
        raise ValueError("End date must not precede start date.")
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def build_daily_metrics(
    records: pd.DataFrame, start: date, end: date, rolling_days: int
) -> pd.DataFrame:
    """Zero-fill calendar days, calculate trailing averages, and return only selected days.

    Records contain date_archived and file_size (bytes). Callers must fetch the
    preceding rolling_days - 1 days as well as the displayed interval. Dates
    without records contribute zero, including before the first archive event.
    """
    if rolling_days < 2:
        raise ValueError("Rolling average window must be greater than 1.")
    start_at, end_before = date_bounds(start, end)
    history_start = start_at - timedelta(days=rolling_days - 1)
    days = pd.date_range(history_start, end_before - timedelta(days=1), freq="D")
    if records.empty:
        daily = pd.DataFrame(0.0, index=days, columns=["total_files", "volume_mb"])
    else:
        work = records[["date_archived", "file_size"]].copy()
        work["day"] = pd.to_datetime(work["date_archived"]).dt.normalize()
        daily = work.groupby("day").agg(
            total_files=("file_size", "size"), volume_mb=("file_size", "sum")
        ).reindex(days, fill_value=0)
        daily["volume_mb"] = daily["volume_mb"] / 1024**2
    daily["total_files"] = daily["total_files"].astype(int)
    daily["files_rolling_avg"] = daily["total_files"].rolling(rolling_days).mean()
    daily["volume_rolling_avg"] = daily["volume_mb"].rolling(rolling_days).mean()
    return daily.loc[start_at:end_before - timedelta(days=1)].copy()


def aggregate_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """Sum selected days into 1/3/7-day bins anchored at the displayed start."""
    span_days = len(daily)
    bin_days = 1 if span_days <= 90 else 3 if span_days <= 270 else 7
    # Daily resampling starts at the first selected midnight.
    bars = daily[["total_files", "volume_mb"]].resample(f"{bin_days}D").sum()
    bars["period_end"] = [
        min(start + pd.Timedelta(days=bin_days - 1), daily.index[-1])
        for start in bars.index
    ]
    return bars


def build_metrics_figure(
    daily: pd.DataFrame, bars: pd.DataFrame, rolling_days: int
) -> go.Figure:
    """Build two grouped bar series and two daily lines on independent unit axes."""
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    periods = [
        [start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")]
        for start, end in zip(bars.index, bars["period_end"])
    ]
    # One alignment group and distinct offset groups also separate bars across y/y2.
    for column, label, color, secondary in (
        ("total_files", "Files (period total)", "#007988", False),
        ("volume_mb", "MB (period total)", "#fdc700", True),
    ):
        unit = "MB" if secondary else "files"
        number_format = ",.2f" if secondary else ",.0f"
        fig.add_trace(go.Bar(
            x=bars.index.to_pydatetime().tolist(), y=bars[column].tolist(),
            name=label, marker_color=color, offsetgroup=column, alignmentgroup="metrics",
            customdata=periods,
            hovertemplate=(
                "%{customdata[0]} – %{customdata[1]}<br>"
                f"Period total: %{{y:{number_format}}} {unit}<extra></extra>"
            ),
        ), secondary_y=secondary)
    for column, label, color, secondary in (
        ("files_rolling_avg", "Files", "#003c6c", False),
        ("volume_rolling_avg", "MB", "#f29813", True),
    ):
        fig.add_trace(go.Scatter(
            x=daily.index.to_pydatetime().tolist(), y=daily[column].tolist(),
            name=f"{label} ({rolling_days}-day daily average)", mode="lines",
            line={"color": color, "dash": "dash", "width": 2},
            hovertemplate=(
                "%{x|%Y-%m-%d}<br>"
                f"{rolling_days}-calendar-day average: %{{y:,.2f}} {label}/day<extra></extra>"
            ),
        ), secondary_y=secondary)
    fig.update_layout(
        template="plotly_white", autosize=True, barmode="group", bargap=0.2,
        hovermode="closest", dragmode="zoom",
        margin={"l": 65, "r": 65, "t": 20, "b": 135},
        legend={"orientation": "h", "x": 0, "y": -0.22, "yanchor": "top"},
    )
    fig.update_xaxes(type="date", title_text="Archive date", automargin=True)
    fig.update_yaxes(title_text="Files", rangemode="tozero", autorange=True,
                     automargin=True, secondary_y=False)
    fig.update_yaxes(title_text="MB archived", rangemode="tozero", autorange=True,
                     automargin=True, showgrid=False, secondary_y=True)
    return fig


def dashboard_chart(
    records: pd.DataFrame, start: date, end: date, rolling_days: int, title: str, element_id: str
) -> dict:
    """Return safe template data, or an explicit empty state for the displayed range."""
    daily = build_daily_metrics(records, start, end, rolling_days)
    figure = None
    if daily["total_files"].sum():
        fig = build_metrics_figure(daily, aggregate_bars(daily), rolling_days)
        # Plotly normalizes NumPy/datetime values; Jinja tojson handles HTML escaping.
        figure = json.loads(to_json(fig))
    return {"id": element_id, "title": title, "figure": figure}
