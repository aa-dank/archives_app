"""Archive Search identity, versioned JSON, and fixed-generation cache policy."""

import copy
import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.engine import make_url

from archives_application.cache import ByteCache, CacheUnavailable, positive_integer


SCHEMA_VERSION = 1
SEARCH_FORMAT_VERSION = 1
CODEC_ERRORS = (ValueError, TypeError, KeyError, OverflowError, RecursionError)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ArchiveSearchCachePolicy:
    """Explicit consumer policy; existing API callers default to disabled."""

    enabled: bool = False
    backend: ByteCache | None = None
    namespace: str = ""
    ttl_seconds: int = 432000
    max_entry_bytes: int = 5242880
    refresh: bool = False

    def __post_init__(self):
        positive_integer(self.ttl_seconds, "ttl_seconds")
        positive_integer(self.max_entry_bytes, "max_entry_bytes")
        if type(self.enabled) is not bool or type(self.refresh) is not bool:
            raise ValueError("Cache enable/refresh policy must be boolean")
        if self.enabled and (self.backend is None or not isinstance(self.namespace, str) or not self.namespace.strip()):
            raise ValueError("An enabled policy requires a backend and namespace")


def browser_cache_policy(app, refresh=False, ttl_seconds=None) -> ArchiveSearchCachePolicy:
    """Resolve the default or a server-side override before lookup/generation."""
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
    generation_id: str
    generated_at: datetime
    expires_at: datetime | None = None
    cache_hit: bool = False


def fresh_generation() -> SearchGeneration:
    return SearchGeneration(uuid4().hex, utc_now())


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def identity_digest(request, config, file_limit: int, ttl_seconds: int) -> str:
    """Hash normalized inputs and effective settings, excluding secrets/viewer state."""
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


def _require(condition):
    if not condition:
        raise ValueError("Invalid Archive Search cache payload")


def _fields(value, names):
    _require(type(value) is dict and set(value) == set(names))


def _strings(value):
    _require(type(value) is list and all(type(item) is str for item in value))


def _count(value, nullable=False):
    _require((nullable and value is None) or (type(value) is int and value >= 0))


def _datetime(value, utc=False):
    _require(type(value) is str)
    parsed = datetime.fromisoformat(value)
    if utc:
        _require(parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0))
    return parsed


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


def encode_search_data(data, file_limit):
    """Explicit conversions for scope, membership sets, ranks, and DB datetimes."""
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
    decode_search_data(copy.deepcopy(wire), file_limit)
    return wire


def decode_search_data(wire, file_limit):
    """Reject incomplete/incompatible data before it can reach templates or exports."""
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


def remaining_seconds(expires_at) -> int:
    """Round down: delayed writes must never extend the source expiration."""
    return max(0, math.floor((expires_at - utc_now()).total_seconds()))


class ArchiveSearchCache:
    def __init__(self, policy, request, app, file_limit):
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

    def log(self, event, generation=None, **fields):
        details = {
            "consumer": "archive-search", "namespace": self.policy.namespace,
            "identity_digest": self.digest, "ttl_seconds": self.policy.ttl_seconds,
        }
        if generation is not None:
            details.update(generation_id=generation.generation_id,
                           source_age_seconds=max(0, (utc_now() - generation.generated_at).total_seconds()))
        details.update(fields)
        self.app.logger.info("archive_search_cache %s", json.dumps({"event": event, **details}, sort_keys=True))

    def read(self, key):
        if not self.available:
            return None
        try:
            value = self.policy.backend.get(key)
        except CacheUnavailable:
            self.available = False
            self.log("backend_failure", operation="read")
            return None
        if value is not None and (not isinstance(value, bytes) or len(value) > self.policy.max_entry_bytes):
            self.log("oversized_or_invalid_entry", payload_bytes=len(value) if isinstance(value, bytes) else None)
            return None
        return value

    def write(self, key, payload, generation):
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

    def lookup(self):
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
            _require(data["query_text"] == self.request.query_text and data["search_mode"] == self.request.search_mode
                     and data["extensions"] == list(self.request.extensions)
                     and (data["scope"].scope_type == self.request.requested_scope_type
                          or (self.request.requested_scope_type == "location" and data["scope"].scope_type == "all")))
        except CODEC_ERRORS:
            self.log("invalid_payload", payload_bytes=len(payload))
            return None
        generation = SearchGeneration(envelope["generation_id"], generated, expires, True)
        self.log("lookup_hit", generation, payload_bytes=len(payload),
                 elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
        return data, generation

    def publish(self, data, generation):
        expires = generation.generated_at + timedelta(seconds=self.policy.ttl_seconds)
        cached = SearchGeneration(generation.generation_id, generation.generated_at, expires)
        started = time.perf_counter()
        try:
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

    def workbook_key(self, generation):
        return f"{self.prefix}:workbook:{generation.generation_id}"

    def workbook_timestamp(self, generation):
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

    def publish_workbook(self, generation, timestamp):
        self.write(self.workbook_key(generation), _json_bytes({"storage_kind": "local-temp", "timestamp": timestamp}),
                   generation)
