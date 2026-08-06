"""Read-only data access for the archive file-information page."""

from sqlalchemy import text

from archives_application import db, utils
from archives_application.archiver import archive_search


DEFAULT_TEXT_WINDOW_CHARS = 10_000
DEFAULT_DATE_MENTION_LIMIT = 100


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


def _fetch_locations(file_hash: str, user_archives_location: str | None) -> list[dict]:
    """Fetch and format all indexed locations for a canonical file hash."""
    sql = """
        SELECT
            fl.id AS location_id,
            fl.file_server_directories,
            fl.filename,
            fl.existence_confirmed
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
        location["user_path"] = utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=location["file_server_directories"] or "",
            user_archives_location=user_archives_location,
            filename=location["filename"],
        )
        locations.append(location)
    return locations


def _fetch_date_mentions(file_hash: str, limit: int) -> tuple[list[dict], bool]:
    """Fetch a bounded, chronological document-date list for a file."""
    sql = """
        SELECT
            mention_date,
            granularity,
            mentions_count
        FROM file_date_mentions
        WHERE file_hash = :file_hash
        ORDER BY mention_date ASC, granularity ASC
        LIMIT :limit_plus_one
    """
    rows = db.session.execute(
        text(sql),
        {"file_hash": file_hash, "limit_plus_one": limit + 1},
    ).mappings().all()
    has_more = len(rows) > limit
    return [dict(row) for row in rows[:limit]], has_more


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


def get_file_info(
    file_hash: str,
    app,
    include_text: bool = False,
) -> dict | None:
    """Return the complete view model for an indexed archive file.

    The source text is deliberately queried only when ``include_text`` is true.
    """
    metadata = _fetch_file_metadata(file_hash)
    if metadata is None:
        return None

    locations = _fetch_locations(
        file_hash=file_hash,
        user_archives_location=app.config.get("USER_ARCHIVES_LOCATION"),
    )
    display_location = min(locations, key=_location_sort_key) if locations else None
    mention_limit = date_mention_limit(app)
    date_mentions, date_mentions_truncated = _fetch_date_mentions(file_hash, mention_limit)

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
        "date_mentions_truncated": date_mentions_truncated,
        "date_mention_limit": mention_limit,
        "extracted_text": extracted_text,
        "returned_text_length": returned_text_length,
        "text_window_chars": text_limit,
        "text_truncated": (
            extracted_text is not None
            and stored_text_length is not None
            and stored_text_length > returned_text_length
        ),
    }
