import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PureWindowsPath

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from sqlalchemy import bindparam, text

from archives_application import db, utils
from archives_application.models import (
    ArchiveSearchRunModel,
    CAANModel,
    ProjectCaanModel,
    ProjectModel,
)


SEARCH_MODE_LABELS = {
    "filename_only": "Filename only",
    "filepath": "Filename/path",
    "content": "Document text",
    "combined": "Filename/path + document text",
}

SCOPE_LABELS = {
    "all": "All archives",
    "location": "Location prefix",
    "project": "Project",
    "caan": "CAAN",
}

THIN_TEXT_THRESHOLD = 50
LOW_CONTEXT_TEXT_THRESHOLD = 200
IMAGE_EXTENSIONS = {"jpg", "jpeg", "tif", "tiff", "png", "gif", "bmp"}
UNSUPPORTED_OR_LOW_VALUE_EXTENSIONS = {
    "zip", "lnk", "mov", "mp4", "avi", "dwg", "dxf", "pl", "tfw", "plt",
    "ctb", "db", "exe", "gdbtable", "shx", "dbf", "dll", "bak", "tmp",
}
API_SEARCH_MODES = set(SEARCH_MODE_LABELS)
API_SCOPE_TYPES = set(SCOPE_LABELS)
API_REQUEST_SOURCES = {"web", "api"}
API_EXTENSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ArchiveSearchAPIValidationError(ValueError):
    """Raised when an archives-search API request cannot be safely executed."""


@dataclass(frozen=True)
class ArchiveSearchRequest:
    """Validated, transport-neutral inputs for an archives search."""

    query_text: str
    search_mode: str
    requested_scope_type: str
    requested_scope_value: str | None
    extensions: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        query_text: str,
        search_mode: str = "combined",
        requested_scope_type: str = "all",
        requested_scope_value: str | None = None,
        extension_filters=None,
    ):
        """Normalize validated values supplied by an HTML form or future API adapter."""
        scope_type = (requested_scope_type or "all").strip().lower()
        scope_value = (
            str(requested_scope_value).strip()
            if requested_scope_value is not None
            else None
        )
        if scope_type == "all":
            scope_value = None
        elif scope_type == "project" and scope_value:
            scope_value = scope_value.upper()

        return cls(
            query_text=(query_text or "").strip(),
            search_mode=(search_mode or "combined").strip().lower(),
            requested_scope_type=scope_type,
            requested_scope_value=scope_value,
            extensions=tuple(_parse_extension_filter(extension_filters)),
        )

    @classmethod
    def from_form(cls, form):
        """Adapt a validated ArchiveSearchForm into the search service contract."""
        scope_type = form.scope_type.data or "all"
        scope_fields = {
            "location": form.location_scope.data,
            "project": form.project_number.data,
            "caan": form.caan.data,
        }
        return cls.from_values(
            query_text=form.search_term.data,
            search_mode=form.search_mode.data,
            requested_scope_type=scope_type,
            requested_scope_value=scope_fields.get(scope_type),
            extension_filters=form.file_extension.data,
        )

    @classmethod
    def from_api_payload(
        cls,
        payload: dict,
        query_max_length: int,
        extensions_max_length: int,
    ):
        """Validate and adapt an API JSON payload into the shared search contract."""
        if not isinstance(payload, dict):
            raise ArchiveSearchAPIValidationError("The JSON request body must be an object.")

        allowed_fields = {
            "user",
            "password",
            "query_text",
            "search_mode",
            "scope_type",
            "scope_value",
            "extensions",
            "limit",
        }
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise ArchiveSearchAPIValidationError(
                f"Unknown request field(s): {', '.join(unknown_fields)}."
            )

        query_text = payload.get("query_text")
        if not isinstance(query_text, str) or not query_text.strip():
            raise ArchiveSearchAPIValidationError("query_text must be a non-empty string.")
        if len(query_text) > query_max_length:
            raise ArchiveSearchAPIValidationError(
                f"query_text must not exceed {query_max_length} characters."
            )

        search_mode = payload.get("search_mode", "combined")
        if not isinstance(search_mode, str) or search_mode.strip().lower() not in API_SEARCH_MODES:
            raise ArchiveSearchAPIValidationError(
                "search_mode must be one of: combined, filename_only, filepath, content."
            )

        scope_type = payload.get("scope_type", "all")
        if not isinstance(scope_type, str) or scope_type.strip().lower() not in API_SCOPE_TYPES:
            raise ArchiveSearchAPIValidationError(
                "scope_type must be one of: all, location, project, caan."
            )
        scope_type = scope_type.strip().lower()

        scope_value = payload.get("scope_value")
        if scope_value is not None and not isinstance(scope_value, str):
            raise ArchiveSearchAPIValidationError("scope_value must be a string or null.")
        if scope_type == "all" and scope_value and scope_value.strip():
            raise ArchiveSearchAPIValidationError("scope_value must be omitted when scope_type is all.")
        if scope_type != "all" and (not isinstance(scope_value, str) or not scope_value.strip()):
            raise ArchiveSearchAPIValidationError(
                "scope_value is required when scope_type is location, project, or caan."
            )

        extensions = payload.get("extensions", "")
        if not isinstance(extensions, str):
            raise ArchiveSearchAPIValidationError("extensions must be a comma-separated string.")
        if len(extensions) > extensions_max_length:
            raise ArchiveSearchAPIValidationError(
                f"extensions must not exceed {extensions_max_length} characters."
            )
        parsed_extensions = _parse_extension_filter(extensions)
        if any(not API_EXTENSION_PATTERN.fullmatch(extension) for extension in parsed_extensions):
            raise ArchiveSearchAPIValidationError(
                "extensions must be comma-separated letters, numbers, underscores, or hyphens."
            )

        return cls.from_values(
            query_text=query_text,
            search_mode=search_mode,
            requested_scope_type=scope_type,
            requested_scope_value=scope_value,
            extension_filters=parsed_extensions,
        )


