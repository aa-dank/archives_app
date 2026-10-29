# Development Journal - archives_app

A running log of development sessions, implementation decisions, operational
notes, and follow-on work for future reference.

---

## Entry 001 - FileMaker reconciliation removal and project location maintenance
**Date:** 2026-05-21  
**Author:** OpenAI Codex (GPT-5)

---

### Context

FileMaker-to-PostgreSQL data parity for projects, CAANs, contracts, and join
tables is now handled by the standalone service at
`/home/projects/project_sync_service`. The Archives App should no longer scrape
FileMaker or reconcile FileMaker records into the app database.

The only project maintenance responsibility that remains in this app is keeping
`projects.file_server_location` current. Per the database and file server
reference in `/home/projects/business_services_db/reference/ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`,
project and file location paths are stored relative to the Records root.

### What changed

The old FileMaker reconciliation task and endpoints were removed. They were
replaced with project-location-only maintenance that resolves project folders on
the archives file server via `FileServerUtils.path_to_project_dir`, stores the
path relative to `ARCHIVES_LOCATION`, and clears stale location values when no
project folder can be found.

The new admin endpoints are:

- `/confirm_project_locations`
- `/test/confirm_project_locations`

Both endpoints accept an optional single `project` parameter or a comma-separated
`projects` list. With neither parameter, all projects are checked. Routine
`/admin/maintenance` now also runs the location confirmation task through
`AppCustodian.confirm_project_locations_task`.

### Files changed

| File | Change |
|---|---|
| `archives_application/project_tools/project_tools_tasks.py` | Removed FileMaker reconciliation logic and kept a focused `confirm_project_locations_task`. |
| `archives_application/project_tools/routes.py` | Removed `/fmp_reconciliation` routes and added location confirmation endpoints. |
| `archives_application/main/main_tasks.py` | Added routine maintenance wrapper for project location confirmation. |
| `archives_application/main/routes.py` | Removed FileMaker task retention and FileMaker config exposure. |
| `archives_application/templates/caan_info.html` | Updated copy so CAAN/project pages no longer claim data comes from FileMaker. |
| `README.md` | Updated feature description to describe project location maintenance instead of FileMaker scraping. |
| `pyproject.toml`, `uv.lock`, `requirements.txt` | Removed `python-fmrest`; upgraded dependencies and exported requirements from `uv.lock`. |
| `Dockerfile` | Updated to Python 3.13 and removed extra unpinned Redis install. |
| `Pipfile`, `Pipfile.lock` | Removed obsolete Pipenv files; `uv` is now the source of truth. |

### Why this matters

This app no longer owns FileMaker data parity. Keeping reconciliation logic here
would create two sources of truth and increase the risk of stale or conflicting
updates. Narrowing this app to file-server location maintenance preserves the
behavior users still need while leaving all FileMaker sync behavior to
`project_sync_service`.

### Operational notes

- `projects.file_server_location` should remain a Records-root-relative path.
- `FileServerUtils.user_path_from_db_data()` is still the display conversion
  path for user-facing Windows locations.
- `pyproject.toml` plus `uv.lock` are the dependency source of truth.
- `requirements.txt` is generated from `uv.lock` for Docker.
- `.venv` was synced with `uv sync` after the dependency upgrade.

### Verification

- `uv pip check` passed after syncing `.venv`.
- `.venv/bin/python -m compileall archives_application/project_tools archives_application/main` passed.
- `rg` found no remaining FileMaker or `fmrest` references in app/package files.
- `git diff --check` passed.

### Follow-on work

- A full Flask app import still depends on a local `deploy_app_config.json`, so
  route registration was not fully smoke-tested in this workspace.
- If Docker is still part of active deployment, build the image once after this
  change to confirm the Python 3.13 base image works with the exported
  requirements and required system packages.

---

## Entry 002 - Branch purpose map
**Date:** 2026-05-28  
**Author:** OpenAI Codex (GPT-5)

---

### Context

To make future maintenance easier, this entry records what each branch appears
to be for based on branch names and each branch tip commit subject.

### Branch summaries

#### Active local branches

| Branch | Purpose summary |
|---|---|
| `master` | Primary integration branch; current baseline includes FileMaker reconciliation removal and project location-only maintenance. |
| `feature/archiving_dashboard_plot_upgrades` | Workstream for archiving dashboard improvements plus related input validation hardening (project number regex validation in forms). |
| `feature/dir-contents-summary` | Workstream for directory contents summary capabilities and supporting version/logic updates. |
| `project-server-location-endpoint` | Adds or refines API-backed scraping/location endpoints (notably `scrape_location` and `scrape_files` behavior). |
| `default-nesting-behavior` | Fix branch for destination path resolution when no existing project path is present (nesting/path fallback behavior). |
| `batch-archive-feat` | Batch archive/inbox processing enhancements, including lifecycle and HTML flow updates around `batch_process_inbox_task`. |
| `caan-gui-upgrades` | UI and UX updates around CAAN views/search (sidebar search, docstrings, and version rollover changes). |
| `fs-coordination-added` | File-system coordination/concurrency work branch; appears to be a sync branch after merging CAAN GUI upgrades with coordination logic. |

#### Remote-only and legacy branches

| Branch | Purpose summary |
|---|---|
| `origin/ServerEdit-redis-async` | Early async ServerEdit work integrating Redis-backed task handling and DB follow-up task wiring. |
| `origin/add-rq-attempt` | Initial RQ adoption branch for background task queueing. |
| `origin/fix_app_context_issue_for_rq_worker` | Stabilization branch to correct Flask app-context usage inside RQ worker task execution. |
| `origin/add-celery` and `origin/implement-Flask-CeleryExt` | Experimental Celery integration attempts that appear to have been superseded by RQ. |
| `origin/add-google-auth` | Google sign-in/authentication implementation branch. |
| `origin/blueprint_app` | Historical Flask blueprint refactor branch. |
| `origin/application_maintenance` | Added/finished admin maintenance route and app maintenance workflows. |
| `origin/confirmation_page` | Early confirmation/inbox flow implementation branch. |
| `origin/add_inbox_items_to_app_db` | Workstream for ingesting inbox items into the application database. |
| `origin/adding-batch_move_edit` | Batch move/edit feature branch, including validation and user-facing error-text refinements. |
| `origin/avoid_new_postgres_conns` | Database efficiency branch aimed at reducing unnecessary new Postgres connections. |
| `origin/add_filemaker_data` | Historical FileMaker-related data/config integration branch (predates current FileMaker reconciliation removal). |

### Notes

- Several remote branches have corresponding local tracking branches and share
  the same purpose; summaries above avoid repeating duplicate pairs.
- Purpose descriptions are inferred from branch names plus latest commit
  subjects and should be treated as operational guidance, not strict ownership.

---

## Entry 003 - Archive search implementation
**Date:** 2026-06-15
**Author:** OpenAI Codex (GPT-5)

---

### Context

The archive app needed a v1 search workflow that can search file names, file
paths, and extracted document text while preserving the existing `/file_search`
endpoint during validation. The implementation is based on:

- `research/user_search_feature_spec.md`
- `research/user_search_feats_research.md`
- `/home/projects/business_services_db/reference/ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`
- `/home/projects/business_services_db/reference/business_services_db_schema_20260520.md`
- `/home/projects/business_services_db/reference/historical/archive_search_chunked_fts_plan.md`

The core product model is:

```text
file hash = canonical result identity
file locations = access/display metadata
content chunks = retrieval evidence
```

### What changed

A new `/archives_search` workflow was added as the intended replacement path for
the older `/file_search` workflow. The old `/file_search` endpoint remains
unchanged.

The new search supports:

- filename-only search
- filename/path search
- document text search over `file_content_fts_chunks.search_vector`
- combined filename/path plus document text search
- one primary scope at a time: all archives, location prefix, project, or CAAN
- project and CAAN scope compilation through `projects.file_server_location`
- CAAN expansion through `project_caans`
- file-hash-level HTML result grouping
- primary in-scope location selection plus additional location details
- coverage/status messaging for content-searchable, thin text, failed
  extraction, not attempted, unsupported/low-value formats, and related states
- multi-sheet Excel export with `Results`, `Locations`, and `Coverage` sheets

The result page was later widened to better accommodate the search results table,
and the search form gained a default-collapsed overview section for short usage
guidance.

### Files changed

| File | Change |
|---|---|
| `archives_application/archiver/archive_search.py` | Added the search service/helper layer for scope resolution, FTS queries, result merging, coverage summaries, snippets, location selection, and Excel dataframe construction. |
| `archives_application/archiver/forms.py` | Added `ArchiveSearchForm` with search-mode, scope, location/project/CAAN, and extension controls plus one-scope-at-a-time validation. |
| `archives_application/archiver/routes.py` | Added `/archives_search` and timestamped Excel download handling while leaving `/file_search` unchanged. |
| `archives_application/templates/archive_search.html` | Added the archive search form and default-collapsed search overview. |
| `archives_application/templates/archive_search_results.html` | Added coverage summary, file-hash-level results table, additional location details, snippets, Excel link, and wider table layout. |
| `archives_application/templates/layout.html` | Added an `Archive Search` navigation link while preserving the existing `File Search` link. |

### Implementation notes

Document-content search uses raw SQL rather than ORM query construction because
the request path needs PostgreSQL-specific FTS functions, generated
`search_vector` access, CTEs, `ts_rank_cd`, `ts_headline`, and precise
directory-boundary scope predicates. Project and CAAN scope resolution still use
the app's SQLAlchemy models where the ORM is a good fit.

Search result limiting is configurable through app config values:

- `ARCHIVE_SEARCH_HTML_LIMIT`, default `300`
- `ARCHIVE_SEARCH_EXCEL_LIMIT`, default `3000`
- `ARCHIVE_SEARCH_CHUNK_CANDIDATE_LIMIT`, default `50000`
- `ARCHIVE_SEARCH_CHUNK_CANDIDATE_MULTIPLIER`, default `20`

Unscoped content search is allowed but warns users that project, CAAN, or
location scopes are preferred because prior profiling showed high latency for
all-archive content queries.

### Why this matters

Users can now search across extracted document text without losing the archive
database's duplicate-file semantics. Results are centered on `files.hash`, while
locations remain available for access and review. Coverage messaging reduces the
risk that users interpret "no content hits" as proof that the archive lacks a
document when extraction may be incomplete, thin, failed, unsupported, or not yet
attempted.

### Operational notes

- `/archives_search` is the new workflow to validate and eventually promote.
- `/file_search` remains available as a fallback during validation.
- Search is synchronous; Redis and the worker process are not used by this
  feature's request path.
- Excel files are generated in the app process and written to the existing temp
  file location for timestamped download.
- CAAN search quality depends on `project_caans` population and linked projects'
  recorded `file_server_location` values.

### Verification

- `python3 -m compileall archives_application/archiver/archive_search.py archives_application/archiver/forms.py archives_application/archiver/routes.py` passed during implementation.
- `uv run python -m compileall ...` also passed when run with dependency-cache
  access.
- `git diff --check` passed for the edited templates after layout updates.
- A live Flask route/database search was not run in this workspace because local
  app import depends on a `deploy_app_config.json` that is not present here.

### Follow-on work

- Smoke-test `/archives_search` in a configured environment with real database
  access.
- Run representative scoped content searches and review latency/query plans.
- Verify CAAN expansion quality in the target database.
- Consider mapping `file_content_fts_chunks.search_vector` in both app and
  canonical model metadata if future work moves more search construction into
  SQLAlchemy expressions.
- Tune result ranking and displayed columns after PM/archivist review.

## Entry 004 - Archive Search Excel export sanitization (2026-07-06)

### Context

Archive search requests were failing after query execution when writing the
workbook due to `openpyxl.utils.exceptions.IllegalCharacterError` from control
characters embedded in OCR/extracted text snippets.

### Changes made

- Added workbook-bound string sanitization using
  `openpyxl.cell.cell.ILLEGAL_CHARACTERS_RE` before `to_excel()` writes.
- Applied sanitization to `Results`, `Locations`, and `Coverage` dataframe
  exports in the archive search workbook builder.
- Updated the `/archives_search` route to keep HTML results rendering even if
  workbook export fails, with a warning shown to users and traceback logging for
  operators.
- Updated result-page controls so the Excel download action is disabled when an
  export was not generated.

### Performance note

Workbook generation is currently part of the synchronous search-submit request
path. The new sanitization step is linear over string/object cells and is
expected to add only minor overhead relative to FTS query execution and Excel
file writing.

## Entry 005 - Archive search run telemetry (2026-07-16)

### Context

The production `business_services_db` now contains an `archive_search_runs`
table for narrow, run-level search telemetry. The archives app needed to record
search parameters, authenticated user identity when available, duration, result
count, coverage, application version, and the final execution status. Recording
applies only to `/archives_search`; the legacy `/file_search` workflow remains
unchanged.

