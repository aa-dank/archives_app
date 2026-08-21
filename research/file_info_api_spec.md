# File Information API Specification

Last updated: 2026-08-19

## Purpose

Provide an authenticated JSON equivalent of the archive file-information view.
The endpoint identifies one canonical file by its `files.hash` value and returns
its stored metadata, all indexed locations, text/indexing metadata, and
detected-date data.  Extracted source text is optional because it can be very
large.

This is a read-only API.  It does not read from the SMB/file server and does
not create, update, delete, or enqueue any archive records.

## Endpoint

```
GET /api/files?file_hash={file_hash}
GET /api/files?user_path={URL-encoded-user-path}
```

The request must select a file using exactly one of `file_hash` or `user_path`.
`file_hash` is the canonical value stored in `files.hash`, not a location ID.
`user_path` is a complete user-facing Windows/UNC file path and must be URL
encoded when it contains URL-reserved characters such as `#`, `?`, or `%`.

A hash is the canonical file identity.  A single file hash can have multiple
`file_locations` rows; the response contains every indexed location for that
hash.  A user path identifies one indexed location, which is first resolved to
its canonical file hash and then returns that same canonical-file response.

Do not place either selector in a variable URL path segment.  Windows paths
contain separators and can resemble other identifiers, so query selectors are
less ambiguous and easier for clients to construct correctly.

The endpoint intentionally has no `/v1` prefix at introduction so it follows
the existing application API route convention.  If a backward-incompatible
version is needed later, introduce a versioned route then rather than changing
this response silently.

## Authentication and Authorization

Every request must be authenticated before file existence or metadata is
looked up.  An unauthenticated or invalid request returns `401 Unauthorized`.

The endpoint accepts either an authenticated application session or HTTP Basic
credentials in the `Authorization` request header.  Basic credentials use an
active application's user email and password and therefore require HTTPS in
deployment.  The endpoint must not accept an application password in a query
parameter.

All authenticated archive API users may read file metadata and source text in
the first version.  Keep this check as a named policy helper so a later
role-based text rule can be applied consistently.

Responses containing source text must send:

```
Cache-Control: private, no-store
```

Using the same header for all responses from this endpoint is acceptable and
is preferred initially, since both internal archive paths and document text
may be sensitive.

## Query Parameters

The request accepts exactly one required selector plus the following optional
boolean query parameters.  Parameter names are case-sensitive; boolean values
are parsed case-insensitively, so `true`, `TRUE`, `false`, and `FALSE` are
valid.

| Parameter | Required | Meaning |
| --- | --- | --- |
| `file_hash` | Exactly one selector required | Canonical value from `files.hash`. |
| `user_path` | Exactly one selector required | Complete user-facing Windows/UNC path to an indexed file. |

| Parameter | Default | Meaning |
| --- | --- | --- |
| `include_text` | `false` | When `true`, include the complete stored `file_contents.source_text` value as `source_text`.  When `false`, omit `source_text` entirely. |
| `include_user_paths` | `false` | When `true`, represent each location using a user-usable full path.  When `false`, represent it using the path columns stored in `file_locations`. |

`file_hash` and `user_path` are mutually exclusive.  Repeated selectors, no
selector, an empty value, a value other than `true` or `false` for a boolean,
and unknown query parameters return `400 Bad Request`.  A request such as
`?file_hash=abc&include_text=TRUE` is valid.

### User-path resolution

When `user_path` is supplied, the API resolves it without performing file
server I/O:

1. Verify that it is underneath the configured `USER_ARCHIVES_LOCATION`.
2. Normalize Windows path separators and harmless redundant path components.
3. Reject traversal-like `..` components.
4. Convert the result to the database-relative directory and filename values.
5. Find an indexed `file_locations` record with a case-insensitive exact match
   on those directory and filename values.
6. Resolve that location's file hash and return the normal canonical-file
   response.

The lookup follows Windows/SMB case-insensitive path semantics.  It must not
select an arbitrary result: if matching location records resolve to more than
one file hash, return `409 Conflict` and report an ambiguous indexed location.
If all matches resolve to the same hash, they are equivalent and the request
succeeds.

## Response Contract

A successful lookup returns `200 OK` with JSON.  Dates use `YYYY-MM-DD` and
timestamps use ISO-8601 strings, including an offset when one is available.
Database nulls are JSON `null`.

```json
{
  "file_hash": "canonical-file-hash",
  "size_bytes": 1048576,
  "extension": "pdf",
  "location_count": 2,
  "locations": [],
  "text_status": "content_searchable",
  "text_status_label": "Content searchable",
  "text_length": 12540,
  "text_updated_at": "2026-08-19T12:34:56+00:00",
  "detected_dates": [
    { "date": "2020-04-15", "occurrences": 2 }
  ]
}
```

This contract deliberately does **not** include `display_filename` or
`multiple_filenames`.  Those are presentation conveniences for the HTML page.
API clients can use the `filename` present on every location and decide their
own display behavior.

`file_id`, raw extraction errors, `file_contents.source_metadata`, and
`file_content_failures.source_metadata` are internal implementation details
and are not exposed.

### Location representations

Every location object includes a `filename` key, even when the value is null.
The representation depends on `include_user_paths`.

With the default `include_user_paths=false`, locations expose a complete
database-relative path:

```json
{
  "location_id": 42,
  "database_path": "Projects/P12345/Reports/Geotechnical Report.pdf",
  "filename": "Geotechnical Report.pdf",
  "existence_confirmed": "2026-08-18T12:30:00",
  "hash_confirmed": "2026-08-18T12:31:00"
}
```

With `include_user_paths=true`, locations expose a complete user-facing path
created through `FileServerUtils.user_path_from_db_data(...)`:

