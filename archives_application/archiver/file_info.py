"""Read-only data access and serialization for archive file information."""

from pathlib import PureWindowsPath

from sqlalchemy import text

from archives_application import db, utils
from archives_application.archiver import archive_search


DEFAULT_TEXT_WINDOW_CHARS = 10_000
DEFAULT_DATE_MENTION_LIMIT = 50
DATE_MENTION_SUMMARY_ITEM_LIMIT = 5


class FileInfoAPIValidationError(ValueError):
    """Raised when an API file selector or option is invalid."""


class AmbiguousFileLocationError(ValueError):
    """Raised when one user path is indexed under multiple file hashes."""


def can_view_file_text(user) -> bool:
    """Return whether a user may view extracted text.

    V1 allows every authenticated user.  Keeping this as a named policy helper
    makes a later role-based policy a local change.
    """
    return bool(getattr(user, "is_authenticated", False))


def _configured_positive_int(app, setting_name: str, default: int) -> int:
    """Read a positive integer setting without accepting an invalid limit."""
    try:
        configured_value = int(app.config.get(setting_name, default))
    except (TypeError, ValueError):
        return default
    return configured_value if configured_value > 0 else default


def text_window_chars(app) -> int:
    """Return the server-controlled extracted-text display limit."""
    return _configured_positive_int(
        app,
        "FILE_INFO_TEXT_WINDOW_CHARS",
        DEFAULT_TEXT_WINDOW_CHARS,
    )


def date_mention_limit(app) -> int:
    """Return the server-controlled document-date display limit."""
    return _configured_positive_int(
        app,
        "FILE_INFO_DATE_MENTION_LIMIT",
        DEFAULT_DATE_MENTION_LIMIT,
    )


def _location_sort_key(location: dict) -> tuple:
    return (
        len(location.get("file_server_directories") or ""),
        location.get("file_server_directories") or "",
        location.get("filename") or "",
        location.get("location_id") or 0,
    )


def _fetch_file_metadata(file_hash: str) -> dict | None:
    """Fetch file-level metadata without selecting the extracted source text."""
    sql = """
        SELECT
            f.id AS file_id,
            f.hash AS file_hash,
            f.size AS size_bytes,
            f.extension,
            fc.text_length,
            fc.updated_at AS text_updated_at,
            fcf.stage AS failure_stage,
            EXISTS (
                SELECT 1
                FROM file_content_fts_chunks c
                WHERE c.file_hash = f.hash
            ) AS has_chunks
        FROM files f
        LEFT JOIN file_contents fc ON fc.file_hash = f.hash
        LEFT JOIN file_content_failures fcf ON fcf.file_hash = f.hash
        WHERE f.hash = :file_hash
    """
    row = db.session.execute(text(sql), {"file_hash": file_hash}).mappings().first()
    return dict(row) if row else None


def _fetch_locations(
    file_hash: str,
    user_archives_location: str | None,
    include_user_paths: bool = False,
) -> list[dict]:
    """Fetch all indexed locations, optionally with user-facing paths."""
    sql = """
        SELECT
            fl.id AS location_id,
            fl.file_server_directories,
            fl.filename,
            fl.existence_confirmed,
            fl.hash_confirmed
        FROM files f
        JOIN file_locations fl ON fl.file_id = f.id
        WHERE f.hash = :file_hash
        ORDER BY
            length(coalesce(fl.file_server_directories, '')),
            fl.file_server_directories,
            fl.filename,
            fl.id
    """
    rows = db.session.execute(text(sql), {"file_hash": file_hash}).mappings().all()
    locations = []
    for row in rows:
        location = dict(row)
        if include_user_paths:
            location["user_path"] = utils.FileServerUtils.user_path_from_db_data(
                file_server_directories=location["file_server_directories"] or "",
                user_archives_location=user_archives_location,
                filename=location["filename"],
            )
        locations.append(location)
    return locations


def _fetch_date_mentions(file_hash: str, limit: int) -> list[dict]:
    """Fetch a bounded, chronological list of calendar dates for a file."""
    sql = """
        SELECT
            mention_date,
            sum(mentions_count) AS mentions_count
        FROM file_date_mentions
        WHERE file_hash = :file_hash
        GROUP BY mention_date
        ORDER BY mention_date ASC
        LIMIT :limit
    """
    rows = db.session.execute(
        text(sql),
        {"file_hash": file_hash, "limit": limit},
    ).mappings().all()
    return [dict(row) for row in rows]


def _fetch_all_date_mentions(file_hash: str) -> list[dict]:
    """Fetch every distinct detected date for API output."""
    sql = """
        SELECT
            mention_date,
            sum(mentions_count) AS mentions_count
        FROM file_date_mentions
        WHERE file_hash = :file_hash
        GROUP BY mention_date
        ORDER BY mention_date ASC
    """
    rows = db.session.execute(text(sql), {"file_hash": file_hash}).mappings().all()
    return [dict(row) for row in rows]