The design also leaves a clean input boundary for a possible future JSON search
API without implementing that endpoint yet.

### Changes made

- Added `ArchiveSearchRunModel`, matching the live PostgreSQL table, JSONB
  columns, constraints, foreign key behavior, defaults, and indexes.
- Added the immutable `ArchiveSearchRequest` service contract. The current HTML
  route creates it from a validated WTForms form, while a future API can create
  the same request from validated values.
- Replaced the procedural `run_archive_search()` orchestration with the stateful
  `ArchiveSearchRun` lifecycle while retaining stateless query, scope, metadata,
  snippet, coverage, and workbook helpers as module-level functions.
- Updated `/archives_search` to execute an `ArchiveSearchRun` and associate the
  authenticated user ID when present.
- Did not add telemetry to `/file_search`, initial form GET requests, invalid
  form submissions, or timestamped workbook-download requests.

---

### Search run lifecycle

Each validated search submission follows this lifecycle:

1. Insert and commit an `incomplete` row before search execution.
2. Measure `ArchiveSearchRun._execute_search()` with a monotonic timer.
3. On success, update the row with `successful`, `duration_ms`,
   `returned_result_count`, and `coverage_summary`.
4. On a search exception, update the row with `failed` and `duration_ms`, then
   re-raise the original exception for the existing route-level handling.
5. If the process terminates before finalization, the committed row remains
   `incomplete`.

Telemetry writes are best-effort. Insert, update, or rollback failures are
logged but do not replace the search outcome or prevent successfully assembled
results from being returned. Search completion is finalized before workbook
generation, so an optional Excel export failure does not mark a completed
database search as failed.

### Files changed

| File | Change |
|---|---|
| `archives_application/models.py` | Added the `ArchiveSearchRunModel` ORM mapping. |
| `archives_application/archiver/archive_search.py` | Added `ArchiveSearchRequest` and `ArchiveSearchRun`; moved the existing search workflow into the run lifecycle. |
| `archives_application/archiver/routes.py` | Updated only `/archives_search` to construct and execute the search run. |

### Verification

- `.venv/bin/python -m compileall` passed for the changed Python files.
- `git diff --check` passed.
- Confirmed there are no remaining application callers of the removed
  `run_archive_search()` function.
- Automated tests were not added because this repository currently uses manual
  feature verification.

### Manual follow-on

- Confirm the deployed application database role has `INSERT`, `UPDATE`, and
  `SELECT` access to `archive_search_runs` and sequence usage for
  `archive_search_runs_id_seq`.
- Exercise successful, failed, interrupted, zero-result, authenticated, and
  anonymous searches in a configured environment and inspect their telemetry
  rows.
- Confirm `/file_search` and workbook-download requests do not create telemetry
  rows.
- If a JSON search endpoint is added, implement request validation and a JSON
  result presenter around the existing `ArchiveSearchRequest` and
  `ArchiveSearchRun` service boundary.

---

## Entry 006 - Search API, operational hardening, and project-location API updates
**Date:** 2026-08-21<br>
**Activity covered:** 2026-07-20 through 2026-08-10<br>
**Author:** OpenAI Codex (GPT-5)

---

### Context

This entry catches up the commits made after Entry 005. It is based on the
actual diffs for all 13 first-parent commits after the search-telemetry change,
not solely on their commit subjects. The work completed the planned JSON-facing
search boundary, retired the legacy search implementation, clarified task-time
units, and addressed a few user-facing path and request-handling issues.

### What changed

#### Archive search API and telemetry source

`POST /api/archives_search` was added as the programmatic counterpart to the
HTML archive search workflow. It accepts only a JSON object, authenticates with
an existing logged-in session or a `user`/`password` pair, validates a strict
allowlist of request fields, and returns canonical file-hash-level JSON results.

The endpoint uses the existing `ArchiveSearchRequest` and `ArchiveSearchRun`
service layer rather than duplicating query logic. It supports filename,
filename/path, content, and combined search modes; all, location, project, and
CAAN scopes; normalized comma-separated extensions; and a configured result
limit capped at 3,000. Responses include scope resolution, ranked results,
coverage information, warnings, and a telemetry run ID when persistence
succeeds. API telemetry is marked with `request_source="api"`; web searches
retain the default `"web"` source.

The endpoint documentation was expanded with the request schema, response
shape, status codes, examples, path-display semantics, and the warning that
unscoped content searches may require longer client/proxy timeouts.

#### Search workflow consolidation

The obsolete `FileSearchForm`, old `/file_search` implementation, and its two
templates were removed. `/file_search` remains available as a compatibility
route, but now invokes the canonical `/archives_search` workflow rather than
the earlier filename/path-only search code. This supersedes Entry 003's
temporary note that the old endpoint was unchanged.

#### Task timeout and RQ enqueue reliability

Database backup requests can supply a task timeout. The queue contract was then
standardized to seconds throughout the app: the backup default is 5,400 seconds,
project-location confirmation remains 18,000 seconds, and `ServerEdit.execute`
defaults to 900 seconds. `RQTaskUtils.enqueue_new_task` no longer converts
minutes to seconds.

The enqueue helper was also corrected to avoid mutable default dictionaries,
copy caller-provided mappings, and preserve an explicit
`enqueue_call_kwargs["timeout"]` even when its value is falsey. This prevents
queue-specific state, including timeout values, from leaking into later
requests.

#### UI and request-handling refinements

- The CAAN project table now renders missing or `numpy.nan`
  `file_server_location` values as `Not recorded in database`, avoiding a
  misleading path ending in `\\nan`.
- Archive upload and inbox success messages now display configured
  user-facing Windows/UNC archive paths instead of application filesystem
  paths.
- Redundant URL decoding was removed from server-change paths because Flask has
  already decoded request parameters.
- `FlaskAppUtils.retrieve_request_param()` now checks query parameters, headers,
  form data, then a JSON object body. Consequently,
  `/api/project_location` accepts both GET and POST, including a JSON `project`
  value for POST requests.
- Added `research/fs_coordination_branch_summary.md`, documenting the historical
  branch's Redis-backed advisory locking, path aliasing, collision avoidance,
  and remaining maturity concerns.

### Commit coverage

| Date | Commit | Summary |
|---|---|---|
| 2026-07-20 | `6cdde1d` | Made the database-backup timeout request-configurable. |
| 2026-07-20 | `42957d1` | Standardized RQ timeout units to seconds and adjusted defaults. |
| 2026-07-21 | `f4e453b` | Added `ArchiveSearchRunModel.request_source`. |
| 2026-07-21 | `0ea94ee` | Removed the legacy file-search code and retained `/file_search` as an archive-search alias. |
| 2026-07-22 | `a1aafeb` | Made missing CAAN project locations explicit in the UI. |
| 2026-07-30 | `bccb278` | Changed archive success messages to user-facing paths. |
| 2026-07-21 | `5908e6d` | Added the initial archive-search JSON API and shared API validation/serialization helpers. |
| 2026-08-03 | `c6f430f` | Documented the API contract comprehensively. |
| 2026-08-03 | `e4e1fc6` | Rolled the application version to 1.16.0. |
| 2026-08-03 | `58b5467` | Fixed mutable enqueue state and timeout precedence. |
| 2026-08-06 | `1235d4d` | Removed double URL decoding from server changes. |
| 2026-08-07 | `284cd63` | Added JSON-aware request parameters and POST support for project-location lookup. |
| 2026-08-10 | `5b6a569` | Added the filesystem-coordination branch research summary. |

### Files and interfaces affected

| Area | Change |
|---|---|
| `archives_application/archiver/archive_search.py` | Added API payload validation, API response serialization, result-limit validation, and API telemetry-source handling. |
| `archives_application/archiver/routes.py` | Added `/api/archives_search`; consolidated `/file_search` onto `/archives_search`; corrected displayed archive paths and redundant decoding. |
| `archives_application/models.py` | Added `ArchiveSearchRunModel.request_source` with a database default of `web`. |
| `archives_application/main/routes.py`, `project_tools/routes.py`, `archiver/server_edit.py`, `utils.py` | Defined seconds-based task timeout behavior and made request/enqueue handling safer. |
| `research/fs_coordination_branch_summary.md` | Recorded the purpose and current assessment of the historical coordination branch. |

### Operational notes and follow-on work

- Deployments using `archive_search_runs` need the `request_source` column and
  its `web` default before API traffic is enabled.
- Configure `ARCHIVE_SEARCH_API_QUERY_MAX_LENGTH`,
  `ARCHIVE_SEARCH_API_EXTENSIONS_MAX_LENGTH`, and
  `ARCHIVE_SEARCH_API_RESULT_LIMIT` deliberately; the endpoint enforces a hard
  maximum of 3,000 returned canonical files.
- Smoke-test authenticated session and credential-based API requests, malformed
  JSON, unauthorized requests, each scope type, and both successful and failed
  telemetry rows in a configured environment.
- Verify any external caller of `backup_database`, project-location
  confirmation, or `ServerEdit.execute` now supplies timeout values in seconds.
- Continue to treat the filesystem-coordination branch as historical research;
  its locking behavior needs dedicated testing and review before reuse.

---

## Entry 007 - File-information views, date summaries, and API
**Date:** 2026-08-21<br>
**Activity covered:** 2026-08-14 through 2026-08-20<br>
**Author:** OpenAI Codex (GPT-5)

---

### Context

This entry catches up the file-information work merged after Entry 006. It is
based on the actual diffs for the five feature commits listed below, rather
than commit subjects alone. The work established a canonical, read-only
file-information view, made it the detail destination for archive search and
directory-summary results, and added a documented authenticated API.

### What changed

#### HTML file-information view

`GET /file_info/<file_hash>` presents one canonical file with its size,
extension, hash, indexed-location count, all user-facing indexed locations,
and text/indexing status. Extracted text is shown only to authenticated users;
the HTML response is marked `Cache-Control: private, no-store` when it contains
that text. The page now labels the stored length explicitly as an extracted-text
character count and no longer exposes the text-extraction update timestamp.

The detected-date section is titled **Dates Detected in Extracted Text** and
explains that these are neither filesystem timestamps nor authoritative document
dates. Date rows are aggregated by calendar date, so hidden extraction
granularity values do not create duplicate-looking dates or distort occurrence
counts. Files with 50 or fewer distinct dates display the full chronological
table. Larger sets display a date summary: distinct-date and total-occurrence
metrics, earliest/latest dates, and five-date lists for earliest, latest, and
most frequently occurring dates. `FILE_INFO_DATE_MENTION_LIMIT` defaults to
50; the extracted-text preview default remains 10,000 characters.

#### Search and directory-summary navigation

Archive-search result filenames now link to the canonical file-information
page. The compact result-row details retain the indexed-location count and a
**See file information** link, while removing repeated location paths, badges,
hash, size, and extension data. Primary Location remains plain left-aligned
text, avoiding an implication that it opens the SMB file. Searchability details
remain in the expandable area, and the character-count label is consistent with
the file-information view.

The current-directory file table in `/dir_contents_summary` also links each
filename to file information. Its extracted-text column was renamed to make its
character unit explicit, and its generated spreadsheet output drops the
internal hash used solely for HTML link generation.

#### Authenticated file-information API

`GET /api/files` provides a read-only JSON representation of one canonical
file. It requires either an active application session or HTTP Basic
credentials and accepts exactly one selector: `file_hash` or a complete
user-facing `user_path`. User paths are normalized and resolved against indexed
database locations only; this endpoint performs no SMB/file-server I/O and
returns `409 Conflict` rather than arbitrarily choosing an ambiguous path.

The API validates an allowlisted query-parameter set and supports
`include_text=true` for the complete stored source text and
`include_user_paths=true` for user-facing location paths. Default responses use
database-relative location paths. Dates and timestamps are explicitly
ISO-serialized, all detected dates are returned without the HTML display limit,
and responses send `Cache-Control: private, no-store`. The contract, selection
semantics, response fields, error statuses, and large-source-text behavior are
documented in `research/file_info_api_spec.md`.

The same change also clarified archive-search guidance: punctuation other than
quotes and a leading minus is ignored by the supported web-style search syntax.

### Commit coverage

| Date | Commit | Verified change |
|---|---|---|
| 2026-08-14 | `fd7e50f` | Clarified searchability and character-count labels; removed extraction-update display and date precision from the HTML view. |
| 2026-08-17 | `c3e722e` | Linked archive-search filenames to file information and reduced duplicated result-detail data. |
| 2026-08-17 | `b269a71` | Linked current-directory summary filenames to file information and kept its internal hash out of downloads. |
| 2026-08-18 | `f0f9318` | Added the 50-date threshold, calendar-date aggregation, and large-set date summary. |
| 2026-08-20 | `a86c843` | Added the authenticated `/api/files` endpoint, API serializer/path resolver, contract specification, and punctuation guidance. |

