"""Archive Search identity, versioned JSON, and fixed-generation cache policy."""

import copy
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.engine import make_url

from archives_application.cache import ByteCache, CacheUnavailable, positive_integer

if TYPE_CHECKING:
    from archives_application.archiver.archive_search import ArchiveSearchRequest


SCHEMA_VERSION = 1
SEARCH_FORMAT_VERSION = 1
# Only cache codec failures belong here; database/search exceptions must retain
# their existing failure response and telemetry rather than becoming cache misses.
CODEC_ERRORS = (ValueError, TypeError, KeyError, OverflowError, RecursionError)


def utc_now() -> datetime:
    """Return an aware UTC instant through the cache's replaceable clock boundary."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ArchiveSearchCachePolicy:
    """Immutable per-operation cache settings, disabled unless explicitly supplied.

    ttl_seconds belongs to the result identity; refresh bypasses lookup without
    changing that identity. Existing API callers use the disabled default.
    """

    enabled: bool = False
    backend: ByteCache | None = None
    namespace: str = ""
    ttl_seconds: int = 432000
    max_entry_bytes: int = 5242880
    refresh: bool = False

    def __post_init__(self) -> None:
        """Reject ambiguous lifetime/size types and incomplete enabled policies."""
        positive_integer(self.ttl_seconds, "ttl_seconds")
        positive_integer(self.max_entry_bytes, "max_entry_bytes")
        if type(self.enabled) is not bool or type(self.refresh) is not bool:
            raise ValueError("Cache enable/refresh policy must be boolean")
        if self.enabled and (self.backend is None or not isinstance(self.namespace, str) or not self.namespace.strip()):
            raise ValueError("An enabled policy requires a backend and namespace")


def browser_cache_policy(app, refresh=False, ttl_seconds=None) -> ArchiveSearchCachePolicy:
    """Resolve browser defaults or a server-side TTL override before lookup.

    Invalid configuration or a lifetime that outlasts workbook retention returns
    a disabled policy so fresh searches remain usable. Request parameters never
    select the lifetime.
    """
    if app.config.get("ARCHIVE_SEARCH_CACHE_ENABLED", False) is not True:
        return ArchiveSearchCachePolicy()
    try:
        ttl = positive_integer(
            app.config.get("ARCHIVE_SEARCH_CACHE_TTL_SECONDS", 432000)
            if ttl_seconds is None else ttl_seconds,
            "ARCHIVE_SEARCH_CACHE_TTL_SECONDS",
        )
        retention = positive_integer(
            app.config.get("ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS", 518400),
            "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS",
        )
        if retention <= ttl:
            raise ValueError("Workbook retention must exceed the effective search lifetime")
        backend = app.extensions.get("cache")
        if backend is None:
            return ArchiveSearchCachePolicy()
        return ArchiveSearchCachePolicy(
            enabled=True, backend=backend, namespace=app.config.get("CACHE_NAMESPACE", ""),
            ttl_seconds=ttl, max_entry_bytes=app.config.get("CACHE_MAX_ENTRY_BYTES", 5242880),
            refresh=refresh,
        )
    except (ValueError, TypeError) as error:
        app.logger.error("archive_search_cache state=disabled reason=invalid_policy detail=%s", error)
        return ArchiveSearchCachePolicy()


@dataclass(frozen=True)
class SearchGeneration:
    """Source execution identity and UTC freshness metadata, separate from results.

    expires_at is set only for a generation read from or successfully published
    to the cache. A failed publication remains an ordinary fresh generation.
    """
    generation_id: str
    generated_at: datetime
    expires_at: datetime | None = None
    cache_hit: bool = False


def fresh_generation() -> SearchGeneration:
    """Identify a successfully completed search before any cache publication."""
    return SearchGeneration(uuid4().hex, utc_now())


def _json_bytes(value) -> bytes:
    """Encode deterministic UTF-8 JSON, preserving Unicode and rejecting nonfinite numbers."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def identity_digest(request: "ArchiveSearchRequest", config, file_limit: int, ttl_seconds: int) -> str:
    """Hash validated inputs, effective search settings, and non-secret data targets.

    Use the existing request normalization without further case/space changes.
    Preserve extension order because it appears in the page/workbook; exclude
    HTML limits, viewer state, refresh, and workbook IDs because they do not
    select source data.
    """
    target = make_url(config["SQLALCHEMY_DATABASE_URI"])
    identity = {
        "schema_version": SCHEMA_VERSION,
        "search_format_version": SEARCH_FORMAT_VERSION,
        "query_text": request.query_text,
        "search_mode": request.search_mode,
        "scope_type": request.requested_scope_type,
        "scope_value": request.requested_scope_value,
        "extensions": list(request.extensions),
        "file_limit": file_limit,
        "candidate_limit": int(config.get("ARCHIVE_SEARCH_CHUNK_CANDIDATE_LIMIT", 50000)),
        "candidate_multiplier": int(config.get("ARCHIVE_SEARCH_CHUNK_CANDIDATE_MULTIPLIER", 20)),
        "ttl_seconds": ttl_seconds,
        "archives_location": config.get("ARCHIVES_LOCATION"),
        "user_archives_location": config.get("USER_ARCHIVES_LOCATION"),
        "database_target": {
            "driver": target.drivername, "host": target.host,
            "port": target.port or (5432 if target.drivername.startswith("postgresql") else None),
            "database": target.database,
            # libpq can select a target through query parameters (e.g. a Unix socket).
            "target_options": {key: target.query[key] for key in ("host", "port", "dbname")
                               if key in target.query},
        },
    }
    return hashlib.sha256(_json_bytes(identity)).hexdigest()


