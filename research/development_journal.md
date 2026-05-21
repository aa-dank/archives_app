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
