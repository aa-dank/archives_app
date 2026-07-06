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