```json
{
  "location_id": 42,
  "user_path": "\\\\server\\Records\\Projects\\P12345\\Reports\\Geotechnical Report.pdf",
  "filename": "Geotechnical Report.pdf",
  "existence_confirmed": "2026-08-18T12:30:00",
  "hash_confirmed": "2026-08-18T12:31:00"
}
```

`database_path` is formed by joining the stored
`file_server_directories` and `filename` values in the database-relative path
format.  If there is no directory value, it is the filename; if there is no
filename, it is the directory value.  It is not rooted at
`USER_ARCHIVES_LOCATION`.

The user-path representation does not also return `database_path`, and the
database-path representation does not also return `user_path`.  This gives
the two parameter states predictable parity: each returns one complete path
for the location plus its separate `filename` value.

Locations retain the existing deterministic order: shortest stored directory
path first, then directory, filename, and location ID.

### Text and indexing metadata

`text_status` retains the stable archive-search status vocabulary, while
`text_status_label` is a human-readable convenience.  `text_length` is the
stored character count when recorded; it is not a guarantee that source text
will be returned unless `include_text=true`.

`detected_dates` is always a chronological list of every distinct date
detected for the file.  Each record contains the ISO date string and the
aggregate number of detections for that date:

```json
{
  "detected_dates": [
    { "date": "2020-04-15", "occurrences": 2 },
    { "date": "2020-05-01", "occurrences": 1 }
  ]
}
```

Detected dates are derived from extracted text and are not authoritative
document dates or filesystem timestamps.  The API deliberately does not
apply the HTML page's date-mention display limit or return its summary and
highlight fields.

### Full source-text inclusion

When `include_text=true`, add one member to the response:

```json
{
  "source_text": "the complete value stored in file_contents.source_text"
}
```

If no `file_contents` row exists, or its `source_text` is null,
`source_text` is returned as `null`.  The response is otherwise the same as
the metadata response.

There is intentionally no preview, truncation, page cursor, or client-set
length for this first version.  This API is expected to be low-frequency, and
only roughly six of approximately 750,000 files have source text above 10 MB
(including two around 60 MB and one around 26 MB).  The endpoint must retrieve
the full value only when `include_text=true`; the default metadata request must
not select or serialize `file_contents.source_text`.

The rare full-text responses are allowed to be large.  They should be treated
as a deliberate client opt-in, not as an error or automatically truncated
response.  The implementation should log normal request metadata and outcome,
but never log source text or full response bodies.  Production HTTP/proxy and
client timeouts must tolerate at least the expected maximum response size.

No size-based rejection or truncation is part of this first version.  Measure
production performance with the known 26 MB and 60 MB records before adding
such a safeguard.  If it becomes necessary, add an explicit, documented
response-size policy rather than changing the meaning of `include_text=true`
silently.

## Errors

| Status | Condition | JSON body |
| --- | --- | --- |
| `400` | Missing/invalid selector, invalid/repeated/unknown query parameter, or user path outside the configured archive root | `{ "error": "..." }` |
| `401` | Missing or invalid authentication | `{ "error": "Unauthorized." }` |
| `404` | No `files` row has the requested hash, or a valid user path has no indexed location | `{ "error": "File not found." }` |
| `409` | A user path resolves to indexed locations belonging to different file hashes | `{ "error": "Ambiguous indexed location." }` |
| `500` | Unexpected server/database failure | `{ "error": "Unable to retrieve file information." }` |

Hash validation must permit the formats already stored by the application; do
not assume a particular digest algorithm or fixed hash length unless the
database is migrated to enforce one.

## Implementation Notes

- Add the route to the `archiver` blueprint.
- Reuse the read-only retrieval logic in
  `archives_application.archiver.file_info` rather than duplicating its SQL.
- Add a path-resolution helper that translates a user path to a canonical hash
  using only indexed database data.  It must not call `os.path.exists`, read
  the SMB share, or otherwise make file server I/O part of the request.
- Extend that service, or add a serializer beside it, to select
  `source_text` only for `include_text=true` and to choose one of the two
  location representations.
- Build `database_path` from the stored directory and filename values without
  using a user archive root; build `user_path` through
  `FileServerUtils.user_path_from_db_data(...)`.
- Keep JSON serialization separate from the HTML view model.  In particular,
  remove `display_filename` and `multiple_filenames` from the API contract
  without changing the HTML page.
- Convert `date`, `datetime`, and other non-JSON-native values explicitly
  before calling `flask.jsonify`.
- Use parameterized SQL for the hash and never interpolate user values into
  SQL.

## Acceptance Checks

- An authenticated default request returns metadata and one complete
  `database_path` plus `filename` for every location, without selecting or
  returning `source_text`.
- A `file_hash` selector and an equivalent `user_path` selector return the
  same canonical-file response.
- Requests with no selector, both selectors, a path outside the user archive
  root, or traversal-like path components return `400`.
- A valid but unindexed user path returns `404`; an indexed-path ambiguity
  across different hashes returns `409`.
- `include_text=TRUE` returns the complete stored text, including a text value
  larger than 10 MB, and has `Cache-Control: private, no-store`.
- A file without extracted content returns `source_text: null` when text is
  requested.
- `include_user_paths=true` returns `user_path` plus `filename` for every
  location and does not return `database_path`.
- The default location response returns `database_path` and `filename` for
  every location and does not return `user_path`.
- `detected_dates` contains every distinct detected date in chronological
  order, using the documented `date` and `occurrences` keys.
- Invalid booleans, unknown parameters, missing authentication, and unknown
  hashes produce the documented status and JSON error format.
- The existing `/file_info/<file_hash>` HTML behavior remains unchanged.
