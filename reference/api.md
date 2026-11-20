# API reference

The HTTP interfaces support integrations with the existing archives system. This reference summarizes the current endpoints; handler docstrings define their detailed contracts.

The application exposes its route catalog, HTTP methods, and handler documentation at **`/endpoints_index`**. `/endpoints_index?spreadsheet=true` provides a spreadsheet of the catalog. The handlers in [archiver/routes.py](../archives_application/archiver/routes.py) and [project_tools/routes.py](../archives_application/project_tools/routes.py) document exact parameters and response shapes.

Authentication is endpoint-specific:

| Endpoint | Method | Authentication and purpose |
| --- | --- | --- |
| `/api/file_info` | GET | Active application session or HTTP Basic; file metadata, indexed locations, detected dates, optional stored text. |
| `/api/project_info` | GET | Active application session or HTTP Basic; project, CAAN, contract, and archive-location metadata. |
| `/api/archives_search` | POST | Application session or `user`/`password` in a JSON object; canonical archive-search results and coverage. |
| `/api/upload_file` | POST | `user`/`password` request parameters and a multipart upload; archival upload workflow. |
| `/api/archived_or_not` | POST | `user`/`password` request parameters and a multipart upload; duplicate/index check. |
| `/api/server_change`, `/api/consolidate_dirs` | GET, POST | Archivist/admin session or endpoint-specific credentials; filesystem edits and queued reconciliation. |
| `/api/scrape_files` | GET, POST | Admin session or endpoint-specific credentials; enqueue metadata indexing. |
| `/api/scrape_location` | GET, POST | `user`/`password` for API mode; enqueue a specific location. Unlike `/api/scrape_files`, this handler does not enforce an admin role. |

Use HTTPS for deployed credential-bearing requests. HTTP Basic uses an **application password**, not a Google account password; a Google-only account needs application-password credentials to use Basic authentication. Legacy operational endpoints use their own parameter-based authentication, so consult their route documentation instead of assuming Basic authentication applies to every API.

### File and project metadata examples

These examples use a placeholder deployment hostname. With only an email supplied to `--user`, curl prompts for the application password.

```bash
curl --user archivist@example.org --get \
  'https://archives.example.org/api/file_info' \
  --data-urlencode 'file_hash=CANONICAL_FILE_HASH' \
  --data-urlencode 'include_user_paths=true'

curl --user archivist@example.org --get \
  'https://archives.example.org/api/project_info' \
  --data-urlencode 'project_id=123' \
  --data-urlencode 'include_user_path=true'
```

File information accepts exactly one of `file_hash` or `user_path`. `include_text=true` requests the **complete** stored source text, rather than the HTML page's limited window. Project information accepts exactly one of `project_id` or `project_number`; retain the canonical project ID because project numbers can be ambiguous. Metadata APIs reject unknown or repeated query parameters and return `400` for invalid input, `401` for failed authentication, `404` for missing records, and `409` for ambiguous selectors.

File information includes `content_restricted_at` as an ISO-8601 timestamp or
null. When that timestamp is set, extracted text is suppressed: the default
response omits `source_text`, and `include_text=true` returns `source_text: null`
without fetching the text. Metadata, detected dates, and indexing status remain
available; `restriction_reason` is private. A retained hash with zero indexed
locations still returns `200` with `location_count: 0` and `locations: []`.
Its former `user_path` returns `404`. `/api/archived_or_not` also returns `404`
when a known hash has no indexed copies.

The former `/api/project_location` endpoint has been removed. Use `/api/project_info` and request `include_user_path=true` for a display path.

### Archive search request

Send `Content-Type: application/json` with a body such as this, using a logged-in session or adding `user` and `password` fields for application-password authentication:

```json
{
  "query_text": "soil report",
  "search_mode": "combined",
  "scope_type": "project",
  "scope_value": "1234",
  "extensions": "pdf,docx",
  "limit": 100
}
```

`search_mode` accepts `filename_only`, `filepath`, `content`, or `combined`; `scope_type` accepts `all`, `location`, `project`, or `caan`. Omit `scope_value` for `all`. Results include canonical file hashes, locations, ranking, coverage, and a search-run ID when telemetry persistence succeeds. A result limit being reached calls for a narrower query or scope.

Files with active text suppression still match content and filename/path
searches and keep their metadata, ranks, and ordinary coverage classification.
Their `snippet` is an empty string. Search results and coverage exclude hashes
without indexed locations. The same snippet policy applies to browser results
and Excel exports. This permits inference that a suppressed file contains a
searched term. After SQL flag changes, follow the
[cache/export purge procedure](operations.md#text-suppression-and-search-output-purge)
to discard previously generated output.

Return to the [reference index](README.md).
