"""Contract milestones shared by the date table and timeline renderer."""

from datetime import date, timedelta


# Field, table/hover label, callout label, color, marker symbol.
CONTRACT_DATE_FIELDS = (
    ("bid_date", "Bid", "Bid", "#71608b", "circle"),
    ("contract_date", "Contract", "Contract", "#71608b", "circle"),
    ("ntp_start_date", "Notice to proceed", "NTP", "#5f788a", "circle"),
    ("beneficial_occupancy_date", "Beneficial occupancy", "Beneficial occupancy", "#287f5e", "circle"),
    ("substantial_completion_date", "Substantial completion", "Substantial completion", "#287f5e", "circle"),
    ("certificate_of_occupancy_date", "Certificate of occupancy", "Occupancy certificate", "#287f5e", "circle"),
    ("noc_completion_date", "Actual completion", "Completed", "#287f5e", "square"),
    ("noc_recorded_date", "Notice of completion recorded", "NOC recorded", "#6c757d", "circle"),
    ("termination_date", "Termination", "Terminated", "#a74036", "x"),
    ("change_order_revised_expected_end", "Revised expected completion date",
     "Revised expected<br>completion date", "#9a7000", "diamond"),
)


def _milestone(
    value, *, label, short_label, color, symbol, sort_order,
    source_field=None, calculation=None, hover_label=None, in_table=True,
):
    return {
        "source_field": source_field, "label": label, "short_label": short_label,
        "hover_label": hover_label or label, "color": color, "symbol": symbol,
        "date_value": value, "iso_date": value.isoformat(),
        "display_date": value.strftime("%b %d, %Y"), "sort_order": sort_order,
        "calculation": calculation, "in_table": in_table,
    }


def recorded_contract_dates(contract) -> list[dict]:
    """Return all non-null source dates chronologically, preserving coincident events."""
    events = []
    for order, (field, label, short_label, color, symbol) in enumerate(CONTRACT_DATE_FIELDS):
        value = getattr(contract, field, None)
        if value is not None:
            events.append(_milestone(
                value, source_field=field, label=label, short_label=short_label,
                color=color, symbol=symbol, sort_order=order,
            ))
    return sorted(events, key=lambda event: (event["date_value"], event["sort_order"]))


def prepare_contract_dates(contract, today: date | None = None) -> dict:
    """Prepare recorded/calculated events and resolve coincident marker styles once."""
    events = recorded_contract_dates(contract)
    first_recorded_date = events[0]["date_value"] if events else None
    start = contract.ntp_start_date
    before_ntp_days = (
        (start - first_recorded_date).days
        if start is not None and first_recorded_date is not None else None
    )
    for order, (field, name, hover_name, color) in enumerate((
        ("original_project_duration", "Original", "Original", "#5f788a"),
        ("change_order_revised_duration", "Revised", "Current", "#6c757d"),
    ), start=len(CONTRACT_DATE_FIELDS)):
        days = getattr(contract, field)
        if start is None or days is None or days < 0:
            continue
        value = start + timedelta(days=days)
        if field == "change_order_revised_duration" and value == contract.change_order_revised_expected_end:
            continue
        calculation = (
            "NTP + original duration" if field == "original_project_duration"
            else "NTP + current duration"
        )
        events.append(_milestone(
            value, label=f"{name} end (calculated)", short_label=f"{name} end (calc.)",
            hover_label=f"{hover_name} end ({calculation})", calculation=calculation,
            color=color, symbol="diamond-open", sort_order=order,
        ))
    current_day = today if today is not None else date.today()
    if (first_recorded_date is not None and current_day >= first_recorded_date
            and contract.noc_completion_date is None and contract.termination_date is None):
        events.append(_milestone(
            current_day, label="Today", short_label="Today", color="#a74036",
            symbol="line-ns", sort_order=len(CONTRACT_DATE_FIELDS) + 2, in_table=False,
        ))

    groups = {}
    for event in events:
        groups.setdefault(event["date_value"], []).append(event)
    milestones = []
    for value, coincident in sorted(groups.items()):
        # Preserve the existing last-event style for a shared marker and callout.
        color, symbol = coincident[-1]["color"], coincident[-1]["symbol"]
        for event in coincident:
            event["marker_color"] = color
        milestones.append({
            "date_value": value, "events": coincident,
            "color": color, "symbol": symbol,
        })
    return {
        "milestones": milestones,
        "date_rows": sorted((event for event in events if event["in_table"]),
                            key=lambda event: (event["date_value"], event["sort_order"])),
        "first_recorded_date": first_recorded_date,
        "before_ntp_days": before_ntp_days,
    }
