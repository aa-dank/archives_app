"""Read-only data preparation for the project-information page."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import re

from sqlalchemy import func
from sqlalchemy.orm import selectinload

from archives_application import db, utils
from archives_application.models import FileLocationModel, ProjectModel


class ProjectInfoValidationError(ValueError):
    """Raised when the project-info selector does not meet its route contract."""


class ProjectInfoNotFoundError(LookupError):
    """Raised when a valid selector does not identify a project."""


class ProjectInfoAmbiguousNumberError(LookupError):
    """Raised when a project number identifies more than one project row."""


OTHER_CONTRACT_DATE_FIELDS = (
    ("bid_date", "Bid"),
    ("contract_date", "Contract"),
    ("beneficial_occupancy_date", "Beneficial occupancy"),
    ("substantial_completion_date", "Substantial completion"),
    ("certificate_of_occupancy_date", "Certificate of occupancy"),
    ("noc_recorded_date", "Notice of completion recorded"),
    ("termination_date", "Termination"),
)

MULTIPLE_CONTRACT_COLUMN_GROUPS = (
    {
        "label": "Contract and parties",
        "columns": (
            ("contract_number", "Contract number", "text", "contract-table-contract-number"),
            ("contractor_org_name", "Contractor", "text", "contract-table-contractor"),
            ("executive_design_org_name", "Executive design organization", "text", "contract-table-design-org"),
            ("scope_description", "Scope description", "text", "contract-table-scope"),
        ),
    },
    {
        "label": "Financials",
        "columns": (
            ("cost_estimate", "Cost estimate", "currency", ""),
            ("original_contract_cost", "Original cost", "currency", ""),
            ("change_order_total", "Change-order total", "currency", ""),
            ("change_order_revised_cost", "Revised Total (Including Change Orders)", "currency", ""),
            ("funding_number", "Funding number", "text", ""),
        ),
    },
    {
        "label": "Schedule dates",
        "columns": (
            ("bid_date", "Bid", "date", ""),
            ("contract_date", "Contract", "date", ""),
            ("ntp_start_date", "Notice to proceed", "date", ""),
            ("beneficial_occupancy_date", "Beneficial occupancy", "date", ""),
            ("substantial_completion_date", "Substantial completion", "date", ""),
            ("certificate_of_occupancy_date", "Certificate of occupancy", "date", ""),
            ("noc_completion_date", "Notice of completion", "date", ""),
            ("noc_recorded_date", "Notice of completion recorded", "date", ""),
            ("termination_date", "Termination", "date", ""),
            ("change_order_revised_expected_end", "Current expected end", "date", ""),
        ),
    },
    {
        "label": "Duration",
        "columns": (
            ("original_project_duration", "Original duration", "duration", ""),
            ("change_order_time_total", "Change-order time", "signed_duration", ""),
            ("change_order_revised_duration", "Current duration", "duration", ""),
        ),
    },
)


@dataclass(frozen=True)
class ProjectInfoSelector:
    """The single validated selector supplied to the project-info route."""

    kind: str
    value: int | str


def parse_selector(query_args) -> ProjectInfoSelector:
    """Validate the strict, mutually exclusive project-info query contract."""
    allowed_keys = {"project_id", "project_number"}
    supplied_keys = set(query_args.keys())
    unknown_keys = supplied_keys - allowed_keys
    if unknown_keys:
        raise ProjectInfoValidationError("Unknown query parameter(s): " + ", ".join(sorted(unknown_keys)))

    supplied = []
    for name in ("project_id", "project_number"):
        values = query_args.getlist(name)
        if len(values) > 1:
            raise ProjectInfoValidationError(f"{name} must be supplied only once.")
        if values:
            supplied.append((name, values[0]))

    if len(supplied) != 1:
        raise ProjectInfoValidationError("Supply exactly one of project_id or project_number.")

    name, value = supplied[0]
    if name == "project_id":
        if not re.fullmatch(r"\d+", value or "") or int(value) <= 0:
            raise ProjectInfoValidationError("project_id must be a positive integer.")
        return ProjectInfoSelector(kind=name, value=int(value))

    number = (value or "").strip()
    if not number:
        raise ProjectInfoValidationError("project_number must not be blank.")
    return ProjectInfoSelector(kind=name, value=number)


def _project_query():
    return ProjectModel.query.options(
        selectinload(ProjectModel.caans),
        selectinload(ProjectModel.contracts),
    )


def resolve_project(selector: ProjectInfoSelector):
    """Resolve a project, preserving the required duplicate-number behavior."""
    if selector.kind == "project_id":
        project = _project_query().filter(ProjectModel.id == selector.value).one_or_none()
        if project is None:
            raise ProjectInfoNotFoundError(f"Project ID {selector.value} was not found.")
        return project

    # Limit the query because the only distinction needed here is zero, one, or many.
    projects = (
        _project_query()
        .filter(func.lower(func.trim(ProjectModel.number)) == selector.value.lower())
        .limit(2)
        .all()
    )
    if not projects:
        raise ProjectInfoNotFoundError(f"Project number {selector.value} was not found.")
    if len(projects) > 1:
        raise ProjectInfoAmbiguousNumberError(
            f"Project number {selector.value} is ambiguous; use a project ID instead."
        )
    return projects[0]


def _normalized_root(root: str | None) -> str | None:
    """Return a clean Records-relative root, or ``None`` for a blank value."""
    if not isinstance(root, str):
        return None
    normalized = root.strip().replace("\\", "/").rstrip("/")
    return normalized or None


def _descendant_like_pattern(root: str) -> str:
    """Build a LIKE pattern whose special characters remain literal."""
    escaped = root.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}/%"


def indexed_file_count(root: str | None) -> int | None:
    """Count indexed file paths at a root or its directory-boundary descendants."""
    normalized_root = _normalized_root(root)
    if normalized_root is None:
        return None
    return (
        db.session.query(func.count(FileLocationModel.id))
        .filter(
            (FileLocationModel.file_server_directories == normalized_root)
            | FileLocationModel.file_server_directories.like(
                _descendant_like_pattern(normalized_root), escape="\\"
            )
        )
        .scalar()
    )


def _has_value(value) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _readable_date(value: date | datetime) -> str:
    return value.strftime("%b %d, %Y")


def _readable_datetime(value: datetime) -> str:
    return value.strftime("%b %d, %Y %H:%M")


def _currency(value: Decimal) -> str:
    return f"${value:,.2f}"


def contract_fields(contract) -> list[dict]:
    """Prepare ordered, non-empty single-contract fields for definition lists."""
    groups = (
        (
            "Identity and parties",
            (
                ("Contract number", "contract_number", str),
                ("Contractor", "contractor_org_name", str),
                ("Executive design organization", "executive_design_org_name", str),
                ("Scope description", "scope_description", str),
            ),
        ),
        (
            "Financials",
            (
                ("Cost estimate", "cost_estimate", _currency),
                ("Original contract cost", "original_contract_cost", _currency),
                ("Change-order total", "change_order_total", _currency),
                ("Revised Total (Including Change Orders)", "change_order_revised_cost", _currency),
                ("Funding number", "funding_number", str),
            ),
        ),
    )
    prepared_groups = []
    for group_label, fields in groups:
        values = []
        for label, attribute, formatter in fields:
            value = getattr(contract, attribute)
            if _has_value(value):
                values.append({"label": label, "value": formatter(value), "is_scope": attribute == "scope_description"})
        if values:
            prepared_groups.append({"label": group_label, "fields": values})
    return prepared_groups


def _duration_display(value: int, signed: bool = False) -> str:
    """Format a raw calendar-day duration without deriving a new schedule value."""
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value} calendar day{'s' if abs(value) != 1 else ''}"


def multiple_contract_table(contracts) -> list[dict]:
    """Prepare a complete, stable-order row for every direct contract link."""
    rows = []
    for contract in sorted(
        contracts,
        key=lambda row: (_natural_sort_key(str(row.contract_number or "")), row.id),
    ):
        cells = []
        for group in MULTIPLE_CONTRACT_COLUMN_GROUPS:
            for field, _label, value_type, css_class in group["columns"]:
                value = getattr(contract, field)
                if not _has_value(value):
                    display_value = "—"
                elif value_type == "currency":
                    display_value = _currency(value)
                elif value_type == "date":
                    display_value = _readable_date(value)
                elif value_type == "duration":
                    display_value = _duration_display(value)
                elif value_type == "signed_duration":
                    display_value = _duration_display(value, signed=True)
                else:
                    display_value = str(value)
                cells.append({"value": display_value, "css_class": css_class})
        rows.append({"cells": cells})
    return rows


def contract_schedule(contract) -> dict:
    """Prepare the contractual schedule clock and transparent consistency checks."""
    original_duration = contract.original_project_duration
    change_order_time = contract.change_order_time_total
    revised_duration = contract.change_order_revised_duration
    ntp_start = contract.ntp_start_date
    expected_end = contract.change_order_revised_expected_end
    actual_completion = contract.noc_completion_date

    duration_reconciled = None
    if all(value is not None for value in (original_duration, change_order_time, revised_duration)):
        duration_reconciled = original_duration + change_order_time == revised_duration

    expected_end_reconciled = None
    if ntp_start is not None and revised_duration is not None and expected_end is not None:
        expected_end_reconciled = ntp_start + timedelta(days=revised_duration) == expected_end

    actual_variance_days = None
    if actual_completion is not None and expected_end is not None:
        actual_variance_days = (actual_completion - expected_end).days

    components = []
    if original_duration is not None:
        components.append({
            "label": "Original contract duration",
            "value": _duration_display(original_duration),
            "style": "original",
        })
    if change_order_time is not None:
        components.append({
            "label": "Approved change-order time",
            "value": _duration_display(change_order_time, signed=True),
            "style": "change-order",
        })
    if revised_duration is not None:
        components.append({
            "label": "Current contractual duration",
            "value": _duration_display(revised_duration),
            "style": None,
        })

    can_segment_bar = (
        original_duration is not None
        and change_order_time is not None
        and revised_duration is not None
        and original_duration >= 0
        and change_order_time >= 0
        and revised_duration > 0
        and duration_reconciled
    )
    bar_segments = []
    if can_segment_bar:
        bar_segments = [
            {
                "label": "Original contract duration",
                "days": original_duration,
                "percent": (original_duration / revised_duration) * 100,
                "style": "original",
            },
            {
                "label": "Approved change-order time",
                "days": change_order_time,
                "percent": (change_order_time / revised_duration) * 100,
                "style": "change-order",
            },
        ]
    elif revised_duration is not None and revised_duration > 0:
        bar_segments = [{
            "label": "Current contractual duration",
            "days": revised_duration,
            "percent": 100,
            "style": "current",
        }]

    if actual_variance_days is None:
        actual_variance_label = None
    elif actual_variance_days == 0:
        actual_variance_label = "on the current expected end date"
    elif actual_variance_days < 0:
        actual_variance_label = f"{abs(actual_variance_days)} calendar day{'s' if abs(actual_variance_days) != 1 else ''} before the current expected end"
    else:
        actual_variance_label = f"{actual_variance_days} calendar day{'s' if actual_variance_days != 1 else ''} after the current expected end"

    return {
        "ntp_start": ntp_start,
        "ntp_start_display": _readable_date(ntp_start) if ntp_start else None,
        "expected_end": expected_end,
        "expected_end_display": _readable_date(expected_end) if expected_end else None,
        "actual_completion": actual_completion,
        "actual_completion_display": _readable_date(actual_completion) if actual_completion else None,
        "actual_variance_label": actual_variance_label,
        "duration_reconciled": duration_reconciled,
        "expected_end_reconciled": expected_end_reconciled,
        "components": components,
        "show_duration_key": can_segment_bar,
        "bar_segments": bar_segments,
        "has_schedule_dates": ntp_start is not None and expected_end is not None,
    }


def other_contract_dates(contract) -> list[dict]:
    """Return chronological non-schedule dates without treating them as a schedule."""
    dates = []
    for sort_order, (field, label) in enumerate(OTHER_CONTRACT_DATE_FIELDS):
        value = getattr(contract, field)
        if value is not None:
            dates.append({
                "source_field": field,
                "label": label,
                "iso_date": value.isoformat(),
                "display_date": _readable_date(value),
                "sort_order": sort_order,
                "date_value": value,
            })
    return sorted(dates, key=lambda event: (event["date_value"], event["sort_order"]))


def _status(value: bool | None, true_label: str, false_label: str) -> str:
    if value is None:
        return "Unknown"
    return true_label if value else false_label


def project_context(project, user_archives_location: str | None) -> dict:
    """Prepare only presentation-neutral page data; this function never touches SMB."""
    root = _normalized_root(project.file_server_location)
    contracts = list(project.contracts)
    contract_state = "none" if not contracts else "single" if len(contracts) == 1 else "multiple"
    display_path = None
    if root and user_archives_location:
        display_path = utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=root,
            user_archives_location=user_archives_location,
        )
    indexed_count = indexed_file_count(root) if root else None

    sorted_caans = sorted(project.caans, key=lambda caan: _natural_sort_key(caan.caan))
    context = {
        "project": project,
        "project_status": _status(project.closed, "Closed", "Open"),
        "drawings_status": _status(project.drawings, "Yes", "No"),
        "last_synced_display": _readable_datetime(project.last_synced_at) if project.last_synced_at else None,
        "caans": sorted_caans,
        "caans_collapsible": len(sorted_caans) > 10,
        "root": root,
        "display_path": display_path,
        "indexed_file_count": indexed_count,
        "contract_state": contract_state,
        "contract_count": len(contracts),
        "contract_groups": [],
        "contract_schedule": None,
        "other_contract_dates": [],
        "multiple_contract_column_groups": MULTIPLE_CONTRACT_COLUMN_GROUPS,
        "multiple_contract_rows": [],
    }
    if contract_state == "single":
        context["contract_groups"] = contract_fields(contracts[0])
        context["contract_schedule"] = contract_schedule(contracts[0])
        context["other_contract_dates"] = other_contract_dates(contracts[0])
    elif contract_state == "multiple":
        context["multiple_contract_rows"] = multiple_contract_table(contracts)
    return context


def _natural_sort_key(value: str | None):
    return tuple(
        (0, int(chunk)) if chunk.isdigit() else (1, chunk.lower())
        for chunk in re.split(r"(\d+)", value or "")
        if chunk
    )
