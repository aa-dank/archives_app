"""Read-only data preparation for the project-information page."""

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime
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


TIMELINE_FIELDS = (
    ("bid_date", "Bid", "actual"),
    ("contract_date", "Contract", "actual"),
    ("ntp_start_date", "Notice to proceed", "actual"),
    ("beneficial_occupancy_date", "Beneficial occupancy", "actual"),
    ("substantial_completion_date", "Substantial completion", "actual"),
    ("certificate_of_occupancy_date", "Certificate of occupancy", "actual"),
    ("noc_completion_date", "Notice of completion", "actual"),
    ("noc_recorded_date", "Notice of completion recorded", "actual"),
    ("termination_date", "Termination", "actual"),
    ("change_order_revised_expected_end", "Revised expected end", "expected"),
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
                ("Change-order revised cost", "change_order_revised_cost", _currency),
                ("Account number", "account_number", str),
                ("Funding number", "funding_number", str),
            ),
        ),
        (
            "Duration",
            (
                ("Original project duration", "original_project_duration", lambda value: f"{value} days"),
                ("Change-order time total", "change_order_time_total", lambda value: f"{value} days"),
                ("Change-order revised duration", "change_order_revised_duration", lambda value: f"{value} days"),
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


def milestone_groups(contract) -> list[dict]:
    """Build chronological milestone groups so same-date labels stack cleanly."""
    events = []
    for sort_order, (field, label, event_type) in enumerate(TIMELINE_FIELDS):
        value = getattr(contract, field)
        if value is not None:
            events.append(
                {
                    "source_field": field,
                    "label": label,
                    "iso_date": value.isoformat(),
                    "display_date": _readable_date(value),
                    "event_type": event_type,
                    "sort_order": sort_order,
                    "date_value": value,
                }
            )
    events.sort(key=lambda event: (event["date_value"], event["sort_order"]))
    if not events:
        return []

    earliest = events[0]["date_value"]
    latest = events[-1]["date_value"]
    span_days = (latest - earliest).days
    groups = OrderedDict()
    for event in events:
        if span_days:
            event["position"] = round(((event["date_value"] - earliest).days / span_days) * 100, 4)
        else:
            event["position"] = 50
        groups.setdefault(event["iso_date"], {"position": event["position"], "events": []})["events"].append(event)
    return list(groups.values())


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
        "milestone_groups": [],
    }
    if contract_state == "single":
        context["contract_groups"] = contract_fields(contracts[0])
        context["milestone_groups"] = milestone_groups(contracts[0])
    return context


def _natural_sort_key(value: str | None):
    return tuple(
        (0, int(chunk)) if chunk.isdigit() else (1, chunk.lower())
        for chunk in re.split(r"(\d+)", value or "")
        if chunk
    )
