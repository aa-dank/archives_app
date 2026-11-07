"""Read-only contract schedule figures; recorded dates remain authoritative."""

from datetime import date, timedelta
import json

import plotly.graph_objects as go
from plotly.io import to_json

from .contract_dates import prepare_contract_dates


MILLISECONDS_PER_DAY = 86400000


def prepare_contract_timeline(contract, schedule: dict, today: date | None = None) -> dict:
    """Prepare shared table/figure data without constructing or inspecting a figure."""
    data = prepare_contract_dates(contract, today)
    rows = []
    if data["first_recorded_date"] is not None:
        rows.append({
            "id": "before_ntp", "label": "Before NTP", "days": data["before_ntp_days"],
            "note": "From earliest recorded contract date" if data["before_ntp_days"] is not None
                    else "NTP not recorded", "total": False,
        })
    for field, label, signed, total in (
        ("original_project_duration", "Original contract duration", False, False),
        ("change_order_time_total", "Approved change-order time", True, False),
        ("change_order_revised_duration", "Current contractual duration", False, True),
    ):
        days = getattr(contract, field)
        if days is not None:
            rows.append({"id": field, "label": label, "days": days, "note": None,
                         "signed": signed, "total": total})

    original = contract.original_project_duration
    change = contract.change_order_time_total
    revised = contract.change_order_revised_duration
    can_segment = (
        original is not None and change is not None and revised is not None
        and original >= 0 and change >= 0 and revised > 0 and schedule["duration_reconciled"]
    )
    plotted = (
        {"original_project_duration", "change_order_time_total"}
        if can_segment else {"change_order_revised_duration"}
    )
    segments = []
    segment_start = contract.ntp_start_date
    for row in rows:
        days = row["days"]
        row["display_days"] = (
            f"{days:+,}" if row.get("signed") and days > 0 else f"{days:,}"
        ) if days is not None else "Unavailable"
        row.update(trace_index=None, color=None)
        if segment_start is None or days is None or days <= 0:
            continue
        if row["id"] == "before_ntp":
            start = data["first_recorded_date"]
            end = segment_start
            color = "#9b8bb0"
        elif row["id"] in plotted:
            start = segment_start
            end = start + timedelta(days=days)
            color = "#d39e00" if row["id"] == "change_order_time_total" else "#5f788a"
            segment_start = end
        else:
            continue
        row.update(trace_index=len(segments), color=color)
        segments.append({"id": row["id"], "label": row["label"], "days": days,
                         "start": start, "end": end, "color": color})
    return {**data, "segments": segments, "duration_rows": rows}


def build_contract_timeline(data: dict) -> dict | None:
    """Render prepared milestones and duration segments on a single date axis."""
    if not data["milestones"]:
        return None
    fig = go.Figure()
    dates = [milestone["date_value"] for milestone in data["milestones"]]
    # Duration controls use this same prepared segment order as their trace indices.
    for segment in data["segments"]:
        fig.add_trace(go.Bar(
            orientation="h", y=[0], x=[segment["days"] * MILLISECONDS_PER_DAY],
            base=[segment["start"].isoformat()], width=6,
            name=segment["label"], marker_color=segment["color"],
            customdata=[[segment["start"].isoformat(), segment["end"].isoformat(), segment["days"]]],
            hovertemplate=("%{fullData.name}<br>%{customdata[0]} – %{customdata[1]}"
                           "<br>%{customdata[2]} calendar days<extra></extra>"),
        ))
        dates.extend([segment["start"], segment["end"]])

    earliest, latest = min(dates), max(dates)
    padding = timedelta(days=max(3, (latest - earliest).days * 0.08))
    range_start, range_end = earliest - padding, latest + padding
    span = (range_end - range_start).days
    lanes = []
    lane_heights = []
    callouts = []
    for milestone in data["milestones"]:
        value, events, color = milestone["date_value"], milestone["events"], milestone["color"]
        fig.add_trace(go.Scatter(
            x=[value.isoformat()], y=[0], name=" / ".join(event["hover_label"] for event in events),
            mode="markers", marker={"color": color, "size": 13, "symbol": milestone["symbol"]},
            showlegend=False,
            hovertemplate=("<b>%{x|%b %d, %Y}</b><br>"
                           + "<br>".join(event["hover_label"] for event in events) + "<extra></extra>"),
        ))
        position = (value - range_start).days / span
        anchor = "left" if position < 0.2 else "right" if position > 0.8 else "center"
        # Reserve label space for a narrow phone viewport as well as desktop.
        # Each lane can hold several callouts when their horizontal extents fit.
        text_width = max(95, max(len(line) for event in events for line in event["short_label"].split("<br>")) * 6.5) / 260
        left = (position if anchor == "left" else position - text_width
                if anchor == "right" else position - text_width / 2)
        right = left + text_width
        lane = next((index for index, intervals in enumerate(lanes)
                     if all(right + 0.04 < start or left > end + 0.04 for start, end in intervals)), len(lanes))
        if lane == len(lanes):
            lanes.append([])
            lane_heights.append(0)
        lanes[lane].append((left, right))
        text_lines = sum(event["short_label"].count("<br>") + 1 for event in events) + 1
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
            text="<br>".join(event["short_label"] for event in events) + f"<br><b>{value:%b %d, %Y}</b>",
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
    }