@dataclass
class ScopeResolution:
    """Compiled archive-search scope plus user-facing resolution notes."""

    scope_type: str
    display_value: str = ""
    prefixes: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    project_count: int = 0
    usable_project_count: int = 0
    missing_project_location_count: int = 0
    roots_with_no_indexed_files: list[str] = field(default_factory=list)
    caan_found: bool | None = None
    linked_project_count: int = 0

    @property
    def has_scope(self) -> bool:
        """Return True when the scope has one or more directory prefixes."""
        return bool(self.prefixes)

    @property
    def label(self) -> str:
        """Build a compact label for displaying the active scope."""
        if self.scope_type == "all":
            return "All archives"
        base = SCOPE_LABELS.get(self.scope_type, self.scope_type)
        return f"{base}: {self.display_value}" if self.display_value else base


def _clean_archive_prefix(prefix: str | None) -> str:
    """Normalize a Records-relative directory prefix for DB comparisons."""
    if prefix is None:
        return ""
    prefix = str(prefix).strip().replace("\\", "/").strip("/")
    if prefix.lower().startswith("records/"):
        prefix = prefix[8:]
    return re.sub(r"/+", "/", prefix)


def _windows_parts(path_value: str) -> list[str]:
    """Split a user-facing Windows path into normalized comparable parts."""
    return [
        part.strip("\\/").lower()
        for part in PureWindowsPath(path_value).parts
        if part and part not in ["\\", "/"]
    ]


def _path_starts_with_user_mount(path_value: str, user_mount: str | None) -> bool:
    """Return True when a user-entered path starts under the configured mount."""
    if not path_value or not user_mount:
        return False
    entered_parts = _windows_parts(path_value)
    mount_parts = _windows_parts(user_mount)
    return len(entered_parts) >= len(mount_parts) and entered_parts[:len(mount_parts)] == mount_parts


def _location_input_to_prefix(location_value: str, app) -> str:
    """Convert a user path or DB-relative location input to a DB prefix."""
    user_archives_location = app.config.get("USER_ARCHIVES_LOCATION")
    archives_location = app.config.get("ARCHIVES_LOCATION")
    if _path_starts_with_user_mount(location_value, user_archives_location):
        app_path = utils.FlaskAppUtils.user_path_to_app_path(location_value, app)
        db_dir, _ = utils.FileServerUtils.app_path_to_db_dir(app_path, archives_location)
        return _clean_archive_prefix(db_dir)
    return _clean_archive_prefix(location_value)


def _like_pattern(prefix: str) -> str:
    """Build an escaped LIKE pattern for descendants of a directory prefix."""
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}/%"


def _scope_clause(column_expr: str, prefixes: list[str], params: dict) -> str:
    """Build a directory-boundary SQL predicate for the supplied prefixes."""
    if not prefixes:
        return "TRUE"
    clauses = []
    for idx, prefix in enumerate(prefixes):
        exact_key = f"scope_prefix_{idx}"
        like_key = f"scope_like_{idx}"
        params[exact_key] = prefix
        params[like_key] = _like_pattern(prefix)
        clauses.append(
            f"({column_expr} = :{exact_key} OR {column_expr} LIKE :{like_key} ESCAPE '\\')"
        )
    return "(" + " OR ".join(clauses) + ")"


def _scope_paths_values(prefixes: list[str], params: dict) -> str:
    """Build a parameterized VALUES list for scope-prefix SQL CTEs."""
    values = []
    for idx, prefix in enumerate(prefixes):
        key = f"scope_value_{idx}"
        params[key] = prefix
        values.append(f"(:{key})")
    return ", ".join(values)


def _root_indexed_file_status(prefixes: list[str]) -> list[str]:
    """Return scope roots that do not match any indexed file locations."""
    if not prefixes:
        return []
    params = {}
    values_sql = _scope_paths_values(prefixes, params)
    sql = f"""
        WITH scope_paths(path_prefix) AS (VALUES {values_sql})
        SELECT
            sp.path_prefix,
            EXISTS (
                SELECT 1
                FROM file_locations fl
                WHERE fl.file_server_directories = sp.path_prefix
                   OR fl.file_server_directories LIKE
                        replace(replace(replace(sp.path_prefix, '\\', '\\\\'), '%', '\\%'), '_', '\\_') || '/%' ESCAPE '\\'
            ) AS has_indexed_files
        FROM scope_paths sp
    """
    rows = db.session.execute(text(sql), params).mappings().all()
    return [row["path_prefix"] for row in rows if not row["has_indexed_files"]]