def _require(condition: bool) -> None:
    """Turn a failed codec invariant into a cache-local validation error."""
    if not condition:
        raise ValueError("Invalid Archive Search cache payload")


def _fields(value, names) -> None:
    """Require the exact dictionary fields supported by this schema version."""
    _require(type(value) is dict and set(value) == set(names))


def _strings(value) -> None:
    """Require a JSON array containing only strings, retaining order and whitespace."""
    _require(type(value) is list and all(type(item) is str for item in value))


def _count(value, nullable: bool = False) -> None:
    """Require a nonnegative integer count/ID, excluding bool, or an allowed null."""
    _require((nullable and value is None) or (type(value) is int and value >= 0))


def _datetime(value, utc: bool = False) -> datetime:
    """Parse ISO datetimes, preserving DB timezone state or requiring UTC metadata."""
    _require(type(value) is str)
    parsed = datetime.fromisoformat(value)
    if utc:
        _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
    return parsed


# These field groups define the complete v1 contract. Adding/removing result or
# scope fields requires reviewing the codec and bumping the schema as needed.
SCOPE_STRINGS = ("scope_type", "display_value")
SCOPE_LISTS = ("prefixes", "messages", "warnings", "roots_with_no_indexed_files")
SCOPE_COUNTS = ("project_count", "usable_project_count", "missing_project_location_count", "linked_project_count")
DATA_STRINGS = ("query_text", "search_mode", "search_mode_label", "scope_label", "extension")
RESULT_STRINGS = (
    "file_hash", "match_source", "filename", "extension", "size_display", "primary_location",
    "snippet", "text_status", "text_status_label", "failure_stage", "failure_summary",
)
RESULT_COUNTS = ("result_rank", "matching_chunks", "additional_location_count")
RESULT_NULLABLE_COUNTS = ("best_chunk_id", "best_location_id", "size_bytes", "text_length")
RESULT_RANKS = ("content_rank", "filepath_rank")
LOCATION_STRINGS = ("file_hash", "filename", "user_path")
LOCATION_DATES = ("existence_confirmed", "hash_confirmed")
COVERAGE_FIELDS = (
    "files_in_scope", "files_with_nonempty_text", "files_with_fts_chunks", "files_with_extraction_failures",
    "files_with_empty_or_thin_text", "files_with_low_context_text", "files_with_text_not_chunked",
    "files_not_attempted", "unsupported_or_low_value_format_files", "content_searchable_files",
    "filename_only_or_not_content_searchable_files", "filename_path_searchable_files", "roots_with_no_indexed_files",
)