### Files and interfaces affected

| Area | Change |
|---|---|
| `archives_application/archiver/file_info.py` | Added shared read-only HTML/API data retrieval, date aggregation and summaries, source-text retrieval, user-path resolution, and API serialization. |
| `archives_application/archiver/routes.py` | Added `GET /file_info/<file_hash>`, `GET /api/files`, and directory-summary filename links. |
| `archives_application/templates/file_info.html` | Added the file-information page and responsive date-detail/summary presentation. |
| `archives_application/templates/archive_search_results.html` | Routed filenames to file information and simplified expanded result details. |
| `archives_application/__init__.py` | Added default extracted-text and date-display limits. |
| `research/file_info_api_spec.md` | Added the complete API contract and acceptance checks. |

### Verification and follow-on work

- The feature work was syntax-checked with Python compilation, Jinja template
  parsing, and `git diff --check`; this journal catch-up also reviewed the
  actual feature-commit diffs. No formal automated test suite exists yet.
- Smoke-test the HTML view with files that have no content, short content,
  multiple locations, 50 dates, and more than 50 dates in a configured
  Postgres environment.
- Exercise all API selector, boolean, authentication, path-resolution, and
  error cases from `research/file_info_api_spec.md`; confirm Basic-auth traffic
  is accepted only over HTTPS in deployment.
- Measure the rare large `include_text=true` responses before introducing any
  response-size policy, and keep any future policy explicit in the API
  specification.

---

## Entry 008 - Authenticated file-information API
**Date:** 2026-08-21<br>
**Activity covered:** 2026-08-21<br>
**Author:** OpenAI Codex (GPT-5)

---

### Context

The archive search API returns canonical file hashes but did not provide a
programmatic equivalent of the user-facing file-information page.  Consumers
also need to start from either a known hash or a copied user-facing archive
path, while keeping potentially large extracted text an explicit opt-in.

### What changed

Added authenticated `GET /api/files`.  Each request supplies exactly one of
`file_hash` or `user_path`, plus optional case-insensitive boolean
`include_text` and `include_user_paths` parameters.

The API accepts an authenticated application session or HTTP Basic credentials
for an active application user.  It never accepts credentials in query
parameters.  It returns metadata, indexing status, all locations, and every
distinct extracted-text date mention.  `include_text=true` adds the complete
stored `file_contents.source_text` value; default requests do not select that
column.  Responses send `Cache-Control: private, no-store`.

`include_user_paths=false` returns one database-relative `database_path` plus
`filename` per location.  `include_user_paths=true` returns a complete
user-facing `user_path` plus `filename` instead.  This creates predictable
path-field parity for API clients.

User-path resolution is database-only: it normalizes Windows path structure,
requires the path to be under `USER_ARCHIVES_LOCATION`, rejects `..` path
components, and compares stored directory/filename values case-insensitively.
An unindexed valid path returns `404`; a location that resolves to more than
one canonical hash returns `409` rather than silently selecting one.

The existing HTML `/file_info/<file_hash>` route now shares the common
metadata/location retrieval service with the API.  Its rendered behavior,
including the authenticated capped text preview and date-summary behavior,
remains unchanged.

### Files and interfaces affected

| File | Change |
|---|---|
| `archives_application/archiver/routes.py` | Added `GET /api/files`, API authentication, strict parameter parsing, and JSON error handling. |
| `archives_application/archiver/file_info.py` | Added shared file/location retrieval, user-path hash resolution, API serialization, full-text opt-in retrieval, and complete detected-date retrieval. |
| `research/file_info_api_spec.md` | Added the endpoint contract, selector/path semantics, response formats, large-text policy, errors, and acceptance checks. |
| `tests/test_file_info_api.py` | Added focused tests for selector and boolean validation, path normalization, serialization, and response cache policy. |

### Verification

- `uv run pytest tests/test_file_info_api.py` passed: 5 tests.
- `uv run python -m compileall -q archives_application tests` passed.
- Whitespace checks passed for the new API implementation, specification, and
  tests.

### Follow-on operational work

- Smoke-test session and HTTP Basic authentication against a configured
  Postgres-backed deployment.
- Exercise hash and user-path selectors, including a path outside the archive
  root, a missing indexed path, and a deliberately ambiguous location if one
  is available in test data.
- Measure client/proxy behavior for the known 26 MB and 60 MB source-text
  records before adding any response-size restriction.

---

## Entry 009 - File-information API URL alignment
**Date:** 2026-08-21<br>
**Author:** OpenAI Codex (GPT-5)

`GET /api/files` was renamed to `GET /api/file_info` so the API follows the
same file-information naming as the user-facing `/file_info/<file_hash>` page.
The route implementation, API introduction, and API specification were
updated. No compatibility alias was retained; API clients must use the new
path. Python compilation, documentation review, and `git diff --check` were
performed. Follow-on: update any external API callers to `/api/file_info`.

---

## Entry 010 - Consolidation source-removal parameter rename
**Date:** 2026-08-24<br>
**Author:** OpenAI Codex (GPT-5)

The `/api/consolidate_dirs` removal option is now named `remove_source`.
API requests use it to control whether the empty source directory is removed;
it defaults to the existing removal behavior and accepts `true` or `false`.
The former `remove_empty_dirs` name is no longer accepted. Updated
the route contract and request handling in `archives_application/archiver/routes.py`.
Verification: Python compilation, stale-name search, and `git diff --check`.

---

## Entry 011 - Shared boolean request-parameter parsing
**Date:** 2026-08-24<br>
**Author:** OpenAI Codex (GPT-5)

Added opt-in strict boolean parsing to `FlaskAppUtils.retrieve_request_param`
via `param_is_bool=True`. It preserves the existing request-source precedence,
accepts actual JSON booleans and case-insensitive `true`/`false`, and raises a
validation error for any other value. Consolidation, file-information,
scrape-location, test-mode, and endpoint-index boolean parameters now use the
shared parsing path. Verification: focused request-context checks, Python
compilation, and `git diff --check`.

---

## Entry 012 - File-information API route documentation
**Date:** 2026-08-28<br>
**Author:** OpenAI Codex (GPT-5)

Expanded the `file_info_api()` docstring for `GET /api/file_info` into a full
route-level API contract. It now documents session/HTTP Basic authentication,
required hash or user-path selectors, optional text/path flags, complete
response semantics, cache behavior, path-resolution rules, and all HTTP error
statuses. Updated `archives_application/archiver/routes.py` only.

Verification: reviewed the route implementation against the endpoint
specification and ran `git diff --check` for the changed files. Follow-on:
keep this docstring and `research/file_info_api_spec.md` aligned whenever the
API contract changes.

---

## Entry 013 - CAAN project directory-summary links
**Date:** 2026-08-28<br>
**Author:** OpenAI Codex (GPT-5)

The Location column on each `/caan_info/<caan>` page now links recorded
project root paths to `/dir_contents_summary?path=...`. Missing project paths
remain non-links, and a missing configured user archive mount retains the
existing `UNKNOWN` fallback. The generated location URL and all other
database-backed table cells are HTML-escaped before the table is rendered.

Affected: `archives_application/project_tools/routes.py` and this journal.
Verification: Python compilation, focused URL-generation check, and `git diff
--check`. The pre-existing file-information suite has 4 passing tests and 1
stale failure that still requests the renamed `/api/files` endpoint. Follow-on:
update that unrelated test to `/api/file_info` separately.

---

## Entry 014 - Project model synchronization fields
**Date:** 2026-09-03<br>
**Author:** aa-dank

Added `notes`, `inspector_fmp_id`, `inspector_name`, `project_manager_fmp_id`, and `project_manager_name` fields to `ProjectModel` in `archives_application/models.py` aligning with the `business_services_db` Alembic migration `d2c4e6f8a1b3_add_project_sync_fields`. Rolled version to 1.17.4 in `pyproject.toml`.

Affected: `archives_application/models.py`, `pyproject.toml`, `research/development_journal.md`, `uv.lock`. Verification: Python compilation and model import checks.

---

## Entry 015 - Contract model contract_number string type
**Date:** 2026-09-05<br>
**Author:** aa-dank

Updated contract_number field on ContractModel in archives_application/models.py from Integer to String aligning with business_services_db Alembic migration f7c3a9d2e4b1_change_contract_number_to_string. Rolled version to 1.17.5 in pyproject.toml.

Affected: archives_application/models.py, pyproject.toml, research/development_journal.md, uv.lock. Verification: Python compilation and model inspection.

---

## Entry 016 - Archived-or-not temporary upload cleanup
**Date:** 2026-09-10<br>
**Author:** OpenAI Codex (GPT-5)

Moved `/archived_or_not` temporary-upload cleanup into a single guarded
`finally` block. This prevents the no-locations error path from deleting the
same temporary file twice and masking the intended exception before the web
exception handler can flash, log, and redirect. It also cleans up uploads when
their hash has no matching `FileModel`, and avoids referencing an unassigned
temporary path if setup fails.

Affected: `archives_application/archiver/routes.py`,
`research/development_journal.md`. Verification: Python compilation and
focused source-flow review. Follow-on: reconcile `FileModel` records that have
no related `FileLocationModel` rows.

---

## Entry 017 - Replacement-file location reconciliation
**Date:** 2026-09-11<br>
**Author:** OpenAI Codex (GPT-5)

`add_file_to_db_task` now handles a changed file at an already-indexed path as
one transaction. It flushes a newly created `FileModel` rather than committing
it before its first location, and when replacing a location it deletes the
displaced `FileModel` only when that model has no remaining locations. Linked
`archived_files` rows are detached first. A rollback now occurs before the task
is marked failed, preserving a usable session for task-status persistence.

Affected: `archives_application/archiver/archiver_tasks.py` and this journal.
Verification: Python compilation and focused replacement-path source review.

---

## Entry 018 - Project-information timeline and index-count specification
**Date:** 2026-08-28<br>
**Author:** OpenAI Codex (GPT-5)

Extended `research/project_info_feature_spec.md` with two initial project-page
features: a semantic single-contract milestone timeline and a single
indexed-file-location count for the recorded project root. The timeline shows
only non-null contract dates, distinguishes revised expected end from actual
milestones, remains readable for same-day/single-date cases, and remains
absent when the project has multiple linked contracts. The count applies only
to the exact root and its directory-boundary descendants, is labelled as an
index result rather than a live filesystem inventory, and does not run for a
missing root.

No application behavior changed. Verification: reviewed the existing archive
search boundary predicate and current file-location indexes. Follow-on:
measure the aggregate count with EXPLAIN ANALYZE and add the appropriate
database migration/index if production query plans require it.

---

## Entry 019 - Project timeline responsive-layout scope
**Date:** 2026-08-28<br>
**Author:** OpenAI Codex (GPT-5)

Updated the project-information specification to keep the contract milestone
timeline desktop-only for its first implementation. The timeline retains its
semantic event text and same-date stacking behavior, but no mobile or
narrow-layout adaptation is required. No application behavior changed.

---

## Entry 020 - Project contract-field access decision
**Date:** 2026-08-31<br>
**Author:** OpenAI Codex (GPT-5)

Updated `research/project_info_feature_spec.md` to make contract financial,
account, and funding fields part of the normal single-contract display. They
follow the existing CAAN-page access behavior and do not need a separate
role-based authorization policy. No application behavior changed.

---

## Entry 021 - Project-information page implementation
**Date:** 2026-08-31<br>
**Author:** OpenAI Codex (GPT-5)

Implemented the read-only `GET /project_info` page and its strict ID/number
selector contract. ID lookups render the project; unique case-insensitive
numbers redirect to the canonical ID URL; ambiguous numbers return 409 rather
than selecting an arbitrary row. The page renders project facts, recorded
archive-root state, one boundary-safe indexed-file-location aggregate, linked
CAANs, and the zero/one/multiple contract states. A single contract includes
financial fields and an HTML/CSS milestone timeline that groups same-date
events and distinguishes the revised expected end.

The CAAN detail table now links project numbers to the canonical project ID
page while retaining the directory-summary location links. Affected:
`archives_application/project_tools/project_info.py`,
`archives_application/project_tools/routes.py`,
`archives_application/templates/project_info.html`,
and `archives_application/static/main.css`. Verification: Python compilation,
Jinja parsing, and `git diff --check`. Unit-test coverage is deferred. Follow-on:
measure the indexed-file aggregate with `EXPLAIN ANALYZE` against
production-like data and add a migration-backed index only if that measured
plan requires one.

---

## Entry 022 - Collapsible long associated-CAAN lists
**Date:** 2026-08-31<br>
**Author:** OpenAI Codex (GPT-5)

The project-information page now places its associated-CAAN table in a closed
native disclosure control when more than 10 CAANs are linked to a project. The
summary states the associated-CAAN count; shorter lists remain visible as
before. This keeps the contract area near the project summary without adding
client-side behavior.

