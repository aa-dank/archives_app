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

PROJECT_INFO_SELECTOR_PARAMETERS = frozenset({"project_id", "project_number"})
PROJECT_INFO_API_PARAMETERS = PROJECT_INFO_SELECTOR_PARAMETERS | {"include_user_path"}

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


def _validate_query_parameters(query_args, allowed_keys) -> None:
    """Reject unknown or repeated query parameters before interpreting values."""
    supplied_keys = set(query_args.keys())
    unknown_keys = supplied_keys - allowed_keys
    if unknown_keys:
        raise ProjectInfoValidationError("Unknown query parameter(s): " + ", ".join(sorted(unknown_keys)))

    for name in supplied_keys:
        if len(query_args.getlist(name)) > 1:
            raise ProjectInfoValidationError(f"{name} must be supplied only once.")


def _parse_selector_values(query_args) -> ProjectInfoSelector:
    """Parse one project selector after its parameter names have been validated."""
    supplied = []
    for name in ("project_id", "project_number"):
        values = query_args.getlist(name)
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


def parse_selector(query_args) -> ProjectInfoSelector:
    """Validate the strict, mutually exclusive HTML project-info query contract."""
    _validate_query_parameters(query_args, PROJECT_INFO_SELECTOR_PARAMETERS)
    return _parse_selector_values(query_args)


def parse_api_request(query_args) -> tuple[ProjectInfoSelector, bool]:
    """Validate the project-information API selector and optional path setting."""
    _validate_query_parameters(query_args, PROJECT_INFO_API_PARAMETERS)
    selector = _parse_selector_values(query_args)
    include_user_path_value = query_args.get("include_user_path")
    if include_user_path_value is None:
        return selector, False

    normalized_value = include_user_path_value.strip().lower()
    if normalized_value not in {"true", "false"}:
        raise ProjectInfoValidationError(
            "include_user_path must be either true or false."
        )
    return selector, normalized_value == "true"


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


def project_archive_data(project, user_archives_location: str | None) -> dict:
    """Return the recorded archive-root data shared by page and API responses."""
    root = _normalized_root(project.file_server_location)
    display_path = None
    if root and user_archives_location:
        display_path = utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=root,
            user_archives_location=user_archives_location,
        )
    return {
        "root": root,
        "display_path": display_path,
        "indexed_file_count": indexed_file_count(root) if root else None,
    }


def _has_value(value) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _readable_date(value: date | datetime) -> str:
    return value.strftime("%b %d, %Y")


def _readable_datetime(value: datetime) -> str:
    return value.strftime("%b %d, %Y %H:%M")


def _currency(value: Decimal) -> str:
    return f"${value:,.2f}"


def _decimal_string(value: Decimal | None) -> str | None:
    """Serialize a database decimal without introducing JSON floating-point loss."""
    return format(value, "f") if value is not None else None


def _iso_date(value: date | None) -> str | None:
    """Serialize an optional date using the API's calendar-date format."""
    return value.isoformat() if value is not None else None


def _iso_datetime(value: datetime | None) -> str | None:
    """Serialize an optional timestamp using ISO-8601."""
    return value.isoformat() if value is not None else None


def _sorted_contracts(contracts) -> list:
    """Sort direct contracts in the stable order used by page and API output."""
    return sorted(
        contracts,
        key=lambda row: (_natural_sort_key(str(row.contract_number or "")), row.id),
    )


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
    for contract in _sorted_contracts(contracts):
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
                cells.append({
                    "value": display_value,
                    "css_class": css_class,
                    "is_scope": field == "scope_description",
                    "has_value": _has_value(value),
                })
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