def encode_search_data(data: dict, file_limit: int) -> dict:
    """Return an independent, validated JSON structure for the complete bounded result.

    Convert scope facts, sorted membership sets, finite ranks, and ISO DB
    datetimes explicitly. Preserve naive/aware timestamps and never mutate the
    source data or omit locations to meet a storage limit.
    """
    wire = copy.deepcopy(data)
    wire["scope"] = asdict(data["scope"])
    for result in wire["results"]:
        ids = result["matching_location_ids"]
        _require(type(ids) is set and all(type(item) is int for item in ids))
        result["matching_location_ids"] = sorted(ids)
        for field in RESULT_RANKS:
            if result[field] is not None:
                _require(not isinstance(result[field], bool))
                result[field] = float(result[field])
        for location in result["locations"]:
            for field in LOCATION_DATES:
                if location[field] is not None:
                    _require(isinstance(location[field], datetime))
                    location[field] = location[field].isoformat()
    # Reuse the read-side validator on a disposable copy: decoding reconstructs
    # runtime types in place, while this function must return JSON-compatible data.
    decode_search_data(copy.deepcopy(wire), file_limit)
    return wire


def decode_search_data(wire: dict, file_limit: int) -> dict:
    """Validate and reconstruct a newly parsed JSON dictionary in place.

    The caller owns this disposable dictionary. Restore ScopeResolution, set
    membership, and confirmation datetimes only after validating their wire
    types; reject incomplete fields, invalid ranks, and results over file_limit.
    """
    # Import lazily because the search service itself imports this cache module.
    from archives_application.archiver.archive_search import ScopeResolution, SEARCH_MODE_LABELS, SCOPE_LABELS

    _fields(wire, (*DATA_STRINGS, "scope", "extensions", "results", "coverage", "messages", "warnings", "limit_hit"))
    for key in DATA_STRINGS:
        _require(type(wire[key]) is str)
    _require(wire["search_mode"] in SEARCH_MODE_LABELS)
    for key in ("extensions", "messages", "warnings"):
        _strings(wire[key])
    _require(type(wire["limit_hit"]) is bool)
    scope = wire["scope"]
    _fields(scope, (*SCOPE_STRINGS, *SCOPE_LISTS, *SCOPE_COUNTS, "caan_found"))
    for key in SCOPE_STRINGS:
        _require(type(scope[key]) is str)
    _require(scope["scope_type"] in SCOPE_LABELS)
    for key in SCOPE_LISTS:
        _strings(scope[key])
    for key in SCOPE_COUNTS:
        _count(scope[key])
    _require(scope["caan_found"] is None or type(scope["caan_found"]) is bool)
    wire["scope"] = ScopeResolution(**scope)
    _fields(wire["coverage"], COVERAGE_FIELDS)
    for value in wire["coverage"].values():
        _count(value)
    results = wire["results"]
    _require(type(results) is list and len(results) <= file_limit)
    for index, result in enumerate(results, 1):
        _fields(result, (*RESULT_STRINGS, *RESULT_COUNTS, *RESULT_NULLABLE_COUNTS, *RESULT_RANKS,
                         "matching_location_ids", "locations"))
        for key in RESULT_STRINGS:
            _require(type(result[key]) is str)
        _require(result["match_source"] in {"both", "content", "filename/path"})
        for key in RESULT_COUNTS:
            _count(result[key])
        _require(result["result_rank"] == index)
        for key in RESULT_NULLABLE_COUNTS:
            _count(result[key], nullable=True)
        for key in RESULT_RANKS:
            value = result[key]
            _require(value is None or (type(value) in (int, float) and math.isfinite(value)))
        ids = result["matching_location_ids"]
        _require(type(ids) is list)
        for value in ids:
            _count(value)
        _require(len(set(ids)) == len(ids))
        result["matching_location_ids"] = set(ids)
        _require(type(result["locations"]) is list)
        for location in result["locations"]:
            _fields(location, (*LOCATION_STRINGS, *LOCATION_DATES, "file_server_directories", "location_id", "in_scope"))
            for key in LOCATION_STRINGS:
                _require(type(location[key]) is str)
            _require(location["file_hash"] == result["file_hash"])
            _require(location["file_server_directories"] is None or type(location["file_server_directories"]) is str)
            _count(location["location_id"])
            _require(type(location["in_scope"]) is bool)
            for key in LOCATION_DATES:
                if location[key] is not None:
                    location[key] = _datetime(location[key])
    return wire