Affected: `archives_application/project_tools/project_info.py`,
`archives_application/templates/project_info.html`,
`archives_application/static/main.css`, and
`research/project_info_feature_spec.md`. Verification: Python compilation,
Jinja parsing, and `git diff --check`.

---

## Entry 023 - Contract schedule overview replaces milestone timeline
**Date:** 2026-09-01<br>
**Author:** OpenAI Codex (GPT-5)

Replaced the project page's generic contract-milestone timeline with a
contractual schedule overview. It anchors the visual to Notice-to-Proceed and
the recorded current expected end, distinguishes original duration from
approved change-order time when those values reconcile, and shows actual NOC
completion variance when both completion and expected-end dates exist. The
page surfaces inconsistent schedule source values as warnings without replacing
them with derived dates. Other administrative and outcome dates now appear in
a compact chronological table rather than an inferred schedule line.

Affected: `archives_application/project_tools/project_info.py`,
`archives_application/templates/project_info.html`,
`archives_application/static/main.css`, and
`research/project_info_feature_spec.md`. Unit-test coverage remains deferred.

---

## Entry 024 - Schedule-duration color key
**Date:** 2026-09-01<br>
**Author:** OpenAI Codex (GPT-5)

Added matching blue and amber swatches to the original-duration and approved
change-order-time labels beneath a split contract schedule bar. The text now
acts as the bar's visible key, while the combined current-duration total remains
uncolored. The swatches are omitted when the source values cannot support a
split bar.

Affected: `archives_application/project_tools/project_info.py`,
`archives_application/templates/project_info.html`,
`archives_application/static/main.css`, and
`research/project_info_feature_spec.md`. Unit-test coverage remains deferred.

---

## Entry 025 - Project-information branch aligned with master data models
**Date:** 2026-09-08<br>
**Author:** OpenAI Codex (GPT-5)

Rebased `feature/project_info` onto master commits `e7724eb` and `97a4231`.
The branch now inherits the project synchronization fields and the string
contract-number model type that correspond to the database migrations already
applied on master. Renumbered the feature branch's journal entries after the
master entries so the combined record remains sequential.

Affected: branch ancestry and `research/development_journal.md`. Verification:
reviewed the inherited model fields, package version 1.17.5, feature-only diff,
and `git diff --check`. Unit-test coverage remains deferred.

---

## Entry 026 - Multiple-contract data table
**Date:** 2026-09-08<br>
**Author:** OpenAI Codex (GPT-5)

Projects with two or more direct contract records now show a horizontally
scrollable, grouped table rather than hiding contract data. It includes one
stable-order row per record across identity/party, financial, schedule-date,
and duration fields; null values use an em dash. The detailed single-contract
schedule overview remains exclusive to projects with exactly one contract.

Affected: `archives_application/project_tools/project_info.py`,
`archives_application/templates/project_info.html`,
`archives_application/static/main.css`, and
`research/project_info_feature_spec.md`. Unit-test coverage remains deferred.

---

## Entry 027 - Collapsible multiple-contract scope descriptions
**Date:** 2026-09-11<br>
**Author:** OpenAI Codex (GPT-5)

Replaced full scope-description text in the multiple-contract table with a
closed native “View scope” disclosure. Opening one disclosure expands only
that contract row; null values continue to display as an em dash.

Affected: `archives_application/project_tools/project_info.py`,
`archives_application/templates/project_info.html`,
`archives_application/static/main.css`, and
`research/project_info_feature_spec.md`. Unit-test coverage remains deferred.

---

## Entry 028 - Project-information API, introduction, and shared authentication
**Date:** 2026-09-14<br>
**Author:** OpenAI Codex (GPT-5)

Added read-only `GET /api/project_info` for authenticated programmatic access
to one project's synchronized project, CAAN, contract, and archive-index data.
It accepts one strict ID-or-number selector, rejects ambiguous numbers, uses
session or HTTP Basic authentication, and returns decimal-safe financial data,
ISO dates, raw schedule fields, and supplemental reconciliation values. The
new route is fully documented in its API docstring and never accesses SMB or
enumerates file rows; it reuses the page's path-boundary aggregate count and
project resolver. The page and API now share archive-root preparation and
stable contract sorting.

Added `authenticate_active_api_user()` as the common active-session or HTTP
Basic policy for the project and file information APIs. `/api/file_info` and
`/api/project_info` now use one implementation while preserving their existing
responses; archive search's legacy JSON-body credentials remain unchanged
pending a separately documented compatibility migration. The helper lives as a
`FlaskAppUtils` static method in the established shared utilities module; its
local `bcrypt` import avoids adding package-initialization coupling to
`utils.py`.

Added a user-facing Project Information introduction covering navigation from
CAAN records, direct ID and project-number URLs, archive-index count
limitations, CAAN disclosure behavior, one-versus-many contract views, and
the contract schedule's source-data and color-key interpretation.

Affected: `archives_application/utils.py`,
`archives_application/archiver/routes.py`,
`archives_application/project_tools/routes.py`,
`archives_application/project_tools/project_info.py`, and
`dev_files/project_info_user_facing_intro.md`, and
`research/development_journal.md`. Verification: Python compilation, minimal
Flask route registration, user-guide review against the current template and
route behavior, and `git diff --check`. Unit-test coverage remains deferred.

---

## Entry 029 - Project-information integration and 1.18.0 release preparation
**Date:** 2026-09-14<br>
**Author:** OpenAI Codex (GPT-5)

Integrated `feature/project_info` with the current master branch, retaining
the master operational fixes and renumbering the feature's journal series to
preserve a single continuous record. Rolled the application package from
1.17.8 to 1.18.0 and regenerated the lockfile; dependency versions were not
intentionally changed.

Affected: `pyproject.toml`, `uv.lock`, and
`research/development_journal.md`, plus the project-information feature and
the master changes included by the merge. Verification: merged current
`origin/master`, resolved the journal conflict, and regenerated `uv.lock`.
Full application verification remains pending.

---

## Entry 030 - Project team and notes on project information
**Date:** 2026-09-14<br>
**Author:** OpenAI Codex (GPT-5)

Added optional Project manager and Inspector facts plus a visible Project notes
card to the project-information page. Notes preserve source line breaks and
wrap long text without interpreting it as markup. The existing API serializer
already returns `project_manager_name`, `inspector_name`, and `notes`; its
usage docstring now explicitly identifies those fields. A read-only data
profile found manager names on most projects, inspector names less often, and
rare but occasionally multi-line notes, supporting this optional-facts and
separate-notes presentation.

Affected: `archives_application/templates/project_info.html`,
`archives_application/static/main.css`,
`archives_application/project_tools/routes.py`,
`dev_files/project_info_user_facing_intro.md`, and
`research/development_journal.md`. Verification: Python compilation, Jinja
template parsing, and `git diff --check`. Unit-test coverage remains deferred.

---

## Entry 031 - Dynamic server-edit timeout scaling for large directory reconciliations
**Date:** 2026-09-14  
**Author:** OpenAI Codex (GPT-5)

---

### Context

A production failure showed that a directory rename affecting 33,298 file rows could still time out even after the filesystem rename completed. The route layer was issuing a fixed `timeout=1200` when queuing the DB reconciliation task, while the actual work in `ServerEdit.add_renaming_to_db_task` scales with `FileLocationModel` row count for every affected file under the renamed directory.

### What changed

The timeout policy is now derived from the estimated scope of the edit instead of a hard-coded value. `ServerEdit.estimate_timeout_seconds()` computes a floor and ceiling based on the real `files_effected` estimate, and `ServerEdit.execute()` uses it when enqueuing the follow-up reconciliation task unless a caller passes an explicit override.

The chosen formula is:

```text
timeout = max(300, min(300 + ceil(files_effected * 0.2), 7200))
```

This keeps single-file edits at 300 seconds while allowing large directory renames and moves to scale up to 7,200 seconds. The route no longer injects a fixed `1200` second timeout; it simply calls `server_edit.execute(...)` with the app limits unchanged.

### Files changed

| File | Change |
|---|---|
| `archives_application/archiver/server_edit.py` | Added dynamic timeout estimation and ensured the timeout is computed at enqueue time from `change_type`, `is_file`, and `files_effected`. |
| `archives_application/archiver/routes.py` | Removed the hard-coded `timeout=1200` channel override for server edits. |
| `tests/test_server_edit_timeouts.py` | Added focused regression tests covering the floor and large-directory cases. |

### Why this matters

The database reconciliation for directory renames updates every affected `FileLocationModel` row and commits inside the loop. That makes runtime proportional to directory size, so a fixed timeout is too brittle for large edits. The new estimate tracks the actual work and keeps the permission and limit checks unchanged.

### Verification

- `.venv/bin/python -m pytest tests/test_server_edit_timeouts.py -q` passed after the fix.
- The regression covers the single-file floor case and the 33,298-file production-scale directory rename case.

## Entry 032 - Propagate file size and extension index
**Date:** 2026-09-24<br>

Propagated the canonical non-unique B-tree index on `files.size` and `lower(files.extension)` to the `File` models in archives_app, archives_scraper, and desktop_archives_scraper. Bumped each dependent project patch version and regenerated its lockfile; no dependency changes were intended.

Affected: `archives_application/models.py`, `pyproject.toml`, `uv.lock`, and this journal in archives_app; `db/models.py`, `pyproject.toml`, and `uv.lock` in archives_scraper; and `desktop_archives_scraper/db/models.py`, `pyproject.toml`, and `uv.lock` in desktop_archives_scraper. Verification: `uv lock` completed in all three repositories, and each `File` model imported successfully with PostgreSQL DDL compilation showing `(size, lower(extension))`.

---

## Entry 033 - Indexed duplicate detection for inbox and archive checks
**Date:** 2026-09-24<br>

Added a shared `find_indexed_file_by_path` helper for inbox and uploaded-file duplicate checks. It first filters the `files` index by byte size and case-insensitive stored extension, then calculates SHA-1 only when that inexpensive precheck finds a candidate; the hash remains the final exact-content comparison. The inbox item page now warns archivists when an exact duplicate is indexed and lists its user-facing archive locations without preventing intentional additional archiving. Both `archived_or_not` endpoints use the same precheck, and the API now removes temporary uploads even when processing fails.

The extension extraction intentionally matches the current indexing behavior, including its extensionless-file semantics; normalizing that behavior and historic values remains follow-on work.

Affected: `archives_application/archiver/routes.py`, `archives_application/templates/inbox_item.html`, and `research/development_journal.md`. Verification: Python compilation of changed modules, Jinja parsing for the inbox template, and `git diff --check`. Route integration tests remain follow-on work because this repository currently has no checked-in route test suite.

---

## Entry 034 - Collapsible inbox duplicate locations
**Date:** 2026-09-24<br>

Changed the inbox duplicate notice to keep potentially long location lists collapsed by default. Its native HTML disclosure control states the number of exact indexed locations and expands to the existing user-facing archive paths on demand.

Affected: `archives_application/templates/inbox_item.html` and `research/development_journal.md`. Verification: Jinja parsing and `git diff --check`.

---

## Entry 035 - Inbox archiving workflow visual refinement
**Date:** 2026-09-24<br>

Refined the inbox archiving page to make its workflow easier to scan: guide links are compact actions, the current file and bounded preview are presented as a distinct panel, and optional document details and archive-destination fields are separated into clearly headed sections. The archive action is now visually primary, long filenames and indexed archive paths wrap safely, and preview images are constrained to the viewport rather than expanding the page indefinitely. No archive form behavior or duplicate-detection logic changed.

Affected: `archives_application/templates/inbox_item.html`, `archives_application/static/main.css`, and `research/development_journal.md`. Verification: Jinja parsing and `git diff --check`.

---

## Entry 036 - Public project metadata search and XLSX export
**Date:** 2026-09-21
**Author:** OpenAI Codex (GPT-5)

Added public read-only `GET /project_search` and `GET /project_search/export`
workflows. The HTML page validates its bookmarkable query/filter contract,
searches approved project and direct-contract metadata with literal,
case-insensitive AND-term matching, preserves duplicate project numbers as
separate project-ID rows, ranks results deterministically, and bounds the page
to the top 300 projects. It adds a distinct Project Search navigation item and
does not change the dedicated CAAN or Archive Search workflows.

The export applies the same complete ranking and emits a write-only XLSX
workbook with flattened project-contract rows, a search-information sheet,
formula-safe database text, aggregate initial-contract-cost state, and only a
converted user-facing archive location. It never exposes raw stored archive
paths, project notes, or FileMaker IDs, and it performs no SMB access,
background work, database writes, or file-location counts.