def _fetch_date_mention_summary(file_hash: str) -> dict:
    """Return aggregate date metrics for a file's extracted-text date mentions."""
    sql = """
        SELECT
            count(DISTINCT mention_date) AS distinct_date_count,
            coalesce(sum(mentions_count), 0) AS total_occurrences,
            min(mention_date) AS earliest_date,
            max(mention_date) AS latest_date
        FROM file_date_mentions
        WHERE file_hash = :file_hash
    """
    row = db.session.execute(text(sql), {"file_hash": file_hash}).mappings().one()
    return dict(row)


def _fetch_date_mention_highlights(
    file_hash: str,
    order_by: str,
) -> list[dict]:
    """Fetch a five-date summary list using a fixed, internal sort order."""
    order_by_clauses = {
        "earliest": "mention_date ASC",
        "latest": "mention_date DESC",
        "most_frequent": "mentions_count DESC, mention_date ASC",
    }
    sql = f"""
        SELECT
            mention_date,
            sum(mentions_count) AS mentions_count
        FROM file_date_mentions
        WHERE file_hash = :file_hash
        GROUP BY mention_date
        ORDER BY {order_by_clauses[order_by]}
        LIMIT :limit
    """
    rows = db.session.execute(
        text(sql),
        {"file_hash": file_hash, "limit": DATE_MENTION_SUMMARY_ITEM_LIMIT},
    ).mappings().all()
    return [dict(row) for row in rows]


def _fetch_text_window(file_hash: str, window_chars: int) -> str | None:
    """Fetch only the leading extracted-text window for an authenticated view."""
    sql = """
        SELECT substring(source_text FROM 1 FOR :window_chars) AS extracted_text
        FROM file_contents
        WHERE file_hash = :file_hash
    """
    row = db.session.execute(
        text(sql),
        {"file_hash": file_hash, "window_chars": window_chars},
    ).mappings().first()
    return row["extracted_text"] if row else None


def _fetch_source_text(file_hash: str) -> str | None:
    """Fetch the complete source text only for an explicit API request."""
    sql = """
        SELECT source_text
        FROM file_contents
        WHERE file_hash = :file_hash
    """
    row = db.session.execute(text(sql), {"file_hash": file_hash}).mappings().first()
    return row["source_text"] if row else None


def _fetch_file_and_locations(
    file_hash: str,
    app,
    include_user_paths: bool = False,
) -> tuple[dict | None, list[dict]]:
    """Fetch shared file metadata and its indexed locations."""
    metadata = _fetch_file_metadata(file_hash)
    if metadata is None:
        return None, []
    locations = _fetch_locations(
        file_hash=file_hash,
        user_archives_location=app.config.get("USER_ARCHIVES_LOCATION"),
        include_user_paths=include_user_paths,
    )
    return metadata, locations


def _relative_location_from_user_path(path_value: str, user_archives_location: str | None) -> tuple[str, str]:
    """Convert an exact user-facing file path to normalized DB location values."""
    if not isinstance(path_value, str) or not path_value.strip():
        raise FileInfoAPIValidationError("A non-empty user_path is required.")
    if not user_archives_location:
        raise FileInfoAPIValidationError("User archive path mapping is not configured.")

    try:
        user_path = PureWindowsPath(path_value.strip())
        user_root = PureWindowsPath(str(user_archives_location).strip())
    except (TypeError, ValueError) as error:
        raise FileInfoAPIValidationError("user_path is not a valid Windows path.") from error

    if any(part == ".." for part in user_path.parts):
        raise FileInfoAPIValidationError("user_path must not contain '..' path components.")

    try:
        relative_path = user_path.relative_to(user_root)
    except ValueError as error:
        raise FileInfoAPIValidationError(
            "user_path must be under the configured user archive root."
        ) from error

    relative_parts = [part for part in relative_path.parts if part not in {"", ".", "\\", "/"}]
    if not relative_parts:
        raise FileInfoAPIValidationError("user_path must identify a file, not the archive root.")

    return "/".join(relative_parts[:-1]), relative_parts[-1]


def resolve_user_path_to_file_hash(path_value: str, app) -> str | None:
    """Resolve a user-facing path to one canonical hash without file-server I/O."""
    file_server_directories, filename = _relative_location_from_user_path(
        path_value=path_value,
        user_archives_location=app.config.get("USER_ARCHIVES_LOCATION"),
    )
    sql = """
        SELECT DISTINCT f.hash AS file_hash
        FROM file_locations fl
        JOIN files f ON f.id = fl.file_id
        WHERE lower(trim(both '/' FROM replace(
            coalesce(fl.file_server_directories, ''), chr(92), '/'
        ))) = :file_server_directories
          AND lower(coalesce(fl.filename, '')) = :filename
        ORDER BY f.hash ASC
    """
    hashes = [
        row["file_hash"]
        for row in db.session.execute(
            text(sql),
            {
                "file_server_directories": file_server_directories.lower(),
                "filename": filename.lower(),
            },
        ).mappings().all()
    ]
    if not hashes:
        return None
    if len(hashes) > 1:
        raise AmbiguousFileLocationError("Ambiguous indexed location.")
    return hashes[0]


