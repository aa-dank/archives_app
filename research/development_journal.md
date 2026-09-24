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