Affected runtime components: `archives_application/project_tools/project_search.py`,
`archives_application/project_tools/routes.py`, the Project Search template and
navigation, and focused tests. Verification: focused SQLite-backed route,
ranking, filter, duplicate-ID-link, export-expansion, and formula-neutralization
tests; Python compilation; Jinja parsing; PostgreSQL query-shape compilation;
and `git diff --check`. Follow-on: measure the specified production PostgreSQL
query plans and revisit indexes only if observed latency justifies a controlled
database-repository migration.

---

## Entry 037 - Compact Project Search results table and filters
**Date:** 2026-09-21
**Author:** OpenAI Codex (GPT-5)

Removed the Archive root status column and missing-root explanation from the
public Project Search HTML results table to make its ranked navigation view
more compact. The file-server-location filter and export-only user-facing
location remain available.

The Drawings filter now replaces its standalone Unknown option with Yes or
Unknown, which includes projects that generated architectural diagrams and
projects without a recorded drawings value. Yes and No remain exact filters.
The visible file-server-location filter uses Any, Known, and Unknown states;
its underlying request values continue to describe stored-location state without
implying a live filesystem check. Campus/client remains an export context field
but is no longer searched.

Affected runtime components: Project Search presentation, filter construction,
feature specification, and focused tests. Verification: focused Project Search
tests and template parsing. No operational follow-on work is required.

The Match in source label is export-only; it was removed from the HTML results
table because its separate-data-source distinctions are not needed for the
primary navigation workflow.

---

## Entry 038 - Exact linked-CAAN lookup in Project Search
**Date:** 2026-09-21
**Author:** OpenAI Codex (GPT-5)

Project Search accepts a case-insensitive exact CAAN-value match for each
individual query term against a project's direct CAAN relationships. It does
not perform partial CAAN-value, CAAN-name, or CAAN-description matching, and it
still returns only project rows. CAAN terms can be combined with project and
contract terms; a single exact linked-CAAN query ranks immediately after an
exact project number and CAAN contributions appear in the export match-source
label.

Affected runtime components: Project Search query construction, result
presentation, feature specification, and focused tests. Verification: focused
exact-match and partial-rejection coverage. No schema change is required; the
existing CAAN relationship index supports this lookup.

---

## Entry 039 - Identifier-style funding-number matching
**Date:** 2026-09-22
**Author:** OpenAI Codex (GPT-5)

Changed Project Search funding-number matching from a broad substring check to
a case-insensitive exact token check. Funding values with multiple codes split
across spaces or line breaks remain searchable by each complete code; partial
codes no longer match. This aligns funding identifiers with exact CAAN-value
matching while retaining broad text matching for contract descriptive fields.

Affected runtime components: Project Search query construction, feature
specification, and focused tests. Verification: exact funding-code and
partial-code-rejection coverage. No schema change or operational follow-on is
required.

---

## Entry 040 - Project Search workbook navigation columns
**Date:** 2026-09-22
**Author:** OpenAI Codex (GPT-5)

Reordered the public Project Search workbook so Project Information URL is the
fourth column and uses an absolute URL. Ranking band and the renamed `database
index` project identifier now follow all contract columns. The Search
information sheet includes the canonical full Project Search results URL for
the exported criteria.

Affected runtime components: project-search workbook preparation, feature
specification, and focused export tests. Verification: workbook-column order,
absolute-URL, and search-results-URL coverage. No operational follow-on is
required.

---

## Entry 041 - Professional Project Search workbook formatting
**Date:** 2026-09-25
**Author:** OpenAI Codex (GPT-5)

Improved the public Project Search XLSX presentation without changing its data
or row-expansion contract. The project/contract sheet now has section-colored,
wrapped headers; frozen and filterable headers; tuned widths; banded rows;
currency and date formats; and clickable Project Information URLs. The Search
information sheet now has readable key/value styling, widths, and a clickable
results URL.

Affected runtime components: write-only workbook formatting, feature
specification, and focused workbook tests. Verification: tested header style,
freeze panes, filter range, widths, currency format, and hyperlink targets.
No operational follow-on is required.

---

## Entry 042 - Missing source paths in server-change API
**Date:** 2026-09-28
**Author:** OpenAI Codex

When an authenticated API server-change request names a source path that does
not exist, the endpoint now returns HTTP 404 with a concise JSON error and the
entered path. The web form keeps its existing flashed error behavior, and other
server-change failures retain their existing handling. This replaces the 500
response with a stack trace for this validation case.

Affected runtime components: `ServerEdit` source-path validation and the
`/api/server_change` response. Verification: focused authenticated API test for
the status, JSON body, and absence of a stack trace. API clients should read
the 404 response body and treat the missing source as a request failure rather
than retrying it as a server error.

---

## Entry 043 - Clearer labels in Project Search workbook
**Date:** 2026-09-28
**Author:** OpenAI Codex

The public Project Search workbook now labels each project's file server
location status as `Known` or `Unknown`, matching the HTML filter and the
workbook's Search information sheet. A known stored location remains `Known`
even if its user-facing path cannot be converted and is shown as `Unavailable`.
This status describes the recorded database value, not a live filesystem check.

Column L was renamed from `Initial contract value` to
`Total Original Contract(s) Cost`. Its value remains the sum of
recorded original costs across a project's linked contracts, repeated on each
contract row with existing missing-cost and partial-total indicators. The
individual contract's original cost is now in column T. The export-only match
source was moved after `database index` and renamed from `Matched in` to
`Search match location`; its header shares the purple trailing-column style.

A `Column guide` sheet now explains the three trailing search columns, the
project and contract cost/count fields, and the file-server-location fields.
Search-specific criteria and totals remain on the `Search information` sheet.

Affected runtime component: Project Search XLSX export. Verification: focused
workbook assertions for the updated headings, column order and style, both
file-server-location states, and the guide sheet; all Project Search tests
passed. No data migration or operational follow-on work is required.

---

## Entry 044 - Shareable CAAN and Archive Search URLs
**Date:** 2026-09-29
**Author:** OpenAI Codex

CAAN Search and Archive Search now submit read-only searches with GET query
parameters. Opening or refreshing a search URL runs the search against current
application data; no result snapshot is stored in the URL. Valid legacy POST
form submissions redirect to the equivalent GET URL. Exact CAAN navigation
continues to redirect to the CAAN information page. Invalid or repeated GET
parameters are rejected, and Archive Search retains its scope and extension
validation. Location-scope URLs may contain user-entered file-server paths.

Archive Search still records each executed search in its existing telemetry
table and creates a workbook for that run. Workbook links remain temporary
download links, separate from repeatable search URLs. New workbook names use
microsecond timestamps to reduce collisions when a URL is opened repeatedly in
quick succession; existing second-resolution workbook links remain readable.

Affected runtime components: CAAN and Archive Search routes and HTML forms, Archive
Search workbook links, and search templates. Verification: focused route tests
for fresh results, scoped URL round-trips, invalid parameters, legacy POST
redirects, and workbook download, plus the existing Project Search tests.
No migration or operational follow-on work is required.

## Entry 045 - CAAN UUID and coordinate schema alignment
**Date:** 2026-10-03

Updated `CAANModel` to match the canonical database schema.
`fmp_id_primary` now uses PostgreSQL UUID values, with nullable latitude
and longitude fields and named coordinate-pair and range constraints.
The pre-existing patch rollover to 1.19.2 was retained without another
increment.

Affected files: `archives_application/models.py` and
`research/development_journal.md`; `pyproject.toml` and `uv.lock` retain
the existing version rollover. Verification: model import and metadata
assertions for the UUID mapping, nullable coordinate fields, and all
three named check constraints. No operational follow-on work is
required.

---

## Entry 046 - Remove Archive Search form redirect
**Date:** 2026-10-05
**Author:** OpenAI Codex

Archive Search GET requests now execute immediately after validation, including
URLs submitted by the HTML form with empty or default-valued fields. This
removes the extra canonical-URL redirect and its network round trip. The
resulting URL remains shareable and reopening it runs a fresh search. Legacy
POST submissions still redirect to GET; temporary workbook downloads and
search-run telemetry are unchanged.

Affected runtime components: Archive Search HTML route and its URL test.
Verification: focused tests cover direct form-style GET execution, repeated
searches, workbook download, and legacy POST behavior. No migration or
operational follow-on is required.

---

## Entry 047 - Remove legacy project-location API
**Date:** 2026-10-05
**Author:** OpenAI Codex

Removed the obsolete `project_location` handler and its GET/POST
`/api/project_location` route because `GET /api/project_info` provides project
and archive-location information. Requests to the removed URL now return 404.

Affected runtime component: Project Tools API routes. Verification: Python
compilation and minimal Flask blueprint registration confirmed the handler and
route are absent, GET and POST requests return 404, and `project_info_api`
remains registered. Existing clients of the removed endpoint must migrate to
`/api/project_info`; request `include_user_path=true` when a display path is
needed. No database migration or filesystem work is required.

---

## Entry 048 - CAAN information page improvements
**Date:** 2026-10-07
**Author:** OpenAI Codex

The CAAN information page now shows a Google Maps Embed iframe centered on a
CAAN's recorded latitude and longitude when both coordinates are valid and a
`GOOGLE_MAPS_EMBED_API_KEY` is configured. The map opens in satellite view at
zoom 14 and is labeled as an approximate CAAN center. Pages without a usable
coordinate pair or configured key omit the map. The key is read from the existing
JSON app configuration; the operator should restrict it to the Maps Embed API
and allowed website referrers.

The Location column now displays each recorded project root as selectable plain
text, with a View summary link below it in the same cell. The link retains the
directory-summary URL, and the path remains converted to the user-facing
archive location. Missing paths and missing archive-mount configuration retain
their text fallbacks. The Drawings? table heading now has a keyboard-focusable
tooltip explaining Yes, UNKNOWN, and No in place of the separate legend. A
Back to CAAN Search link appears above the CAAN heading.

The application version advances to 1.19.4 for this CAAN page release.

Affected runtime components: CAAN information route, template, CSS, and version
display. No database migration or filesystem behavior change is required.
Verification:
map URL checks covered zero, missing, nonfinite, and out-of-range coordinates;
template rendering covered map and no-map states and the search return link;
focused HTML and URL checks confirmed path text and link escaping; a
SQLite-backed page request confirmed the Drawings? tooltip and values; and
`git diff --check` passed. Follow-on: verify an embedded map in a deployed
browser using the configured key and allowed website referrer.

---

## Entry 049 - Preserve spaces across archive path displays
**Date:** 2026-10-08
**Author:** OpenAI Codex

The file-information Locations table now preserves repeated spaces in displayed
paths with `white-space: pre-wrap`, while retaining wrapping for long paths.
Removed template indentation inside path cells so it does not become visible
whitespace. This fixes directory names such as `13xx   Original Ranch Buildings`
appearing with only one space.

Audited template path displays and API path serialization after the file-info
spacing fix. Inbox duplicate paths, archived-file location results, batch-move
source paths and item labels, directory-summary child/file names, and flashed
messages now preserve repeated spaces and wrap long values. File-info,
archive-search, inbox, and archived-location filenames also preserve spaces.
Existing project, CAAN, archive-search path displays and the directory-summary
current path already preserved spaces.

Affected runtime components: shared CSS and the corresponding HTML templates.
API path construction and JSON serialization retain repeated internal spaces;
no API, database, or filesystem changes were needed.
Verification: all Jinja templates parsed; a rendered file-info location-cell
check confirmed three spaces, HTML escaping, and no added indentation;
actual user-path conversion and
file-info location serialization retained triple spaces through a Flask JSON
round trip; rendered location-table escaping and existing table-helper space
preservation checks passed; `git diff --check` passed.
Follow-on: visually confirm these displays and long-value wrapping in a deployed
browser.

---

## Entry 050 - Full user-facing paths in archived-file location results
**Date:** 2026-10-08
**Author:** OpenAI Codex

The Archived or Not HTML results table (`locations_tables.html`) now builds full
Windows paths from each indexed directory and filename using
`FileServerUtils.user_path_from_db_data` and `USER_ARCHIVES_LOCATION`.
The shared location-dataframe helper accepts an optional user mount; its default
retains relative paths for the existing API. If no user mount is configured,
the HTML table also retains relative paths.

Affected runtime components: Archived or Not HTML route and its dataframe helper.
Verification: drive and UNC mount conversion, archive-root files, repeated
spaces, HTML escaping, unchanged relative API output, and input-dataframe
immutability checks passed; the results template loaded successfully and
`git diff --check` passed. No database or filesystem changes are required.
Follow-on: confirm the configured user mount opens the displayed paths from a
Windows client.

---

## Entry 051 - Ariolimax favicon
**Date:** 2026-10-12
**Author:** OpenAI Codex

Added a transparent SVG favicon based on the supplied Ariolimax archivist
reference: a smiling, curled banana slug holding a folder with a building
emblem. Gold shading uses the app's amber, with teal outlines, a steel blue
folder, and light neutral paper and highlights. The asset consists of native
vector paths and gradients. The shared page layout advertises the scalable
icon, giving pages a consistent browser-tab identity.