def resolve_scope(search_request: ArchiveSearchRequest, app) -> ScopeResolution:
    """Resolve requested scope input into Records-relative directory prefixes."""
    scope_type = search_request.requested_scope_type
    resolution = ScopeResolution(scope_type=scope_type)

    if scope_type == "all":
        return resolution

    if scope_type == "location":
        display_value = search_request.requested_scope_value or ""
        prefix = _location_input_to_prefix(display_value, app)
        resolution.display_value = display_value
        resolution.prefixes = [prefix] if prefix else []
        if not prefix:
            resolution.scope_type = "all"
            resolution.warnings.append(
                "The selected location resolves to the Records root, so this search behaves like all archives."
            )
        resolution.roots_with_no_indexed_files = _root_indexed_file_status(resolution.prefixes)
        return resolution

    if scope_type == "project":
        project_number = search_request.requested_scope_value or ""
        resolution.display_value = project_number
        projects = ProjectModel.query.filter(ProjectModel.number == project_number).all()
        resolution.project_count = len(projects)
        if not projects:
            resolution.messages.append(f"No project rows found for project number {project_number}.")
            return resolution

        prefixes = []
        for project in projects:
            root = _clean_archive_prefix(project.file_server_location)
            if root:
                prefixes.append(root)
            else:
                resolution.missing_project_location_count += 1
        resolution.prefixes = sorted(set(prefixes))
        resolution.usable_project_count = len(resolution.prefixes)
        if resolution.missing_project_location_count:
            resolution.messages.append(
                f"{resolution.missing_project_location_count} matching project row(s) have no file server location and were not included."
            )
        if not resolution.prefixes:
            resolution.messages.append(
                "Matching project rows exist, but none has a recorded file server location."
            )
            return resolution
        resolution.roots_with_no_indexed_files = _root_indexed_file_status(resolution.prefixes)
        return resolution

    if scope_type == "caan":
        caan_value = search_request.requested_scope_value or ""
        resolution.display_value = caan_value
        caan = CAANModel.query.filter(CAANModel.caan == caan_value).first()
        resolution.caan_found = bool(caan)
        if not caan:
            resolution.messages.append(f"No CAAN row found for {caan_value}.")
            return resolution

        linked_projects = (
            ProjectModel.query
            .join(ProjectCaanModel, ProjectCaanModel.project_id == ProjectModel.id)
            .filter(ProjectCaanModel.caan_id == caan.id)
            .all()
        )
        resolution.linked_project_count = len(linked_projects)
        resolution.project_count = len(linked_projects)
        resolution.messages.append(
            f"CAAN scope uses direct project links from project_caans; {len(linked_projects)} linked project row(s) were found."
        )
        if not linked_projects:
            resolution.messages.append(
                "No linked projects were found. CAAN content search depends on project-CAAN linkage and may be incomplete until that linkage is populated."
            )
            return resolution

        prefixes = []
        for project in linked_projects:
            root = _clean_archive_prefix(project.file_server_location)
            if root:
                prefixes.append(root)
            else:
                resolution.missing_project_location_count += 1
        resolution.prefixes = sorted(set(prefixes))
        resolution.usable_project_count = len(resolution.prefixes)
        if resolution.missing_project_location_count:
            resolution.messages.append(
                f"{resolution.missing_project_location_count} linked project row(s) have no file server location and were not included."
            )
        if not resolution.prefixes:
            resolution.messages.append(
                "Linked projects exist, but none has a recorded file server location."
            )
            return resolution
        resolution.roots_with_no_indexed_files = _root_indexed_file_status(resolution.prefixes)
        return resolution

    resolution.messages.append(f"Unknown scope type: {scope_type}")
    return resolution


def _file_hash_scope_cte(scope: ScopeResolution, params: dict) -> str:
    """Build the scoped file-hash CTE used by search and coverage queries."""
    if not scope.has_scope:
        return "scoped_file_hashes AS (SELECT f.hash AS file_hash FROM files f)"
    scope_filter = _scope_clause("fl.file_server_directories", scope.prefixes, params)
    return f"""
        scoped_file_hashes AS (
            SELECT DISTINCT f.hash AS file_hash
            FROM file_locations fl
            JOIN files f ON f.id = fl.file_id
            WHERE {scope_filter}
        )
    """


def _parse_extension_filter(extension_value) -> list[str]:
    """Normalize comma-delimited or iterable extension filters into distinct values."""
    if isinstance(extension_value, str):
        raw_extensions = extension_value.split(",")
    else:
        raw_extensions = extension_value or []

    extensions = []
    seen = set()
    for raw_extension in raw_extensions:
        extension = str(raw_extension).strip().lower().lstrip(".")
        if extension and extension not in seen:
            extensions.append(extension)
            seen.add(extension)
    return extensions


def _extension_clause(file_alias: str, extension_values: list[str] | None, params: dict) -> str:
    """Build an optional case-normalized file-extension SQL predicate."""
    if not extension_values:
        return "TRUE"
    extension_params = []
    for idx, extension in enumerate(extension_values):
        key = f"extension_filter_{idx}"
        params[key] = extension
        extension_params.append(f":{key}")
    return f"lower(coalesce({file_alias}.extension, '')) IN ({', '.join(extension_params)})"


def _scope_allows_search(scope: ScopeResolution) -> bool:
    """Return True when the resolved scope can be safely searched."""
    return scope.scope_type == "all" or bool(scope.prefixes)


def _execute_content_search(query_text: str, scope: ScopeResolution, extensions: list[str], file_limit: int, app) -> list[dict]:
    """Run scoped PostgreSQL FTS over chunk search vectors."""
    params = {
        "query_text": query_text,
        "file_limit": file_limit,
        "chunk_candidate_limit": min(
            int(app.config.get("ARCHIVE_SEARCH_CHUNK_CANDIDATE_LIMIT", 50000)),
            max(1000, file_limit * int(app.config.get("ARCHIVE_SEARCH_CHUNK_CANDIDATE_MULTIPLIER", 20))),
        ),
    }
    scoped_cte = _file_hash_scope_cte(scope, params)
    extension_filter = _extension_clause("f", extensions, params)
    sql = f"""
        WITH q AS (
            SELECT websearch_to_tsquery('simple', :query_text) AS query
        ),
        {scoped_cte},
        latest_chunk_sets AS (
            SELECT c.file_hash, max(c.chunked_at) AS chunked_at
            FROM file_content_fts_chunks c
            JOIN scoped_file_hashes sfh ON sfh.file_hash = c.file_hash
            GROUP BY c.file_hash
        ),
        matching_chunks AS (
            SELECT
                c.file_hash,
                c.id AS chunk_id,
                c.chunk_index,
                ts_rank_cd(c.search_vector, q.query) AS chunk_rank
            FROM file_content_fts_chunks c
            JOIN latest_chunk_sets lcs
              ON lcs.file_hash = c.file_hash
             AND lcs.chunked_at = c.chunked_at
            JOIN scoped_file_hashes sfh ON sfh.file_hash = c.file_hash
            JOIN files f ON f.hash = c.file_hash
            CROSS JOIN q
            WHERE c.search_vector @@ q.query
              AND {extension_filter}
            ORDER BY chunk_rank DESC
            LIMIT :chunk_candidate_limit
        ),
        file_scores AS (
            SELECT
                file_hash,
                max(chunk_rank) AS content_rank,
                count(*) AS matching_chunks,
                (array_agg(chunk_id ORDER BY chunk_rank DESC, chunk_index ASC))[1] AS best_chunk_id
            FROM matching_chunks
            GROUP BY file_hash
        )
        SELECT *
        FROM file_scores
        ORDER BY content_rank DESC, matching_chunks DESC, file_hash ASC
        LIMIT :file_limit
    """
    return [dict(row) for row in db.session.execute(text(sql), params).mappings().all()]


