"""Read-only contract schedule figures; recorded dates remain authoritative."""

from datetime import date, timedelta
import json

import plotly.graph_objects as go
from plotly.io import to_json


MILLISECONDS_PER_DAY = 86400000


def build_contract_timeline(contract, schedule: dict, today: date | None = None) -> dict | None:
    """Plot recorded schedule milestones and explicitly labeled duration references.

    Only the HTML page calls this helper. It consumes the existing reconciliation
    and segment decisions without changing the project-info API's schedule data.
    """
    start = schedule["ntp_start"]
    expected = schedule["expected_end"]
    actual = schedule["actual_completion"]
    original_days = contract.original_project_duration
    revised_days = contract.change_order_revised_duration
    original_end = (
        start + timedelta(days=original_days)
        if start is not None and original_days is not None and original_days >= 0 else None
    )
    duration_end = (
        start + timedelta(days=revised_days)
        if start is not None and revised_days is not None and revised_days >= 0 else None
    )
    if start is None and expected is None and actual is None:
        return None

    fig = go.Figure()
    dates = []
    rows = []
    if start is not None and schedule["bar_segments"]:
        rows.append("Duration")
        segment_start = start
        for segment in schedule["bar_segments"]:
            segment_end = segment_start + timedelta(days=segment["days"])
            # A zero-day extension has no width; its recorded value stays in the summary.
            if segment["days"] > 0:
                fig.add_trace(go.Bar(
                    orientation="h", y=[0], x=[segment["days"] * MILLISECONDS_PER_DAY],
                    base=[segment_start.isoformat()], width=0.45,
                    name=segment["label"],
                    marker_color="#d39e00" if segment["style"] == "change-order" else "#5f788a",
                    customdata=[[segment_start.isoformat(), segment_end.isoformat(), segment["days"]]],
                    hovertemplate=(
                        "%{fullData.name}<br>%{customdata[0]} – %{customdata[1]}"
                        "<br>%{customdata[2]} calendar days<extra></extra>"
                    ),
                ))
            dates.extend([segment_start, segment_end])
            segment_start = segment_end

    milestones = [
        (start, "NTP", "Notice to proceed (recorded)", "#5f788a", "circle"),
        (original_end, "Original end*", "Original end (NTP + original duration)", "#5f788a", "diamond-open"),
        (expected, "Expected end", "Current expected end (recorded)", "#9a7000", "diamond"),
    ]
    if duration_end is not None and duration_end != expected:
        milestones.append((duration_end, "Duration end*", "Current end (NTP + current duration)",
                           "#6c757d", "diamond-open"))
    milestones.append((actual, "Completed", "Actual completion (recorded)", "#287f5e", "square"))
    for value, row_label, label, color, symbol in milestones:
        if value is None:
            continue
        row = len(rows)
        rows.append(row_label)
        dates.append(value)
        fig.add_trace(go.Scatter(
            x=[value.isoformat()], y=[row], name=label, mode="markers",
            marker={"color": color, "size": 12, "symbol": symbol}, showlegend=False,
            hovertemplate=label + "<br>%{x|%b %d, %Y}<extra></extra>",
        ))

    # A termination record also closes the clock; absence of NOC alone does not mean active.
    today_marker = None
    if actual is None and contract.termination_date is None:
        today_marker = today if today is not None else date.today()
        dates.append(today_marker)
        fig.add_shape(type="line", x0=today_marker.isoformat(), x1=today_marker.isoformat(),
                      xref="x", yref="paper", y0=0, y1=1,
                      line={"color": "#a74036", "width": 1.5, "dash": "dot"})
        fig.add_annotation(x=today_marker.isoformat(), y=1.04, xref="x", yref="paper",
                           text=f"Today · {today_marker.isoformat()}", showarrow=False,
                           xanchor="right", yanchor="bottom", font={"color": "#a74036"})

    earliest, latest = min(dates), max(dates)
    padding = timedelta(days=max(3, (latest - earliest).days * 0.08))
    fig.update_layout(
        template="plotly_white", autosize=True, barmode="overlay", dragmode="zoom",
        margin={"l": 100, "r": 25, "t": 45, "b": 120},
        legend={"orientation": "h", "x": 0, "y": -0.3, "yanchor": "top", "font": {"size": 11}},
        hovermode="closest",
    )
    fig.update_xaxes(type="date", title_text="Calendar date", automargin=True,
                     range=[(earliest - padding).isoformat(), (latest + padding).isoformat()])
    fig.update_yaxes(tickmode="array", tickvals=list(range(len(rows))), ticktext=rows,
                     range=[len(rows) - 0.5, -0.6], fixedrange=True, zeroline=False,
                     showgrid=True, automargin=True, tickfont={"size": 11})
    return {
        "figure": json.loads(to_json(fig)),
        "original_end": original_end,
        "duration_end": duration_end if duration_end != expected else None,
        "today": today_marker,
    }