Affected runtime components: shared HTML layout and static favicon asset.
Verification: parsed the SVG and its local references, rendered the shared
template, and confirmed an isolated Flask static request returns the exact
asset with the SVG content type. Rendered with librsvg and visually checked
previews at 16, 24, 32, 64, and 96 pixels on light and dark backgrounds, plus
the full-size artwork; confirmed transparent corners and no embedded raster
content. `git diff --check` passed.
No migration or operational follow-on work is required; browsers may need a
refresh to replace a cached favicon after deployment.

---

## Entry 052 - Opt-in Archive Search caching and workbook retention
**Date:** 2026-10-13
**Author:** OpenAI Codex

Implemented the Archive Search cache specification on `feature/search-cache`.
When explicitly enabled, matching `/archives_search` and `/file_search` requests
share successful bounded results for five days from search completion. This
changes the always-fresh browser URL contract in entries 044 and 046. The API
remains fresh. Each validated request retains its own current-viewer telemetry,
including hits. Result pages show source generation time in UTC, cached-result
expiration, a freshness explanation, and a Run fresh search link preserving
active options. `refresh=1` bypasses lookup; failed refresh preserves the prior
entry for normal requests. Result HTML uses `Cache-Control: private, no-store`.

Added an independent byte-cache adapter and application extension using ordinary
Redis GET/SET-with-expiration/DEL, namespaced keys, bounded connection/read
timeouts, and no retries. Search-specific identity and strict versioned JSON
include effective per-generation policy and data-target settings without
credentials or viewer state. Malformed, expired, incompatible, oversized, or
unavailable cache data falls back to fresh searches. Writes use only remaining
lifetime. Structured cache logs record outcomes and resource information without
raw queries, paths, credentials, or payloads. No database schema/migration or RQ
connection/eviction changes are required.

Workbooks are separate generation references, reused while present and
regenerated from cached data after file/reference loss. Export warnings are
response-local. Files publish through same-directory staging and atomic rename
with collision-safe timestamp download IDs; Coverage retains the source UTC
generation time. Temporary-file maintenance now retains valid legacy/current
Archive Search workbooks for six days from completed publication/last write;
ordinary temporary files retain their existing three-day policy. Downloads and
hits never renew file age or result expiry. Invalid workbook retention defers
workbook deletion while other cleanup continues.

Configuration: caching defaults to disabled; result TTL is 432000 seconds,
workbook retention 518400 seconds, cache timeout 0.5 seconds, and entry cap
5242880 bytes. Enabling requires explicit `CACHE_URL` and `CACHE_NAMESPACE`.
Integer lifetimes must be positive and retention must exceed the effective TTL,
including server-side overrides. Invalid settings disable caching with a
configuration error while fresh searches remain usable. All seven new settings,
types, defaults, JSON VALUE/DESCRIPTION examples, restart requirements, namespace
invalidation, and rollout sizing are documented in Configuration/Operations.
Existing deployment/test JSON and credentials were not modified.

Affected runtime components: application initialization/configuration, cache
adapter, Archive Search execution/page service, browser route/template, workbook
publication, and queued temporary-file cleanup. Verification: 105 regression
tests passed; the opt-in backend test skips in the default suite and passed
separately against a disposable local Redis service for TTLs and atomic
publication. Coverage includes query-stage/Excel skipping on warm hits, aliases,
API isolation, refresh, malformed/outage/oversize fallback, JSON/datetime/space
preservation, current-user telemetry, expiration, workbook loss and concurrent
publication, and separate maintenance policies. Python compilation, all 35
Jinja template parses, and `git diff --check` passed. Existing pandas dtype
deprecation warnings remain.

Follow-on: verify the contract against the selected deployment endpoint and
TLS/auth/network settings, shared workbook-volume access, and resource/eviction
capacity before explicitly enabling. Operations records the traffic/cache/disk
planning allowances and warm-hit rollout checks. Capture fresh/cached notices
and the fresh-search control in a deployed browser for the implementation PR,
then refine limits from actual cache logs and disk use. Namespace changes are
the initial whole-cache invalidation mechanism; do not flush a shared RQ service.

---

## Entry 053 - Harden Archive Search cache failure boundaries
**Date:** 2026-10-13
**Author:** OpenAI Codex

Review of the new search cache found Redis pool-construction failures that could
escape startup fallback, and a freshness check that ran before full payload
decoding. Invalid Redis client/pool options now disable caching through the
existing configuration error path. Lookup rechecks source expiration after
decoding so a large entry that expires during reconstruction runs fresh instead
of being served. Expiration arithmetic and lazy backend numeric overflows remain
cache-local failures; they cannot abort an otherwise successful fresh search.

Affected runtime components: Redis adapter construction/operations and Archive
Search result lookup/publication. No new settings, schema, queue, workbook
retention, or rollout-policy changes. Cache classes/helpers now document failure,
lifetime, mutation, and publication contracts, with comments explaining the
non-obvious boundaries.

Verification: 112 regression tests passed, including new cases for Redis
pool-option failures, expiration during decoding, unrepresentable generation
expiration, and backend timeout/configuration overflows. The opt-in live-backend
test skips in the default suite and passed separately against a disposable local
Redis service through the production TCP adapter. Docstring inspection covered every class and
function in the new runtime and test modules; Python compilation, all 35 Jinja
template parses, and `git diff --check` passed. Existing pandas dtype deprecation
warnings remain. Follow-on: use the deployment contract/storage checks recorded
in entry 052 before enabling caching.

---

## Entry 054 - Enable cache in WSL and deployment configuration
**Date:** 2026-10-13
**Author:** OpenAI Codex

At the user's request, added all seven described cache settings to the local
`test_config_wsl.json` and `deploy_app_config.json` and set
`ARCHIVE_SEARCH_CACHE_ENABLED` to the JSON boolean `true`. Both explicitly use
`redis://localhost:6379/0`, matching their existing Redis host/port, through the
independent cache client. Namespaces are `archives:wsl:dataset1` and
`archives:deploy:dataset1` to prevent cross-environment key collisions.
Result TTL remains 432000 seconds, workbook retention 518400 seconds, cache
timeout 0.5 seconds, and encoded-entry cap 5242880 bytes. Existing configuration
values, queue settings, and Redis eviction settings were preserved.

Affected runtime components: browser Archive Search caching after configuration
reload, and workbook maintenance's explicit retention setting. Configuration and
Operations document every override, shared Redis memory, separate pools, active
file selection, and reload requirements. The JSON files are ignored by Git;
their local edits require separate copying to remote environments. The app
continues to select `deploy_app_config.json` by default; the WSL file applies
when explicitly selected.

Verification: both JSON files parsed and retained every prior setting; their
actual configuration loader/app initialization produced enabled cache policies,
valid lifetime pairings, distinct namespaces, and pools separate from RQ.
All 101 focused cache/shareable-URL tests and `git diff --check` passed. No
services were restarted or live endpoint settings changed. Follow-on: copy/reload
the intended configuration, run the selected-endpoint contract/storage checks
from Operations, verify shared-service capacity, and monitor hit rates and disk
use after activation.

---

## Entry 055 - One Redis URL for cache and queue processes
**Date:** 2026-10-13
**Author:** OpenAI Codex

At the user's direction, consolidated connection configuration to one
`REDIS_URL` for browser result caching, the web app's RQ producer, and the worker.
Both local JSON files retain `redis://localhost:6379/0`; removed their redundant
`REDIS_LOCATION`, `REDIS_PORT`, and `CACHE_URL` entries and added a described
shared `REDIS_URL`. Cache enablement, distinct environment/dataset namespaces,
lifetimes, entry-size cap, and cache timeouts remain as recorded in entry 054.
The cache keeps its independent client/pool and never uses `app.q.connection`.

All process roles now use the same resolution order: process-environment
`REDIS_URL`, explicit JSON/class URL, then legacy host/port fallback. Explicit
URLs preserve their authentication/TLS options and cannot be overwritten by old
fields; empty or invalidly typed explicit values do not silently fall back.
`CACHE_URL` is retired and ignored. The worker reads the same selected JSON file
as the app instead of independently defaulting to localhost. Worker startup now
imports the application's configuration and requires its JSON/OAuth files, as
the web process does. A malformed shared endpoint can also invalidate RQ setup;
cache-policy failures still retain their existing best-effort behavior.

Affected runtime components: configuration loading, app/cache initialization, and
worker connection setup. Configuration, Operations, and the feature specification
document the user's shared-instance decision and migration. No Redis service,
memory-limit, eviction-policy, database, or workbook-storage changes were made.
Both JSON files remain Git-ignored and require separate deployment copying.

Verification: all 130 regression tests passed, with the opt-in live Redis test
skipped in the default suite. New coverage verifies URL/environment precedence,
legacy compatibility, invalid explicit values, retired-cache-URL handling, actual
worker entry-point resolution, and shared endpoints with independent cache/queue
pools. Both real JSON files also passed actual loader/app/worker initialization
checks without starting workers or opening service connections. Compilation,
all 35 Jinja template parses, and `git diff --check` passed. Existing pandas dtype
deprecation warnings remain. Follow-on: copy the migrated JSON files and restart
web and worker processes together, using matching environment overrides; retain
the existing shared-service capacity and selected-endpoint rollout checks.

---

## Entry 056 - Archive Search result actions and freshness details
**Date:** 2026-10-13 to 2026-10-14
**Author:** OpenAI Codex

Simplified the busy Archive Search toolbar by grouping a Cached results or Fresh
results badge, Run fresh search, and Download Excel in the Results panel header,
with the workbook explanation beneath it. The badge stays paired with refresh;
actions align to the right on wide screens and wrap below the heading on narrow
screens. The top toolbar contains only Back to search and How to interpret
results. Unavailable exports keep a disabled control, and empty results retain
their actions.

Removed the prominent timestamp paragraph and renamed Coverage to Coverage &
freshness. Its collapsed details contain source generation time in UTC, cache
expiration when available, and the note that cached results can omit recent
archive/index changes, alongside existing scope and coverage information. Fresh
generations stored in cache can show expiration; cache-disabled/unpublished
generations omit that row.

Affected runtime component: Archive Search results template and local CSS.
No search, cache, API, configuration, or workbook behavior changes. The caching
release advances the app version to `1.19.6`; `uv lock` updates only the root
project version and preserves every dependency version. Updated the
feature specification and Operations. Verification: all 101 focused
cache/shareable-URL tests passed; rendered-page checks covered cached, fresh,
and caching-disabled states, badge/action placement, available/unavailable
exports, empty results, collapsed metadata, and conditional expiration. All 35
Jinja templates parsed and `git diff --check` passed. Release validation after
integrating the favicon from master passed all 131 tests against an isolated,
disposable Redis instance, including the live atomic-publication/TTL contract.
`uv lock --check --offline` passed. Existing pandas dtype deprecation warnings
remain. Browser installation was declined, so PR screenshots could not be
captured. Follow-on: visually check wide/narrow layouts and the expanded
Coverage & freshness pane during rollout.

---

## Entry 057 - Remove committed credentials and clean repository history
**Date:** 2026-10-14
**Author:** OpenAI Codex

Investigation found populated username/password assignments in the tracked
`credentials.env`, introduced on 2023-04-03. At the user's direction, removed
the file from tracking while preserving the local copy and added an ignore
rule. The application does not automatically load this file; external
deployment or mount consumers must provision it separately. Affected operational
components are repository secret handling and deployment configuration delivery;
application code, database state, and running services are unchanged.

At the user's authorization, ran git-filter-repo 2.47.0's sensitive-data-removal
mode using a temporary Git 2.51.0 build, with a fresh remote mirror and restricted
recovery copies outside the repository. Preserved all commits, including empty
commits and merge structure. The verified candidate rewrote 478 of 577 commits
(including the tracking-fix/journal commit), changing 29 of 34 branch tips.
Published those 29 updates in one atomic push with explicit leases against the
original remote tips; all 34 remote branch tips matched the verified result.
No branch protection or open PR required changes, no forks were reported, and
LFS was not in use.

Verification: every candidate commit tree matched its original with only the
root credential file removed; all 2,223 reachable file versions were free of
the exact exposed password. File history was empty and Git object-integrity
checks passed. Also sanitized the working clone's 589 mapped commits (490
rewritten) and six direct-tree Codex checkpoint refs. Both existing stashes
were restored in their original order after an extra pruning pass cleared
their log; their remaining contents and all mapped commit trees were verified.
The local credential file and ignored development files were preserved. A
local exclude rule protects the credential filename when switching to older
branches, whose historical ignore files do not contain the new master rule.
The cleanup plan and audit reports remain in ignored `dev_files` storage.

