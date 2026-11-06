"""Recorded contract milestones and their table presentation."""


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


def recorded_contract_dates(contract) -> list[dict]:
    """Return all non-null source dates chronologically, preserving coincident events."""
    events = []
    for order, (field, label, short_label, color, symbol) in enumerate(CONTRACT_DATE_FIELDS):
        value = getattr(contract, field, None)
        if value is not None:
            events.append({
                "source_field": field, "label": label, "short_label": short_label,
                "color": color, "symbol": symbol, "date_value": value,
                "iso_date": value.isoformat(), "display_date": value.strftime("%b %d, %Y"),
                "sort_order": order,
            })
    return sorted(events, key=lambda event: (event["date_value"], event["sort_order"]))


def contract_date_rows(recorded_dates: list[dict], timeline: dict | None) -> list[dict]:
    """Prepare chronological rows with calculated dates and matching plot colors."""
    rows = [{**event, "calculation": None} for event in recorded_dates]
    if timeline is not None:
        for order, (key, label, calculation) in enumerate((
            ("original_end", "Original end (calculated)", "NTP + original duration"),
            ("duration_end", "Revised end (calculated)", "NTP + current duration"),
        ), start=len(CONTRACT_DATE_FIELDS)):
            value = timeline[key]
            if value is not None:
                rows.append({
                    "label": label, "calculation": calculation, "date_value": value,
                    "iso_date": value.isoformat(), "display_date": value.strftime("%b %d, %Y"),
                    "sort_order": order,
                })
    colors = timeline["milestone_colors"] if timeline is not None else {}
    for row in rows:
        row["marker_color"] = colors.get(row["iso_date"])
    return sorted(rows, key=lambda event: (event["date_value"], event["sort_order"]))