def _execute_filename_search(query_text: str, scope: ScopeResolution, extensions: list[str], file_limit: int, filename_only: bool) -> list[dict]:
    """Run grouped filename or filename/path FTS at file-hash level."""
    params = {"query_text": query_text, "file_limit": file_limit}
    path_vector = "" if filename_only else " || to_tsvector('english', coalesce(fl.file_server_directories, ''))"
    scope_filter = _scope_clause("fl.file_server_directories", scope.prefixes, params)
    extension_filter = _extension_clause("f", extensions, params)
    sql = f"""
        WITH q AS (
            SELECT websearch_to_tsquery('english', :query_text) AS query
        ),
        matching_locations AS (
            SELECT
                f.hash AS file_hash,
                fl.id AS location_id,
                ts_rank_cd(
                    to_tsvector('english', regexp_replace(coalesce(fl.filename, ''), '\\.', ' ', 'gi')){path_vector},
                    q.query
                ) AS filepath_rank
            FROM file_locations fl
            JOIN files f ON f.id = fl.file_id
            CROSS JOIN q
            WHERE (
                    to_tsvector('english', regexp_replace(coalesce(fl.filename, ''), '\\.', ' ', 'gi')){path_vector}
                  ) @@ q.query
              AND {scope_filter}
              AND {extension_filter}
        ),
        file_scores AS (
            SELECT
                file_hash,
                max(filepath_rank) AS filepath_rank,
                (array_agg(location_id ORDER BY filepath_rank DESC, location_id ASC))[1] AS best_location_id,
                array_agg(location_id ORDER BY filepath_rank DESC, location_id ASC) AS matching_location_ids
            FROM matching_locations
            GROUP BY file_hash
        )
        SELECT *
        FROM file_scores
        ORDER BY filepath_rank DESC, file_hash ASC
        LIMIT :file_limit
    """
    return [dict(row) for row in db.session.execute(text(sql), params).mappings().all()]


def _merge_results(content_rows: list[dict], filepath_rows: list[dict], file_limit: int) -> list[dict]:
    """Merge content and filename/path matches into ranked file results."""
    merged: dict[str, dict] = {}
    for row in content_rows:
        merged[row["file_hash"]] = {
            "file_hash": row["file_hash"],
            "match_source": "content",
            "content_rank": row.get("content_rank"),
            "filepath_rank": None,
            "matching_chunks": row.get("matching_chunks") or 0,
            "best_chunk_id": row.get("best_chunk_id"),
            "best_location_id": None,
            "matching_location_ids": set(),
        }
    for row in filepath_rows:
        existing = merged.get(row["file_hash"])
        if existing:
            existing["match_source"] = "both"
            existing["filepath_rank"] = row.get("filepath_rank")
            existing["best_location_id"] = row.get("best_location_id")
            existing["matching_location_ids"] = set(row.get("matching_location_ids") or [])
        else:
            merged[row["file_hash"]] = {
                "file_hash": row["file_hash"],
                "match_source": "filename/path",
                "content_rank": None,
                "filepath_rank": row.get("filepath_rank"),
                "matching_chunks": 0,
                "best_chunk_id": None,
                "best_location_id": row.get("best_location_id"),
                "matching_location_ids": set(row.get("matching_location_ids") or []),
            }

    source_priority = {"both": 0, "content": 1, "filename/path": 2}
    sorted_rows = sorted(
        merged.values(),
        key=lambda row: (
            source_priority.get(row["match_source"], 9),
            -(float(row["content_rank"] or 0)),
            -(float(row["filepath_rank"] or 0)),
            -(int(row["matching_chunks"] or 0)),
            row["file_hash"],
        ),
    )
    for idx, row in enumerate(sorted_rows[:file_limit], start=1):
        row["result_rank"] = idx
    return sorted_rows[:file_limit]


def _hash_filter_stmt(sql: str):
    """Return a SQL text statement with expanding file-hash bind params."""
    return text(sql).bindparams(bindparam("file_hashes", expanding=True))


def _fetch_file_metadata(file_hashes: list[str]) -> dict[str, dict]:
    """Fetch file, content, and failure metadata for result hashes."""
    if not file_hashes:
        return {}
    sql = """
        SELECT
            f.hash AS file_hash,
            f.extension,
            f.size AS size_bytes,
            fc.text_length,
            fcf.stage AS failure_stage,
            left(fcf.error, 240) AS failure_summary,
            EXISTS (
                SELECT 1
                FROM file_content_fts_chunks c
                WHERE c.file_hash = f.hash
            ) AS has_chunks
        FROM files f
        LEFT JOIN file_contents fc ON fc.file_hash = f.hash
        LEFT JOIN file_content_failures fcf ON fcf.file_hash = f.hash
        WHERE f.hash IN :file_hashes
    """
    rows = db.session.execute(_hash_filter_stmt(sql), {"file_hashes": file_hashes}).mappings().all()
    return {row["file_hash"]: dict(row) for row in rows}