Operational follow-on: rotate/revoke any still-valid exposed password and
update its consumers separately. Preserve local configuration before replacing
other clones and avoid merging pre-cleanup history. GitHub's six read-only
`refs/pull/1/head` through `refs/pull/6/head` still retain the old history; PR
references/cached views require separate handling under GitHub's sensitive-data
removal support policy. The remote first changed commit and residual-ref details
are retained with the local cleanup plan. Temporary recovery copies retain the
original secrets and require restricted storage until deliberately retired.

---

## Entry 058 - Align worker task model with migrated schema
**Date:** 2026-10-22
**Author:** OpenAI Codex

Following external database revision `8e2b7c4d9a10`, added nullable
`time_started`/`time_updated` model fields and switched task JSON references from
`task_results` to `status_details`. Affected components: task model, queue/status
helpers, project-location progress updates, and scrape resume reads. Timestamp
population, checkpointing, timeout handling, and file reconciliation are deferred;
existing task behavior and completed-scrape selection are preserved.

Verification: 130 tests passed, one opt-in Redis test skipped; isolated SQLite
checks against the migrated column layout verified lifecycle JSON writes and
unpopulated new timestamps. Compilation and `git diff --check` passed. Existing
pandas warnings remain. Follow-on: deploy and restart matching web/worker code
together against the migrated database. No services or database were changed.

---

## Entry 059 - Persist scrape progress and resume interrupted runs
**Date:** 2026-10-22
**Author:** OpenAI Codex

Daily scrapes could exceed their cooperative budget inside a directory and lose
their in-memory resume summary on worker termination. The existing task row now
saves the inclusive relative directory checkpoint before processing it, the current
file/start time before I/O, and progress every 30 seconds during returning hash
reads. Shared task helpers populate `time_started`/`time_updated`; terminal helpers
also retain their existing completion timestamps. Resume selects the newest
non-test scraper checkpoint by enqueue time/ID even when the task was interrupted
or failed. Explicit starts retain precedence and Windows paths use mount mapping;
missing saved directories replay their nearest existing ancestor.

Monotonic deadlines are checked during traversal, between files, and between hash
chunks; partial hashes are discarded and RQ timeout exceptions propagate after a
failed-task save. File/hash and path reconciliation commit together. Existing paths
are associated with the observed content even when its hash is new or already
known elsewhere, making sequential replay safe without creating new locations.
Existing duplicate locations and obsolete content/history are left in place.
Traversal makes one circular pass, includes archive-root files, preserves existing
exclusions, and fails on directory enumeration errors. Completion records whether
the budget expired or the pass finished; a finished pass resets the checkpoint to
the root. Positive durations use `total_seconds()` plus the existing 300-second
RQ buffer, including durations longer than a day.

Affected runtime components: scrape task/traversal, scrape route/resume selector,
shared hashing and queue/status helpers. No additional schema migration or service
changes. Verification: 148 tests passed and one opt-in Redis test skipped. New
coverage includes a forcibly killed child against disposable on-disk SQLite,
durable recovery without skipped sibling files or new duplicate locations,
partial-file deadline/replay, 30-second heartbeat persistence, RQ timeout handling,
atomic rollback, changed-content reconciliation, exclusions, circular traversal,
invalid/test/unrelated checkpoint filtering, missing directories, seek deadlines,
enumeration failure, timestamp lifecycle, explicit Windows starts, and multi-day
timeouts. Compilation and diff checks passed; existing pandas/SQLAlchemy
deprecation warnings remain.

Follow-on: restart matching web/worker processes and monitor the first daily run's
saved current file, elapsed time, timestamps, and stop reason. Hard kills can still
leave `started`; checkpoints survive. Blocked SMB calls cannot observe deadlines
until they return. Directory replay deliberately retries every file, so a file or
directory taking longer than the whole budget still needs investigation; no
file-level cursor, concurrent-scrape coordination, or slow-file skipping was added.

---

## Entry 060 - Review scrape transaction and checkpoint boundaries
**Date:** 2026-10-22
**Author:** OpenAI Codex

Review found that heartbeat persistence failures inside hashing could be caught
as ordinary file errors, allowing the run to continue. Checkpoint failures now
stop processing and use the existing failed-task save, retaining the retry
directory and original exception cause. Seeking a saved directory now prunes
earlier branches until wraparound, avoiding redundant SMB subtree enumeration
while preserving circular traversal order and inclusive replay.

Extracted the per-file content/location transaction into a focused helper that
returns committed counter increments, centralized traversal setup, and flattened
the hash-read loop. Existing file reconciliation and hashing results are preserved.
Affected runtime components: scrape traversal/checkpoint handling and shared hash
helper; no schema or configuration changes.

Verification: 151 tests passed, one opt-in Redis test skipped. Added failure
injection during hashing and after file commit, and an exact circular-order test
that verifies seeking visits only checkpoint ancestors. Existing forced-kill,
replay, deadline, and rollback coverage passed. Compilation and diff checks
passed; existing deprecation warnings remain. Follow-on: deploy matching web and
worker code and investigate checkpoint-storage failures when reported. No live
services, archives, or deployed database were changed.

---

## Entry 061 - Persist file-location confirmation progress
**Date:** 2026-10-22
**Author:** OpenAI Codex

File-location confirmation previously saved its summary only at completion.
It now saves the active location ID, relative path, and check start time before
filesystem I/O, plus committed counters, last completed location/path, elapsed
time, and a stop reason. Progress refreshes before each check and after returning
checks when 30 seconds have elapsed; the existing task helpers maintain timestamps.
Continuation remains driven by `existence_confirmed`, with never-confirmed NULLs
first and ID breaking ties, rather than a separate cursor. Bounded scalar batches
avoid ORM expiration/reloads on each progress commit. Empty tables finish without
spinning through the whole budget.

Missing-location removal, last-location canonical-file cleanup, and archive-event
file-ID unlinking now commit as one transaction before progress saves. Counters
advance only after commit; individual location failures roll back and remain in
the summary. RQ timeouts and progress-save failures propagate through failed-task
handling. Monotonic budget checks also run after filesystem checks, preventing
late-returning checks from starting removals. The route rejects nonpositive
durations and uses full duration seconds plus the existing 600-second RQ buffer.

Affected runtime components: confirmation task, its per-location transaction
helper, and enqueue route. Existing removal policy is preserved; no filesystem
mutations, schema changes, or changes to other worker tasks were introduced.
Verification: 166 tests passed, one opt-in Redis test skipped. New coverage checks
durable snapshots before I/O, post-commit counter refresh, atomic cleanup/rollback
with archive history, remaining-copy preservation, timeout and save failure,
oldest/NULL ordering, empty-table termination, multi-day timeout handling, and a
forcibly killed child whose next run selects the unfinished location first.
Compilation and diff checks passed; existing deprecation warnings remain.

Follow-on: deploy matching web/worker code and inspect confirmation snapshots and
stop reasons. A blocked filesystem check still relies on RQ's outer timeout; a
hard kill can leave `started` with the last saved snapshot. Successful operations
remain committed and unfinished locations retain their older timestamps. No live
services, archive files, or deployed database were changed.

---

## Entry 062 - Report location-specific scraping phases and queued children
**Date:** 2026-10-22
**Author:** OpenAI Codex

Location-specific scraping now persists validation, confirmation, and discovery
phases, active paths/location IDs, elapsed time, committed counters, and successful
child indexing task IDs. Saves occur before filesystem checks, at phase/directory
boundaries, after 30 seconds of returning work, and before/after enqueue calls.
Enqueue counts advance only on success. Parent completion means confirmation and
discovery/enqueueing completed; child indexing retains its own task status. Local
errors produce a completed-with-errors summary; RQ timeouts, progress-save failures,
and directory enumeration errors fail the parent and propagate.

The confirmation phase reuses atomic per-location cleanup and reads bounded scalar
ID batches across commits/deletions. A set replaces linear known-path membership
checks. Paths leave that set only after a committed removal, allowing rediscovery
if a file reappears. Directory scope now matches the exact directory plus optional
separator-delimited descendants, escapes SQL wildcard characters in names, and
honors nonrecursive mode in both phases. Archive-root scans support empty/NULL
directory values. Invalid, outside-archive, and unavailable targets fail before
metadata cleanup. Existing exclusions, empty-file eligibility, and disabled-
confirmation behavior remain in place.

Affected runtime components: location-specific scrape task and its existing
confirmation helper. No schema, enqueue-route, or other worker-task changes.
Verification: 187 tests passed, one opt-in Redis test skipped. New tests cover
durable phase snapshots, heartbeat refresh, child status separation, prefix and
wildcard scope, recursion/root handling, enqueue accounting, timeout and progress
failure in both phases, enumeration/validation failure, atomic history rollback,
reappearing files, deletion across the 1,000-row batch boundary, and a forcibly
killed parent retaining its active file and queued-child IDs. Compilation and
diff checks passed; existing deprecation warnings remain.

Follow-on: deploy matching web/worker code and inspect parent phase/stop reason
alongside child outcomes. Hard kills can leave `started` with committed snapshots;
blocked I/O still relies on RQ. Automatic resume and pending-child deduplication
are deferred, so reruns may enqueue files whose earlier indexing jobs have not
yet created location records. No live services, archive files, or deployed
database were changed.

---

## Entry 063 - Persist database backup phases and report failures accurately
**Date:** 2026-10-23
**Author:** OpenAI Codex

Database backups previously saved details only at completion, swallowed errors
into a successful task record, and converted all results to strings. The backup
task now commits preparation, dump/compression, process-exit waiting, publication,
and completion phases through the shared task helpers. JSON preserves numeric
sizes/durations/exit codes, command lists, and structured errors with tracebacks.
Updates occur at phase boundaries; periodic metrics and heartbeats remain deferred.

Handled failures, including RQ timeouts and phase-save failures, attempt process
termination/reaping, stderr-thread joining, pipe closure, and partial-file removal
before saving a failed task record and propagating the exception to RQ. Cleanup
attempts retain their own errors without replacing the original task exception.
A cleanup failure also fails an otherwise successful task; an already published
backup is retained. Streaming bz2 compression, exit validation before atomic
publication, filename format, route timeout, and retention policy are preserved.
Affected runtime component: `main.main_tasks.db_backup_task`; no schema changes.

Verification: full suite passed (195 tests, one opt-in Redis test skipped), and
all 10 focused backup cases passed, including two added after full-suite
collection. Coverage verifies committed phases before operations, compression
round-trip, typed JSON, preparation/start/read/exit/publication/progress failures,
RQ timeout propagation, cleanup ordering, and original-error preservation.
Compilation and diff checks passed; existing deprecation warnings remain.

Follow-on: deploy matching application/worker code and restart workers. Inspect
backup phase and failure details alongside RQ status. Hard termination can still
leave a partial file and a started record; retries create a fresh dump. Actual
PostgreSQL backup/restore was not exercised, and no live services, archive files,
or deployed database were changed.

---

## Entry 064 - Simplify backup diagnostics and preserve primary failures
**Date:** 2026-10-23
**Author:** OpenAI Codex

Review of the backup progress changes identified unnecessary stderr-thread
lifecycle handling and diagnostics lost when stdout processing failed. The backup
task now directs stderr to an automatically removed temporary file and reads it
after process exit or termination, retaining diagnostics on handled failures
without a pipe reader thread. The worker needs a writable system temporary
directory. Cleanup errors are recorded once. If rollback or failed-status
persistence fails, the worker logs that additional error and propagates the
original task exception to RQ; the database can retain its previous snapshot.

Affected runtime component: `main.main_tasks.db_backup_task`. Phase boundaries,
backup format/publication, route timeouts, and retention are preserved.
Verification: full suite passed (199 tests, one opt-in Redis test skipped),
including 12 backup cases covering a real subprocess writing diagnostics larger
than a pipe buffer, retained diagnostics after timeout, cleanup error accounting,
and failed-status persistence failure. Compilation and diff checks passed;
existing deprecation warnings remain. No PostgreSQL backup/restore or live
service changes were performed. Follow-on: deploy/restart workers and inspect
both RQ errors and task records when persistence is unavailable.

---

## Entry 065 - Share worker progress persistence across long-running tasks
**Date:** 2026-10-23
**Author:** OpenAI Codex

Review across the daily scraper, file-location confirmation, location-specific
scraper, and backup found repeated progress timing and task lifecycle writes.
These tasks now use `utils.WorkerTaskProgress` for initiation, explicit phase
saves, 30-second throttling of returning archive work, elapsed-time refresh, and
terminal status writes. Existing JSON fields and task-specific checkpoints,
save boundaries, budgets, stop reasons, counters, child accounting, and resume
policies remain local and preserved. Backup retains its numeric timing metrics
and phase-only saves. Routes and the legacy RQ task helpers remain compatible.