def remaining_seconds(expires_at: datetime) -> int:
    """Round down: delayed writes must never extend the source expiration."""
    return max(0, math.floor((expires_at - utc_now()).total_seconds()))


class ArchiveSearchCache:
    """Manage one request's bounded result and generation-specific artifact entries.

    Backend failures disable further cache I/O for this request. Reads do not
    renew TTLs, and every publication uses only the source's remaining lifetime.
    Search execution and telemetry remain the search service's responsibility.
    """

    def __init__(self, policy: ArchiveSearchCachePolicy, request: "ArchiveSearchRequest", app, file_limit: int) -> None:
        """Validate effective retention and construct this operation's cache identity."""
        self.policy, self.request, self.app, self.file_limit = policy, request, app, file_limit
        positive_integer(file_limit, "file_limit")
        retention = positive_integer(app.config.get("ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS", 518400),
                                     "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS")
        if retention <= policy.ttl_seconds:
            raise ValueError("Workbook retention must exceed the effective search lifetime")
        self.digest = identity_digest(request, app.config, file_limit, policy.ttl_seconds)
        self.prefix = f"{policy.namespace}:archive-search:v{SCHEMA_VERSION}"
        self.result_key = f"{self.prefix}:result:{self.digest}"
        self.available = True

    def log(self, event: str, generation: SearchGeneration | None = None, **fields) -> None:
        """Record an outcome using identifiers, counters, and timings only.

        Callers must not supply raw queries, paths, credentials, or payloads in
        fields. Namespace and identity digest distinguish environments/searches.
        """
        details = {
            "consumer": "archive-search", "namespace": self.policy.namespace,
            "identity_digest": self.digest, "ttl_seconds": self.policy.ttl_seconds,
        }
        if generation is not None:
            details.update(generation_id=generation.generation_id,
                           source_age_seconds=max(0, (utc_now() - generation.generated_at).total_seconds()))
        details.update(fields)
        self.app.logger.info("archive_search_cache %s", json.dumps({"event": event, **details}, sort_keys=True))

    def read(self, key: str) -> bytes | None:
        """Return bounded bytes or None; stop further cache I/O after an outage."""
        if not self.available:
            return None
        try:
            value = self.policy.backend.get(key)
        except CacheUnavailable:
            # Avoid paying the same failing connection cost again during result
            # publication and workbook-reference handling for this request.
            self.available = False
            self.log("backend_failure", operation="read")
            return None
        if value is not None and (not isinstance(value, bytes) or len(value) > self.policy.max_entry_bytes):
            self.log("oversized_or_invalid_entry", payload_bytes=len(value) if isinstance(value, bytes) else None)
            return None
        return value

    def write(self, key: str, payload: bytes, generation: SearchGeneration) -> bool:
        """Publish complete bounded bytes within a cached generation's remaining TTL.

        Return False for exhausted lifetime, oversize, or backend failure. Never
        delete an earlier entry before replacement or renew the source lifetime.
        """
        if not self.available:
            return False
        if len(payload) > self.policy.max_entry_bytes:
            self.log("oversized_entry", generation, payload_bytes=len(payload))
            return False
        ttl = remaining_seconds(generation.expires_at)
        if ttl == 0:
            self.log("write_skipped_expired", generation)
            return False
        try:
            self.policy.backend.set(key, payload, ttl)
        except CacheUnavailable:
            self.available = False
            self.log("backend_failure", generation, operation="write")
            return False
        self.log("write_success", generation, payload_bytes=len(payload), remaining_ttl_seconds=ttl)
        return True

    def lookup(self) -> tuple[dict, SearchGeneration] | None:
        """Return independent results and source metadata for a valid hit, else None.

        Refresh bypasses lookup. Schema, identity, source lifetime, and the full
        bounded result are validated; malformed or expired entries are misses.
        """
        if self.policy.refresh:
            self.log("refresh")
            return None
        started = time.perf_counter()
        payload = self.read(self.result_key)
        if payload is None:
            self.log("lookup_miss", elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
            return None
        try:
            envelope = json.loads(payload)
            _fields(envelope, ("schema_version", "identity_digest", "generation_id", "generated_at",
                               "ttl_seconds", "expires_at", "search_data"))
            _require(type(envelope["schema_version"]) is int and envelope["schema_version"] == SCHEMA_VERSION)
            _require(envelope["identity_digest"] == self.digest)
            _require(type(envelope["generation_id"]) is str and len(envelope["generation_id"]) == 32
                     and all(char in "0123456789abcdef" for char in envelope["generation_id"]))
            _require(type(envelope["ttl_seconds"]) is int and envelope["ttl_seconds"] == self.policy.ttl_seconds)
            generated = _datetime(envelope["generated_at"], utc=True)
            expires = _datetime(envelope["expires_at"], utc=True)
            _require(expires == generated + timedelta(seconds=self.policy.ttl_seconds))
            _require(generated <= utc_now() < expires)
            data = decode_search_data(envelope["search_data"], self.file_limit)
            # Location input at the Records root intentionally resolves to all
            # archives while retaining its requested cache identity.
            scope_type_matches = (
                data["scope"].scope_type == self.request.requested_scope_type
                or (self.request.requested_scope_type == "location" and data["scope"].scope_type == "all")
            )
            _require(data["query_text"] == self.request.query_text and data["search_mode"] == self.request.search_mode
                     and data["extensions"] == list(self.request.extensions)
                     and scope_type_matches)
            # Decoding a large entry can cross expiration even if the initial
            # freshness check passed. Do not return it as a hit after that point.
            _require(utc_now() < expires)
        except CODEC_ERRORS:
            self.log("invalid_payload", payload_bytes=len(payload))
            return None
        generation = SearchGeneration(envelope["generation_id"], generated, expires, True)
        self.log("lookup_hit", generation, payload_bytes=len(payload),
                 elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        return data, generation

    def publish(self, data: dict, generation: SearchGeneration) -> SearchGeneration:
        """Attempt atomic result replacement after success, returning its freshness state.

        Failed publication leaves the fresh generation usable. Never delete a
        previous entry before attempting atomic replacement; a backend timeout
        can make the write's acknowledgement uncertain without exposing partial data.
        """
        started = time.perf_counter()
        try:
            expires = generation.generated_at + timedelta(seconds=self.policy.ttl_seconds)
            cached = SearchGeneration(generation.generation_id, generation.generated_at, expires)
            payload = _json_bytes({
                "schema_version": SCHEMA_VERSION, "identity_digest": self.digest,
                "generation_id": generation.generation_id, "generated_at": generation.generated_at.isoformat(),
                "ttl_seconds": self.policy.ttl_seconds, "expires_at": expires.isoformat(),
                "search_data": encode_search_data(data, self.file_limit),
            })
        except CODEC_ERRORS:
            self.log("serialization_failure", generation)
            return generation
        self.log("serialized", cached, payload_bytes=len(payload),
                 elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        return cached if self.write(self.result_key, payload, cached) else generation

    def workbook_key(self, generation: SearchGeneration) -> str:
        """Key an artifact by generation so a late export cannot replace newer results."""
        return f"{self.prefix}:workbook:{generation.generation_id}"

    def workbook_timestamp(self, generation: SearchGeneration) -> str | None:
        """Read a validated local-temp download ID; the page service checks file presence."""
        from archives_application.archiver.archive_search import valid_workbook_timestamp
        payload = self.read(self.workbook_key(generation))
        if payload is None:
            return None
        try:
            reference = json.loads(payload)
            _fields(reference, ("storage_kind", "timestamp"))
            _require(reference["storage_kind"] == "local-temp" and valid_workbook_timestamp(reference["timestamp"]))
            return reference["timestamp"]
        except CODEC_ERRORS:
            self.log("invalid_workbook_reference", generation)
            return None

    def publish_workbook(self, generation: SearchGeneration, timestamp: str) -> None:
        """Publish a completed workbook reference using only the source's remaining TTL."""
        self.write(self.workbook_key(generation), _json_bytes({"storage_kind": "local-temp", "timestamp": timestamp}),
                   generation)