def _serialize_contract_api(contract) -> dict:
    """Build one contract's stable, typed JSON representation."""
    schedule_overview = contract_schedule(contract)
    return {
        "id": contract.id,
        "contract_number": contract.contract_number,
        "contractor_org_name": contract.contractor_org_name,
        "executive_design_org_name": contract.executive_design_org_name,
        "scope_description": contract.scope_description,
        "financials": {
            "cost_estimate": _decimal_string(contract.cost_estimate),
            "original_contract_cost": _decimal_string(contract.original_contract_cost),
            "change_order_total": _decimal_string(contract.change_order_total),
            "revised_total_including_change_orders": _decimal_string(
                contract.change_order_revised_cost
            ),
            "funding_number": contract.funding_number,
        },
        "schedule": {
            "bid_date": _iso_date(contract.bid_date),
            "contract_date": _iso_date(contract.contract_date),
            "ntp_start_date": _iso_date(contract.ntp_start_date),
            "beneficial_occupancy_date": _iso_date(contract.beneficial_occupancy_date),
            "substantial_completion_date": _iso_date(contract.substantial_completion_date),
            "certificate_of_occupancy_date": _iso_date(contract.certificate_of_occupancy_date),
            "noc_completion_date": _iso_date(contract.noc_completion_date),
            "noc_recorded_date": _iso_date(contract.noc_recorded_date),
            "termination_date": _iso_date(contract.termination_date),
            "revised_expected_end_date": _iso_date(
                contract.change_order_revised_expected_end
            ),
            "original_duration_days": contract.original_project_duration,
            "change_order_time_days": contract.change_order_time_total,
            "revised_duration_days": contract.change_order_revised_duration,
            "duration_reconciles": schedule_overview["duration_reconciled"],
            "expected_end_reconciles": schedule_overview["expected_end_reconciled"],
            "actual_vs_expected_days": (
                (contract.noc_completion_date - contract.change_order_revised_expected_end).days
                if (
                    contract.noc_completion_date is not None
                    and contract.change_order_revised_expected_end is not None
                )
                else None
            ),
        },
        "last_synced_at": _iso_datetime(contract.last_synced_at),
    }


def project_api_data(
    project,
    user_archives_location: str | None,
    include_user_path: bool,
) -> dict:
    """Serialize one resolved project for the read-only project-information API."""
    archive_data = project_archive_data(project, user_archives_location)
    archive_response = {
        "root_recorded": archive_data["root"] is not None,
        "database_root": archive_data["root"],
        "indexed_file_location_count": archive_data["indexed_file_count"],
    }
    if include_user_path:
        archive_response["user_path"] = archive_data["display_path"]

    sorted_caans = sorted(project.caans, key=lambda caan: _natural_sort_key(caan.caan))
    sorted_contracts = _sorted_contracts(project.contracts)
    return {
        "project": {
            "id": project.id,
            "number": project.number,
            "name": project.name,
            "closed": project.closed,
            "drawings": project.drawings,
            "campus_client": project.campus_client,
            "notes": project.notes,
            "inspector_name": project.inspector_name,
            "project_manager_name": project.project_manager_name,
            "last_synced_at": _iso_datetime(project.last_synced_at),
        },
        "archives": archive_response,
        "caan_count": len(sorted_caans),
        "caans": [
            {
                "id": caan.id,
                "caan": caan.caan,
                "name": caan.name,
                "description": caan.description,
            }
            for caan in sorted_caans
        ],
        "contract_count": len(sorted_contracts),
        "contracts": [_serialize_contract_api(contract) for contract in sorted_contracts],
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
    archive_data = project_archive_data(project, user_archives_location)
    contracts = list(project.contracts)
    contract_state = "none" if not contracts else "single" if len(contracts) == 1 else "multiple"

    sorted_caans = sorted(project.caans, key=lambda caan: _natural_sort_key(caan.caan))
    context = {
        "project": project,
        "project_status": _status(project.closed, "Closed", "Open"),
        "drawings_status": _status(project.drawings, "Yes", "No"),
        "last_synced_display": _readable_datetime(project.last_synced_at) if project.last_synced_at else None,
        "caans": sorted_caans,
        "caans_collapsible": len(sorted_caans) > 10,
        "root": archive_data["root"],
        "display_path": archive_data["display_path"],
        "indexed_file_count": archive_data["indexed_file_count"],
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