Handled task failures now share best-effort rollback and failed-status saving.
If either fails, the additional error is logged and the original task exception
still propagates to RQ. This extends the backup's failure-reporting safeguard to
the archive tasks. Daily scrape checkpoint errors retain their dedicated fatal
exception so hash callbacks cannot swallow persistence failures as file errors.
Progress saves still commit the caller's session; callers must finish or roll
back domain transactions before saving. No schema changes or background heartbeat
thread were introduced.

Affected runtime components: shared task utilities and the four task functions.
Verification: full suite passed (203 tests, one opt-in Redis test skipped),
including existing forced-termination, replay, transaction, and phase coverage.
Added coverage for rolling back flushed, unfinished work before failure status
commits, rollback/save failures preserving original RQ timeouts, and failed
updates not postponing the next eligible snapshot. Compilation and diff checks
passed; existing deprecation warnings remain. Follow-on: deploy matching
application/worker code, restart workers, and inspect RQ alongside saved snapshots
when failure reporting is unavailable. No live services, archive files, or
deployed database were changed.

---

## Entry 066 - Align the development runtime with production Redis and refresh Python
**Date:** 2026-10-26
**Author:** OpenAI Codex

The production host notes record Redis 6.2.20, while local WSL ran Redis 8.10.1
and the old dependency lock selected redis-py 8.0.1. Constrained redis-py to the
Redis-6.2-supported 5.x line (`>=5.3.1,<6`) and RQ to `>=2.12.0,<3`; the refreshed
lock selects 5.3.1 and 2.12.0 respectively. The project and local app venv now
target Python 3.14, selecting source-built 3.14.10. Refreshed the remaining
dependencies, raised direct-library minimums to their tested installed versions,
and regenerated the requirements export. Docker follows Python 3.14 and
pins Redis 6.2.20. Affected components: dependency manifests, local Python/Redis
runtime, Docker development stack, and future web/worker deployments. No app
schema or archive-filesystem behavior changed.

At the user's request, replaced the local Redis apt packages with exact
6.2.20 versions and held `redis`, `redis-server`, and `redis-tools` against
unplanned upgrades. Preserved 148 records (134 streams, 10 hashes, three sets,
one sorted set), including values, stream IDs and expiration deadlines, by
creating a Redis 6 snapshot rather than loading an incompatible Redis 8 dump.
Verified the restored data and running service after replacement. Configuration,
native/logical data, and snapshot backups are root-only under
`/var/backups/archives-redis/20261026T160516`. The local default queue was empty.
The original and intermediate local app venv backups were removed at the user's
request after acceptance; the active `.venv` and its `.venv-python` interpreter
directory were retained and the interpreter/package imports rechecked.

Verification: 205 tests passed on Python 3.14.10 and Redis 6.2.20, including the
live cache publication/TTL contract and new RQ forked-worker coverage for results,
dependent jobs, failure recording and retries. Replaced a newer-client-only
retry-inspection assertion with a behavioral single-attempt check. Dependency
consistency, PDF create/read/render, and Python-3.14 binary-wheel resolution for
all 138 installed packages against x86_64/glibc 2.34 passed. Existing deprecation
warnings remain. The source tarball matched Python.org's published SHA256.

Follow-on: perform the production update separately, checking the live host's
package availability, architecture, uv version, service names, and application
workflows. The local operator handoff is in the ignored
`dev_files/environment_update_20261026.md`, outside the shared system references.
Drain work and rebuild the app venv as ec2-user on the chosen Python 3.14.10
interpreter, retaining Python 3.13 for Supervisor/other environments and rollback.
Production was not updated; no deployed database or archive files were changed.
Remove local Redis package holds only for an intentional coordinated version
change. Repair the unrelated GitHub CLI apt signing-key failure separately.

---

## Entry 067 - Replace WSL Redis with Valkey and name the shared store generically
**Date:** 2026-10-27
**Author:** OpenAI Codex

The intended production target is now Valkey `9.0.6-1.amzn2023.0.1`.
Replaced WSL's Redis 6.2.20 with upstream signed Ubuntu 22.04 packages
`valkey-server`/`valkey-tools` `9.0.6-1.jammy`, held at that version.
The `valkey-server` systemd service serves loopback port 6379 and is enabled
at boot. Its config/ACL, data, and logs use `/etc/valkey`, `/var/lib/valkey`,
and `/var/log/valkey`. Preserved all 148 records (134 streams, 10 hashes,
three sets, one sorted set), their values/stream IDs, eight expiration
deadlines, ACL access, persistence schedule, 16 databases, and memory policy
(`maxmemory 0`, `noeviction`). Redis packages were removed without purging
their prior configuration/data. Root-only rollback backups, including the
original snapshot, logical export, config/ACL, package files, and verification
report, are under `/var/backups/archives-valkey/20261027T174528Z`.

Application-owned settings/helpers, the cache adapter, diagnostics, tests,
and current documentation now use key-value store terminology. The canonical
setting is `KEY_VALUE_STORE_URL`, and the adapter is `KeyValueCache`.
Process environment overrides JSON/class settings; within either source,
the canonical name precedes legacy `REDIS_URL`. Legacy `REDIS_LOCATION` and
`REDIS_PORT` remain the final fallback. Invalid explicit settings still fail
without fallback. RQ and cache retain redis-py 5.3.1, `redis://`/`rediss://`
URLs, and independent pools with existing timeout/TTL behavior. The admin
`/test/see_config` response names the endpoint `key_value_store_url` instead
of `redis_url`; `/test/rq` retains raw server INFO fields. Valkey's actual
version is `valkey_version`; `redis_version` is a compatibility field.

The ignored `test_config_wsl.json` now sets `KEY_VALUE_STORE_URL` to
`redis://localhost:6379/0`. New environment setting `ARCHIVES_CONFIG_FILE`
selects that file for both web and worker before import; the default remains
`deploy_app_config.json`. The deployment config and production host were not
modified. Docker development now uses `valkey/valkey:9.0.6-alpine` and passes
the container service endpoint through the canonical environment setting.
Affected components: app/worker initialization, cache adapter, queue error
messages/admin diagnostics, development Compose stack, and local host service.
No database schema or archive-filesystem changes.

Verification: 224 tests passed against WSL Valkey, including live atomic cache
publication/expiry and forked RQ results, dependencies, failures, and retries.
Added precedence/legacy compatibility and selected-file startup coverage.
The two live contracts passed again after making the RQ test remove its result
streams and worker metadata. All original values and expiration deadlines were
rechecked after saving and restarting Valkey. Signed repository metadata and
package SHA256s, dependency lock/installed-package consistency, Python compilation,
and diff checks passed. The actual WSL JSON also passed app/worker/cache connection
and disposable byte/TTL/delete smoke checks. Existing deprecation warnings remain. Docker was not
built. An initial cutover settings check hit Valkey's retired `dynamic-hz`
directive and automatically restored Redis; the corrected attempt succeeded.

Follow-on: start local web and worker with
`ARCHIVES_CONFIG_FILE=test_config_wsl.json`. Perform production installation,
configuration migration, and its authentication/TLS/network/workflow checks
separately. Keep the Valkey package holds until an intentional update, and use
the pre-migration Redis snapshot if rolling back: Valkey 9 snapshots must not
be loaded into Redis 6.2. Current operations/setup details are in
`reference/operations.md`; historical journal entries remain unchanged.

---

## Entry 068 - Require key-value store configuration without Redis aliases
**Date:** 2026-10-27
**Author:** OpenAI Codex

At the user's direction, removed the compatibility fallbacks introduced in
Entry 067. App, cache, and worker now require `KEY_VALUE_STORE_URL`, with the
process environment taking precedence over JSON/class configuration.
`REDIS_URL`, `REDIS_LOCATION`, and `REDIS_PORT` cannot supply or override the
endpoint. Missing, blank, or invalidly typed required settings fail startup;
an invalid environment value cannot fall back to the file. Current configuration,
operations, development, and feature documentation reflect this contract.

Affected runtime components: shared endpoint resolver and app/worker configuration
loaders. The WSL JSON already uses the required key. The deployment JSON and
production host remain untouched; migrate that file before running this code,
or supply `KEY_VALUE_STORE_URL` consistently in every process environment.
The default selected filename remains `deploy_app_config.json`, so local startup
and tests must explicitly select `ARCHIVES_CONFIG_FILE=test_config_wsl.json`.
RQ's redis-py client and protocol URL schemes remain unchanged. No service,
database, archive-filesystem, or stored queue/cache data changes in this follow-up.

Verification: regression coverage checks rejection of Redis-only configuration
and environment settings, both JSON loaders, ignored obsolete environment
overrides across app/cache/worker, required-value validation, and selected-file
startup. Full suite: 209 passed, with the two opt-in live-service checks skipped.
Python compilation and diff checks passed; existing deprecation warnings remain.
Follow-on operational work is the separate deployment configuration migration
before adopting this code.

---

## Entry 069 - Upgrade redis-py for the Valkey 9 runtime
**Date:** 2026-10-27
**Author:** OpenAI Codex

Upgraded redis-py from 5.3.1 to 8.1.0 in the local app venv, dependency manifest,
lockfile, and generated requirements export. The supported dependency range is
now `redis>=8.1.0,<9`; RQ stays at 2.12.0. Resolution removed the now-unused
transitive PyJWT dependency and otherwise retained existing package versions.
This replaces the client restriction originally chosen for Redis 6.2; the
target is Valkey 9.0.6 on WSL and the intended Amazon Linux production host.

New shared `key_value_store.rq_connection_from_url` initializes web producers
and workers with explicit RESP2, byte responses, and zero automatic command
retries, avoiding implicit redis-py 8 default changes and replayed queue writes.
Queue read/connect timeout defaults and TCP keepalive remain as before; URL
transport/auth/TLS/timeouts still apply, and RQ extends worker read timeouts
for blocking dequeue. The client owns and closes its pool. Cache clients retain
their separate bounded RESP2 pools, no-retry behavior, and existing TTL policy.
Affected runtime components: local Python environment, dependency artifacts,
web queue initialization, and worker connection initialization. No service,
schema, archive-filesystem, or stored-data migration was performed.

Verification: all 214 tests passed against local Valkey 9.0.6 on Python 3.14.10.
Live checks cover cache atomic publication/expiry, forked RQ results/dependencies,
failure/retry handling, and a worker staying on the same blocked connection
beyond five seconds before completing a newly enqueued job. Additional coverage
verifies byte/protocol/retry policy across all roles, URL transport options,
and a failed queue write attempted only once. Actual WSL app/worker/cache
connections passed RESP2/PING/server-version smoke checks; the original key
count remains 148 after test cleanup. Dependency lock/installed consistency,
Python compilation, and diff checks passed. Existing deprecation warnings remain.
Docker and the Amazon Linux production host were not exercised.

Follow-on: deploy the reviewed lock with matching web/worker code and restart
both process roles after the separate production Valkey/configuration setup.
Run the live contracts and actual network/authentication/TLS workflow checks
there before adoption; do not re-resolve dependencies independently on that host.

---

## Entry 070 - Full project workbook export and timestamped spreadsheet downloads
**Date:** 2026-10-28
**Author:** OpenAI Codex

Added an always-visible Export all projects and linked contracts link on Project
Search. Its public `/project_search/export/all` download ignores search criteria
and reuses the existing workbook sheets, columns, formatting, formula protection,
and batched project iteration. Every project appears, with one row per linked
contract or one blank-contract row when there are none. Unlinked contracts remain
outside this project-based export. Full exports identify their scope in Search
information and use All projects as the ranking band. Filtered exports still
require active criteria; HTML results retain their 300-project display limit.

Added a shared UTC filename helper using `YYYYMMDDTHHMMSSZ`. Project-search,
full-project, directory-summary, endpoints-index, and archivist archived-files
downloads now include the export timestamp, retaining report names and existing
archivist date ranges. Archive Search keeps its already-timestamped download IDs
and retention behavior. Affected components: project-search routes, workbook
builder and template, shared utilities, and spreadsheet download routes in main,
archiver, and timekeeper. No database or archive-filesystem mutations.

Verification: focused project-search and spreadsheet/shareable-URL tests passed,
covering full export beyond 300 projects, contract expansion and natural ordering,
projects without contracts, empty-database exports, filter-independent links,
formula protection, UTC download filenames, and workbook structure. Full suite:
217 passed, three opt-in live-service tests skipped; existing deprecation warnings
remain. Changed Python files compile, the template parses, and diff checks pass.
Follow-on: check full-export latency with production-sized data and visually check the link
on wide and narrow screens; no browser screenshot was captured in this environment.
No migration or configuration change is required.

---

## Entry 071 - Put search links first in navigation
**Date:** 2026-10-28
**Author:** OpenAI Codex

Reordered File Server Tools navigation to begin with Archive Search, CAAN Search,
and Project Search. The remaining links retain their existing order. Affected
runtime component: the shared `layout.html` template. Verification: rendered the
layout and checked link order for anonymous and authenticated users; diff checks
passed. No operational follow-on is required.
