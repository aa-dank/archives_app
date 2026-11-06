"""Read-only contract schedule figures; recorded dates remain authoritative."""

from datetime import date, timedelta
import json

import plotly.graph_objects as go
from plotly.io import to_json

from .contract_dates import recorded_contract_dates


MILLISECONDS_PER_DAY = 86400000


def build_contract_timeline(
    contract, schedule: dict, today: date | None = None, *, recorded_dates: list[dict] | None = None,
) -> dict | None:
    """Annotate one date axis with schedule milestones and duration references.

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
    recorded_dates = recorded_contract_dates(contract) if recorded_dates is None else recorded_dates
    if not recorded_dates:
        return None

    fig = go.Figure()
    dates = [event["date_value"] for event in recorded_dates]
    first_recorded_date = min(dates)
    before_ntp_days = (start - first_recorded_date).days if start is not None else None
    if before_ntp_days:
        fig.add_trace(go.Bar(
            orientation="h", y=[0], x=[before_ntp_days * MILLISECONDS_PER_DAY],
            base=[first_recorded_date.isoformat()], width=6,
            name="Before NTP", marker_color="#9b8bb0",
            customdata=[[first_recorded_date.isoformat(), start.isoformat(), before_ntp_days]],
            hovertemplate=("Before NTP<br>%{customdata[0]} – %{customdata[1]}"
                           "<br>%{customdata[2]} calendar days<extra></extra>"),
        ))
    if start is not None and schedule["bar_segments"]:
        segment_start = start
        for segment in schedule["bar_segments"]:
            segment_end = segment_start + timedelta(days=segment["days"])
            # A zero-day extension has no width; its recorded value stays in the summary.
            if segment["days"] > 0:
                fig.add_trace(go.Bar(
                    orientation="h", y=[0], x=[segment["days"] * MILLISECONDS_PER_DAY],
                    base=[segment_start.isoformat()], width=6,
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
        (event["date_value"], event["short_label"], event["label"], event["color"], event["symbol"])
        for event in recorded_dates
    ]
    milestones.append((original_end, "Original end (calc.)", "Original end (NTP + original duration)",
                       "#5f788a", "diamond-open"))
    if duration_end is not None and duration_end != expected:
        milestones.append((duration_end, "Revised end (calc.)", "Current end (NTP + current duration)",
                           "#6c757d", "diamond-open"))
    # Put every event on the same date axis. Coincident dates share one callout.
    today_marker = None
    if actual is None and contract.termination_date is None:
        current_day = today if today is not None else date.today()
        if current_day >= first_recorded_date:
            today_marker = current_day
            milestones.append((today_marker, "Today", "Today", "#a74036", "line-ns"))

    events_by_date = {}
    for value, short_label, label, color, symbol in milestones:
        if value is not None:
            dates.append(value)
            events_by_date.setdefault(value, []).append((short_label, label, color, symbol))

    earliest, latest = min(dates), max(dates)
    padding = timedelta(days=max(3, (latest - earliest).days * 0.08))
    range_start, range_end = earliest - padding, latest + padding
    span = (range_end - range_start).days
    lanes = []
    lane_heights = []
    callouts = []
    milestone_colors = {value.isoformat(): events[-1][2] for value, events in events_by_date.items()}
    for value, events in sorted(events_by_date.items()):
        color = milestone_colors[value.isoformat()]
        fig.add_trace(go.Scatter(
            x=[value.isoformat()], y=[0], name=" / ".join(event[1] for event in events),
            mode="markers", marker={"color": color, "size": 13, "symbol": events[-1][3]},
            showlegend=False,
            hovertemplate=("<b>%{x|%b %d, %Y}</b><br>"
                           + "<br>".join(event[1] for event in events) + "<extra></extra>"),
        ))
        position = (value - range_start).days / span
        anchor = "left" if position < 0.2 else "right" if position > 0.8 else "center"
        # Reserve label space for a narrow phone viewport as well as desktop.
        # Each lane can hold several callouts when their horizontal extents fit.
        text_width = max(95, max(len(line) for event in events for line in event[0].split("<br>")) * 6.5) / 260
        left = (position if anchor == "left" else position - text_width
                if anchor == "right" else position - text_width / 2)
        right = left + text_width
        lane = next((index for index, intervals in enumerate(lanes)
                     if all(right + 0.04 < start or left > end + 0.04 for start, end in intervals)), len(lanes))
        if lane == len(lanes):
            lanes.append([])
            lane_heights.append(0)
        lanes[lane].append((left, right))
        text_lines = sum(event[0].count("<br>") + 1 for event in events) + 1
        lane_heights[lane] = max(lane_heights[lane], text_lines * 15 + 6)
        callouts.append((value, events, color, anchor, lane))

    # Account for multi-event callout height as well as horizontal label spacing.
    above, below = 25, 55
    offsets = []
    for lane, label_height in enumerate(lane_heights):
        if lane % 2 == 0:
            offsets.append(-(above + label_height / 2))
            above += label_height + 18
        else:
            offsets.append(below + label_height / 2)
            below += label_height + 18
    for value, events, color, anchor, lane in callouts:
        fig.add_annotation(
            x=value.isoformat(), y=0, xref="x", yref="y",
            text="<br>".join(event[0] for event in events) + f"<br><b>{value:%b %d, %Y}</b>",
            showarrow=True, arrowhead=0, arrowwidth=1, arrowcolor=color,
            ax=0, ay=offsets[lane], axref="pixel", ayref="pixel", xanchor=anchor,
            font={"size": 11, "color": color}, bgcolor="rgba(255,255,255,0.95)", borderpad=3,
        )

    # Keep the figure compact for ordinary schedules, with space for dense callouts.
    above, below = max(80, above), max(80, below)
    bottom_margin = 30
    height = above + below + 20 + bottom_margin
    fig.update_layout(
        template="plotly_white", autosize=True, barmode="overlay", dragmode="zoom",
        margin={"l": 25, "r": 25, "t": 20, "b": bottom_margin},
        showlegend=False,
        hovermode="closest",
    )
    fig.update_xaxes(type="date", anchor="free", position=below / (above + below), showline=True,
                     linecolor="#cad4dc", layer="below traces", ticks="outside",
                     showgrid=False, zeroline=False, automargin=True,
                     range=[range_start.isoformat(), range_end.isoformat()])
    fig.update_yaxes(visible=False, range=[-below, above], fixedrange=True)
    return {
        "figure": json.loads(to_json(fig)),
        "height": height,
        "original_end": original_end,
        "duration_end": duration_end if duration_end != expected else None,
        "today": today_marker,
        "first_recorded_date": first_recorded_date,
        "before_ntp_days": before_ntp_days,
        "milestone_colors": milestone_colors,
    }


def contract_duration_rows(contract, timeline: dict | None) -> list[dict]:
    """Build the duration summary and key from recorded values and actual bar traces."""
    periods = []
    if timeline is not None:
        periods.append(("Before NTP", timeline["before_ntp_days"],
                        "From earliest recorded contract date" if timeline["before_ntp_days"] is not None
                        else "NTP not recorded", False, False))
    for label, field, signed, total in (
        ("Original contract duration", "original_project_duration", False, False),
        ("Approved change-order time", "change_order_time_total", True, False),
        ("Current contractual duration", "change_order_revised_duration", False, True),
    ):
        value = getattr(contract, field)
        if value is not None:
            periods.append((label, value, None, signed, total))
    traces = timeline["figure"]["data"] if timeline is not None else []
    rows = []
    for label, days, note, signed, total in periods:
        index = next((i for i, trace in enumerate(traces)
                      if trace["type"] == "bar" and trace["name"] == label), None)
        rows.append({
            "label": label, "days": days,
            "display_days": (f"{days:+,}" if signed and days > 0 else f"{days:,}")
                            if days is not None else "Unavailable",
            "note": note, "total": total, "trace_index": index,
            "color": traces[index]["marker"]["color"] if index is not None else None,
        })
    return rows