def _fetch_locations(file_hashes: list[str], user_archives_location: str, scope: ScopeResolution) -> dict[str, list[dict]]:
    """Fetch all file locations for results and mark in-scope paths."""
    if not file_hashes:
        return {}
    sql = """
        SELECT
            f.hash AS file_hash,
            fl.id AS location_id,
            fl.filename,
            fl.file_server_directories,
            fl.existence_confirmed,
            fl.hash_confirmed
        FROM files f
        JOIN file_locations fl ON fl.file_id = f.id
        WHERE f.hash IN :file_hashes
        ORDER BY f.hash, length(coalesce(fl.file_server_directories, '')), fl.file_server_directories, fl.filename, fl.id
    """
    rows = db.session.execute(_hash_filter_stmt(sql), {"file_hashes": file_hashes}).mappings().all()
    locations: dict[str, list[dict]] = {}
    for row in rows:
        row_dict = dict(row)
        row_dict["in_scope"] = _location_in_scope(row_dict["file_server_directories"], scope)
        row_dict["user_path"] = utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=row_dict["file_server_directories"],
            user_archives_location=user_archives_location,
            filename=row_dict["filename"],
        )
        locations.setdefault(row_dict["file_hash"], []).append(row_dict)
    return locations


def _location_in_scope(file_server_directories: str | None, scope: ScopeResolution) -> bool:
    """Return True when a location is inside the active search scope."""
    if not scope.has_scope:
        return True
    location = _clean_archive_prefix(file_server_directories)
    for prefix in scope.prefixes:
        if location == prefix or location.startswith(f"{prefix}/"):
            return True
    return False


def _select_primary_location(result: dict, locations: list[dict]) -> dict | None:
    """Choose the best display location for a file-level result."""
    if not locations:
        return None
    best_location_id = result.get("best_location_id")
    if best_location_id:
        for location in locations:
            if location["location_id"] == best_location_id and location["in_scope"]:
                return location
    in_scope_locations = [location for location in locations if location["in_scope"]]
    candidates = in_scope_locations or locations
    return sorted(
        candidates,
        key=lambda location: (
            len(location.get("file_server_directories") or ""),
            location.get("file_server_directories") or "",
            location.get("filename") or "",
            location.get("location_id") or 0,
        ),
    )[0]


def _status_from_metadata(metadata: dict) -> str:
    """Classify content-search coverage for a single file."""
    extension = (metadata.get("extension") or "").lower().lstrip(".")
    text_length = metadata.get("text_length")
    has_content = text_length is not None
    has_chunks = bool(metadata.get("has_chunks"))
    has_failure = bool(metadata.get("failure_stage"))

    if has_chunks and (text_length or 0) >= LOW_CONTEXT_TEXT_THRESHOLD and extension in IMAGE_EXTENSIONS:
        return "image_ocr_searchable"
    if extension in IMAGE_EXTENSIONS and has_content and 0 < (text_length or 0) < LOW_CONTEXT_TEXT_THRESHOLD:
        return "image_ocr_thin"
    if has_chunks and (text_length or 0) >= THIN_TEXT_THRESHOLD:
        return "content_searchable"
    if has_content and (text_length or 0) < THIN_TEXT_THRESHOLD:
        return "empty_or_thin_text"
    if has_content and not has_chunks:
        return "text_extracted_not_chunked"
    if has_failure:
        return "extraction_failed"
    if extension in UNSUPPORTED_OR_LOW_VALUE_EXTENSIONS:
        return "unsupported_or_low_value_format"
    return "not_attempted"


def status_label(status: str) -> str:
    """Convert an internal status code to a friendly display label."""
    return {
        "content_searchable": "Content searchable",
        "text_extracted_not_chunked": "Text extracted, not chunked",
        "empty_or_thin_text": "Empty or thin text",
        "extraction_failed": "Extraction failed",
        "not_attempted": "Not attempted",
        "unsupported_or_low_value_format": "Unsupported/low-value format",
        "filename_only": "Filename only",
        "image_ocr_thin": "Image OCR thin",
        "image_ocr_searchable": "Image OCR searchable",
    }.get(status, status)


def _fetch_snippets(query_text: str, chunk_ids: list[int]) -> dict[int, str]:
    """Generate safe highlighted snippets for narrowed content matches."""
    if not chunk_ids:
        return {}
    sql = """
        SELECT
            id,
            ts_headline(
                'simple',
                chunk_text,
                websearch_to_tsquery('simple', :query_text),
                'MaxWords=35, MinWords=12, ShortWord=3, HighlightAll=false, StartSel=''[[[H]]]'', StopSel=''[[[/H]]]'''
            ) AS snippet
        FROM file_content_fts_chunks
        WHERE id IN :chunk_ids
    """
    rows = db.session.execute(
        text(sql).bindparams(bindparam("chunk_ids", expanding=True)),
        {"query_text": query_text, "chunk_ids": chunk_ids},
    ).mappings().all()
    snippets = {}
    for row in rows:
        safe = html.escape(row["snippet"] or "")
        safe = safe.replace("[[[H]]]", "<mark>").replace("[[[/H]]]", "</mark>")
        snippets[row["id"]] = safe
    return snippets


def _format_size(byte_count: int | None) -> str:
    """Format a byte count for display."""
    if byte_count is None:
        return ""
    value = float(byte_count)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024.0 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024.0
    return str(byte_count)


def _sanitize_excel_cell_value(value):
    """Strip illegal XML control characters from string cell values before XLSX export."""
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