def _database_path(location: dict) -> str | None:
    """Join stored directory and filename values into a database-relative path."""
    directories = location.get("file_server_directories")
    filename = location.get("filename")
    if not directories:
        return filename
    if not filename:
        return directories
    separator = "\\" if "\\" in directories and "/" not in directories else "/"
    return f"{directories.rstrip('/\\')}{separator}{filename.lstrip('/\\')}"


def _iso_value(value):
    """Convert date/datetime-like values to the API's ISO-8601 representation."""
    return value.isoformat() if value is not None else None


def _serialize_api_location(location: dict, include_user_paths: bool) -> dict:
    """Serialize one indexed location in exactly one configured path form."""
    result = {
        "location_id": location.get("location_id"),
        "filename": location.get("filename"),
        "existence_confirmed": _iso_value(location.get("existence_confirmed")),
        "hash_confirmed": _iso_value(location.get("hash_confirmed")),
    }
    if include_user_paths:
        result["user_path"] = location.get("user_path")
    else:
        result["database_path"] = _database_path(location)
    return result


def get_file_info_api(
    file_hash: str,
    app,
    include_text: bool = False,
    include_user_paths: bool = False,
) -> dict | None:
    """Return the API representation for one canonical archive file."""
    metadata, locations = _fetch_file_and_locations(
        file_hash=file_hash,
        app=app,
        include_user_paths=include_user_paths,
    )
    if metadata is None:
        return None

    text_status = archive_search._status_from_metadata(metadata)
    api_data = {
        "file_hash": metadata["file_hash"],
        "size_bytes": metadata.get("size_bytes"),
        "extension": metadata.get("extension"),
        "location_count": len(locations),
        "locations": [
            _serialize_api_location(location, include_user_paths)
            for location in locations
        ],
        "text_status": text_status,
        "text_status_label": archive_search.status_label(text_status),
        "text_length": metadata.get("text_length"),
        "text_updated_at": _iso_value(metadata.get("text_updated_at")),
        "detected_dates": [
            {
                "date": _iso_value(mention.get("mention_date")),
                "occurrences": mention.get("mentions_count"),
            }
            for mention in _fetch_all_date_mentions(file_hash)
        ],
    }
    if include_text:
        api_data["source_text"] = _fetch_source_text(file_hash)
    return api_data


def get_file_info(
    file_hash: str,
    app,
    include_text: bool = False,
) -> dict | None:
    """Return the complete view model for an indexed archive file.

    The source text is deliberately queried only when ``include_text`` is true.
    """
    metadata, locations = _fetch_file_and_locations(
        file_hash=file_hash,
        app=app,
        include_user_paths=True,
    )
    if metadata is None:
        return None

    display_location = min(locations, key=_location_sort_key) if locations else None
    mention_limit = date_mention_limit(app)
    date_mention_summary = _fetch_date_mention_summary(file_hash)
    if date_mention_summary["distinct_date_count"] <= mention_limit:
        date_mentions = _fetch_date_mentions(file_hash, mention_limit)
        date_mention_highlights = {}
    else:
        date_mentions = []
        date_mention_highlights = {
            "earliest": _fetch_date_mention_highlights(file_hash, "earliest"),
            "latest": _fetch_date_mention_highlights(file_hash, "latest"),
            "most_frequent": _fetch_date_mention_highlights(file_hash, "most_frequent"),
        }

    text_status = archive_search._status_from_metadata(metadata)
    text_limit = text_window_chars(app)
    extracted_text = _fetch_text_window(file_hash, text_limit) if include_text else None
    returned_text_length = len(extracted_text) if extracted_text is not None else 0
    stored_text_length = metadata.get("text_length")

    return {
        "file_hash": metadata["file_hash"],
        "file_id": metadata["file_id"],
        "display_filename": (display_location or {}).get("filename") or "Unnamed indexed file",
        "multiple_filenames": len({location.get("filename") for location in locations}) > 1,
        "size_bytes": metadata.get("size_bytes"),
        "size_display": archive_search._format_size(metadata.get("size_bytes")),
        "extension": metadata.get("extension") or "",
        "location_count": len(locations),
        "locations": locations,
        "text_status": text_status,
        "text_status_label": archive_search.status_label(text_status),
        "text_length": stored_text_length,
        "text_updated_at": metadata.get("text_updated_at"),
        "date_mentions": date_mentions,
        "date_mention_limit": mention_limit,
        "date_mention_summary": date_mention_summary,
        "date_mention_highlights": date_mention_highlights,
        "extracted_text": extracted_text,
        "returned_text_length": returned_text_length,
        "text_window_chars": text_limit,
        "text_truncated": (
            extracted_text is not None
            and stored_text_length is not None
            and stored_text_length > returned_text_length
        ),
    }