def _sanitize_excel_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Sanitize all object-typed columns so openpyxl can safely write the workbook."""
    if df.empty:
        return df
    object_columns = df.select_dtypes(include=["object"]).columns
    for column in object_columns:
        df[column] = df[column].map(_sanitize_excel_cell_value)
    return df


def _coverage_summary(scope: ScopeResolution, extensions: list[str], app) -> dict:
    """Compute scope-level content coverage and status counts."""
    if not _scope_allows_search(scope):
        return {
            "files_in_scope": 0,
            "files_with_nonempty_text": 0,
            "files_with_fts_chunks": 0,
            "files_with_extraction_failures": 0,
            "files_with_empty_or_thin_text": 0,
            "files_with_low_context_text": 0,
            "files_with_text_not_chunked": 0,
            "files_not_attempted": 0,
            "unsupported_or_low_value_format_files": 0,
            "content_searchable_files": 0,
            "filename_only_or_not_content_searchable_files": 0,
            "filename_path_searchable_files": 0,
            "roots_with_no_indexed_files": len(scope.roots_with_no_indexed_files),
        }

    params = {
        "thin_threshold": THIN_TEXT_THRESHOLD,
        "low_context_threshold": LOW_CONTEXT_TEXT_THRESHOLD,
    }
    scoped_cte = _file_hash_scope_cte(scope, params)
    extension_filter = _extension_clause("f", extensions, params)
    sql = f"""
        WITH {scoped_cte},
        status_rows AS (
            SELECT
                f.hash AS file_hash,
                f.extension,
                fc.text_length,
                fcf.stage AS failure_stage,
                EXISTS (
                    SELECT 1 FROM file_content_fts_chunks c
                    WHERE c.file_hash = f.hash
                ) AS has_chunks
            FROM scoped_file_hashes sfh
            JOIN files f ON f.hash = sfh.file_hash
            LEFT JOIN file_contents fc ON fc.file_hash = f.hash
            LEFT JOIN file_content_failures fcf ON fcf.file_hash = f.hash
            WHERE {extension_filter}
        ),
        classified AS (
            SELECT
                *,
                CASE
                    WHEN has_chunks AND coalesce(text_length, 0) >= :low_context_threshold
                         AND lower(coalesce(extension, '')) IN ('jpg', 'jpeg', 'tif', 'tiff', 'png', 'gif', 'bmp')
                        THEN 'image_ocr_searchable'
                    WHEN lower(coalesce(extension, '')) IN ('jpg', 'jpeg', 'tif', 'tiff', 'png', 'gif', 'bmp')
                         AND text_length IS NOT NULL
                         AND coalesce(text_length, 0) > 0
                         AND coalesce(text_length, 0) < :low_context_threshold
                        THEN 'image_ocr_thin'
                    WHEN has_chunks AND coalesce(text_length, 0) >= :thin_threshold
                        THEN 'content_searchable'
                    WHEN text_length IS NOT NULL AND coalesce(text_length, 0) < :thin_threshold
                        THEN 'empty_or_thin_text'
                    WHEN text_length IS NOT NULL AND NOT has_chunks
                        THEN 'text_extracted_not_chunked'
                    WHEN failure_stage IS NOT NULL
                        THEN 'extraction_failed'
                    WHEN lower(coalesce(extension, '')) IN ('zip', 'lnk', 'mov', 'mp4', 'avi', 'dwg', 'dxf', 'pl', 'tfw', 'plt', 'ctb', 'db', 'exe', 'gdbtable', 'shx', 'dbf', 'dll', 'bak', 'tmp')
                        THEN 'unsupported_or_low_value_format'
                    ELSE 'not_attempted'
                END AS text_status
            FROM status_rows
        )
        SELECT
            count(*) AS files_in_scope,
            count(*) FILTER (WHERE text_length IS NOT NULL AND text_length > 0) AS files_with_nonempty_text,
            count(*) FILTER (WHERE has_chunks) AS files_with_fts_chunks,
            count(*) FILTER (WHERE failure_stage IS NOT NULL) AS files_with_extraction_failures,
            count(*) FILTER (WHERE text_length IS NOT NULL AND coalesce(text_length, 0) < :thin_threshold) AS files_with_empty_or_thin_text,
            count(*) FILTER (WHERE text_length IS NOT NULL AND coalesce(text_length, 0) < :low_context_threshold) AS files_with_low_context_text,
            count(*) FILTER (WHERE text_status = 'text_extracted_not_chunked') AS files_with_text_not_chunked,
            count(*) FILTER (WHERE text_status = 'not_attempted') AS files_not_attempted,
            count(*) FILTER (WHERE text_status = 'unsupported_or_low_value_format') AS unsupported_or_low_value_format_files,
            count(*) FILTER (WHERE text_status IN ('content_searchable', 'image_ocr_searchable')) AS content_searchable_files,
            count(*) FILTER (WHERE text_status NOT IN ('content_searchable', 'image_ocr_searchable')) AS filename_only_or_not_content_searchable_files
        FROM classified
    """
    row = db.session.execute(text(sql), params).mappings().first()
    coverage = dict(row or {})
    coverage["filename_path_searchable_files"] = coverage.get("files_in_scope", 0)
    coverage["roots_with_no_indexed_files"] = len(scope.roots_with_no_indexed_files)
    return coverage


class ArchiveSearchRun:
    """Execute one archive search and persist its run-level lifecycle metadata."""

    STATUS_INCOMPLETE = "incomplete"
    STATUS_SUCCESSFUL = "successful"
    STATUS_FAILED = "failed"

    def __init__(
        self,
        search_request: ArchiveSearchRequest,
        app,
        file_limit: int,
        user_id: int | None = None,
        request_source: str = "web",
    ):
        if request_source not in API_REQUEST_SOURCES:
            raise ValueError(
                f"request_source must be one of: {', '.join(sorted(API_REQUEST_SOURCES))}."
            )
        self.request = search_request
        self.app = app
        self.file_limit = file_limit
        self.user_id = user_id
        self.request_source = request_source

        self.status = self.STATUS_INCOMPLETE
        self.record_id: int | None = None
        self.duration_ms: int | None = None
        self.search_data: dict | None = None
        self._executed = False

    def execute(self) -> dict:
        """Create an incomplete run, execute the search, and persist its outcome."""
        if self._executed:
            raise RuntimeError("An ArchiveSearchRun instance can only be executed once.")
        self._executed = True

        self._create_incomplete_record()
        started_at = time.perf_counter()

        try:
            self.search_data = self._execute_search()
        except Exception:
            self.duration_ms = self._elapsed_ms(started_at)
            self.status = self.STATUS_FAILED
            self._persist_final_state()
            raise

        self.duration_ms = self._elapsed_ms(started_at)
        self.status = self.STATUS_SUCCESSFUL
        self._persist_final_state()
        return self.search_data

    def _create_incomplete_record(self):
        """Commit the initial row so a terminated search remains incomplete."""
        try:
            record = ArchiveSearchRunModel(
                user_id=self.user_id,
                query_text=self.request.query_text,
                search_mode=self.request.search_mode,
                requested_scope_type=self.request.requested_scope_type,
                requested_scope_value=self.request.requested_scope_value,
                extension_filters=list(self.request.extensions),
                status=self.STATUS_INCOMPLETE,
                request_source=self.request_source,
                application_version=str(self.app.config["VERSION"]),
            )
            db.session.add(record)
            db.session.flush()
            record_id = record.id
            db.session.commit()
            self.record_id = record_id
        except Exception:
            self._rollback_session()
            self.app.logger.error(
                "Unable to create archive search telemetry record",
                exc_info=True,
            )

    def _persist_final_state(self):
        """Best-effort finalization that never replaces the search outcome."""
        if self.record_id is None:
            return

        # A failed PostgreSQL statement leaves the session unusable until rollback.
        # Search results are plain dictionaries, so resetting this transaction is safe.
        self._rollback_session()
        try:
            record = db.session.get(ArchiveSearchRunModel, self.record_id)
            if record is None:
                self.app.logger.error(
                    "Archive search telemetry record %s was not found during finalization",
                    self.record_id,
                )
                return

            record.status = self.status
            record.duration_ms = self.duration_ms
            if self.status == self.STATUS_SUCCESSFUL and self.search_data is not None:
                record.returned_result_count = len(self.search_data["results"])
                record.coverage_summary = dict(self.search_data["coverage"])

            db.session.commit()
        except Exception:
            self._rollback_session()
            self.app.logger.error(
                "Unable to finalize archive search telemetry record %s",
                self.record_id,
                exc_info=True,
            )

    def _rollback_session(self):
        """Best-effort rollback used by telemetry paths that must not mask a search."""
        try:
            db.session.rollback()
        except Exception:
            self.app.logger.error(
                "Unable to roll back the database session during search telemetry",
                exc_info=True,
            )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        """Return non-negative monotonic elapsed time rounded to milliseconds."""
        return max(0, round((time.perf_counter() - started_at) * 1000))

    def _execute_search(self) -> dict:
        """Run the archive search workflow and return its result data."""
        query_text = self.request.query_text
        mode = self.request.search_mode
        extensions = list(self.request.extensions)
        scope = resolve_scope(self.request, self.app)
        warnings = list(scope.warnings)
        messages = list(scope.messages)
        if scope.roots_with_no_indexed_files:
            messages.append(
                f"{len(scope.roots_with_no_indexed_files)} scope root(s) did not match any indexed file locations."
            )

        content_rows = []
        filepath_rows = []

        if mode in ["content", "combined"] and _scope_allows_search(scope):
            content_rows = _execute_content_search(
                query_text,
                scope,
                extensions,
                self.file_limit,
                self.app,
            )
        elif mode in ["content", "combined"] and scope.scope_type != "all" and not scope.prefixes:
            messages.append(
                "Document-content search was skipped because the selected scope resolved to no usable root paths."
            )

        if mode in ["filename_only", "filepath", "combined"] and _scope_allows_search(scope):
            filepath_rows = _execute_filename_search(
                query_text,
                scope,
                extensions,
                self.file_limit,
                mode == "filename_only",
            )
        elif mode in ["filename_only", "filepath", "combined"] and scope.scope_type != "all" and not scope.prefixes:
            messages.append(
                "Filename/path search was skipped because the selected scope resolved to no usable root paths."
            )

        results = _merge_results(content_rows, filepath_rows, self.file_limit)
        file_hashes = [row["file_hash"] for row in results]
        metadata = _fetch_file_metadata(file_hashes)
        locations = _fetch_locations(
            file_hashes,
            self.app.config.get("USER_ARCHIVES_LOCATION"),
            scope,
        )
        snippets = _fetch_snippets(
            query_text,
            [row["best_chunk_id"] for row in results if row.get("best_chunk_id")],
        )

        for row in results:
            meta = metadata.get(row["file_hash"], {})
            file_locations = locations.get(row["file_hash"], [])
            primary = _select_primary_location(row, file_locations) or {}
            text_status = _status_from_metadata(meta) if meta else "not_attempted"
            row.update({
                "filename": primary.get("filename") or "",
                "extension": meta.get("extension") or "",
                "size_bytes": meta.get("size_bytes"),
                "size_display": _format_size(meta.get("size_bytes")),
                "primary_location": primary.get("user_path") or "",
                "primary_location_id": primary.get("location_id"),
                "additional_location_count": max(len(file_locations) - 1, 0),
                "locations": file_locations,
                "snippet": snippets.get(row.get("best_chunk_id"), ""),
                "text_status": text_status,
                "text_status_label": status_label(text_status),
                "text_length": meta.get("text_length"),
                "failure_stage": meta.get("failure_stage") or "",
                "failure_summary": meta.get("failure_summary") or "",
                "matching_location_ids": row.get("matching_location_ids") or set(),
            })

        coverage = _coverage_summary(scope, extensions, self.app)
        return {
            "query_text": query_text,
            "search_mode": mode,
            "search_mode_label": SEARCH_MODE_LABELS.get(mode, mode),
            "scope": scope,
            "scope_label": scope.label,
            "extension": ", ".join(extensions),
            "extensions": extensions,
            "results": results,
            "coverage": coverage,
            "messages": messages,
            "warnings": warnings,
            "limit_hit": len(results) >= self.file_limit,
        }


def build_archive_search_api_response(
    search_data: dict,
    search_run_id: int | None,
    result_limit: int,
) -> dict:
    """Serialize archive-search results for programmatic clients without HTML or Excel data."""
    scope = search_data["scope"]
    results = []
    for result in search_data["results"]:
        snippet = html.unescape(re.sub(r"</?mark>", "", result.get("snippet") or ""))
        results.append({
            "result_rank": result["result_rank"],
            "file_hash": result["file_hash"],
            "filename": result["filename"],
            "extension": result["extension"],
            "size_bytes": result["size_bytes"],
            "primary_location": result["primary_location"],
            "additional_location_count": result["additional_location_count"],
            "match_source": result["match_source"],
            "content_rank": float(result["content_rank"]) if result["content_rank"] is not None else None,
            "filepath_rank": float(result["filepath_rank"]) if result["filepath_rank"] is not None else None,
            "matching_chunks": result["matching_chunks"],
            "snippet": snippet,
            "text_status": result["text_status"],
            "text_length": result["text_length"],
        })

    return {
        "search_run_id": search_run_id,
        "query_text": search_data["query_text"],
        "search_mode": search_data["search_mode"],
        "extensions": search_data["extensions"],
        "scope": {
            "type": scope.scope_type,
            "display_value": scope.display_value,
            "resolved_prefixes": scope.prefixes,
        },
        "results": results,
        "returned_result_count": len(results),
        "result_limit": result_limit,
        "limit_hit": search_data["limit_hit"],
        "coverage": search_data["coverage"],
        "messages": search_data["messages"],
        "warnings": search_data["warnings"],
    }


def archive_search_api_result_limit(payload: dict, maximum_limit: int) -> int:
    """Validate the optional API result limit without allowing more than the server maximum."""
    result_limit = payload.get("limit", maximum_limit)
    if isinstance(result_limit, bool) or not isinstance(result_limit, int):
        raise ArchiveSearchAPIValidationError("limit must be an integer.")
    if result_limit < 1 or result_limit > maximum_limit:
        raise ArchiveSearchAPIValidationError(
            f"limit must be between 1 and {maximum_limit}."
        )
    return result_limit


def build_archive_search_workbook(search_data: dict, generated_at: datetime) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build Results, Locations, and Coverage dataframes for Excel export."""
    result_rows = []
    location_rows = []
    for result in search_data["results"]:
        result_rows.append({
            "result_rank": result["result_rank"],
            "match_source": result["match_source"],
            "file_hash": result["file_hash"],
            "filename": result["filename"],
            "extension": result["extension"],
            "size_bytes": result["size_bytes"],
            "size_display": result["size_display"],
            "primary_location": result["primary_location"],
            "additional_location_count": result["additional_location_count"],
            "content_rank": result["content_rank"],
            "filepath_rank": result["filepath_rank"],
            "matching_chunks": result["matching_chunks"],
            "snippet": re.sub(r"</?mark>", "", result["snippet"] or ""),
            "text_status": result["text_status"],
            "text_status_label": result["text_status_label"],
            "text_length": result["text_length"],
            "failure_stage": result["failure_stage"],
            "failure_summary": result["failure_summary"],
        })
        matching_location_ids = result.get("matching_location_ids") or set()
        for location_rank, location in enumerate(result["locations"], start=1):
            location_rows.append({
                "file_hash": result["file_hash"],
                "location_rank": location_rank,
                "in_scope": location["in_scope"],
                "location_matched_query": location["location_id"] in matching_location_ids,
                "filename": location["filename"],
                "file_server_directories": location["file_server_directories"],
                "user_path": location["user_path"],
                "existence_confirmed": location["existence_confirmed"],
                "hash_confirmed": location["hash_confirmed"],
            })

    scope = search_data["scope"]
    coverage_rows = [
        {"field": "query_text", "value": search_data["query_text"]},
        {"field": "search_mode", "value": search_data["search_mode_label"]},
        {"field": "file_extensions", "value": search_data["extension"]},
        {"field": "scope_type", "value": scope.scope_type},
        {"field": "scope_display_value", "value": scope.display_value},
        {"field": "scope_roots", "value": "\n".join(scope.prefixes)},
        {"field": "project_rows_found", "value": scope.project_count},
        {"field": "usable_project_roots", "value": scope.usable_project_count},
        {"field": "project_rows_missing_file_server_location", "value": scope.missing_project_location_count},
        {"field": "caan_found", "value": scope.caan_found},
        {"field": "linked_project_rows", "value": scope.linked_project_count},
        {"field": "roots_with_no_indexed_files", "value": "\n".join(scope.roots_with_no_indexed_files)},
        {"field": "generated_at", "value": generated_at.strftime("%Y-%m-%d %H:%M:%S")},
        {"field": "limit_hit", "value": search_data["limit_hit"]},
    ]
    coverage_rows.extend(
        {"field": key, "value": value}
        for key, value in search_data["coverage"].items()
    )
    coverage_rows.extend(
        {"field": "message", "value": message}
        for message in search_data["messages"]
    )
    coverage_rows.extend(
        {"field": "warning", "value": warning}
        for warning in search_data["warnings"]
    )

    return (
        _sanitize_excel_dataframe(pd.DataFrame(result_rows)),
        _sanitize_excel_dataframe(pd.DataFrame(location_rows)),
        _sanitize_excel_dataframe(pd.DataFrame(coverage_rows)),
    )
