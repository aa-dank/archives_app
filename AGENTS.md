# Repository Guidelines

## Project Structure & Module Organization

This is a Flask application for managing a Windows/SMB-backed archives file server. The app factory is `archives_application.create_app()`, with entry points in `run.py` and `worker.py`. Core modules live under `archives_application/`: blueprints are split into `main`, `archiver`, `project_tools`, `users`, and `timekeeper`; shared models and helpers are in `models.py` and `utils.py`. HTML templates are in `archives_application/templates/`, CSS and generated static files are in `archives_application/static/`, and design/research notes are in `research/`. Configuration is JSON-driven through `test_config*.json` and `app_config.py`.

## Build, Test, and Development Commands

- `uv sync` installs dependencies from `pyproject.toml` and `uv.lock`.
- `python run.py` starts the Flask app on `0.0.0.0:5000`.
- `python worker.py` starts the RQ worker; run it alongside the app for queued jobs.
- `docker compose up --build` runs the development stack using the provided Docker files.

Postgres and Redis must be available locally or through Docker. The app also expects environment/config files such as `test_config*.json` and Google OAuth secrets.

## Coding Style & Naming Conventions

Use idiomatic Python with 4-space indentation, descriptive snake_case functions, and PascalCase model/classes. Keep route handlers thin: validate input, authorize access, then delegate filesystem or database work to helpers/tasks. For file paths, always convert user-supplied Windows/UNC paths with `FlaskAppUtils.user_path_to_app_path(...)` before IO, and return display paths with `FileServerUtils.user_path_from_db_data(...)`.

## Commit & Pull Request Guidelines

Recent commits use short, direct subjects such as `fixed issue related to illegal chars...` or `removed redundant...`. Keep commits focused and use concise imperative or past-tense summaries. Pull requests should describe the user-facing change, note database/filesystem side effects, list manual verification steps, and include screenshots for template or CSS changes.

## Development Journal

`research/development_journal.md` is a required, version-controlled record of changes that affect deployed behavior, operations, data, security, or externally relied-on contracts; it must remain present. Before beginning a journal-worthy change, or modifying an area with recent journal history, review the most recent relevant entry.

Append a concise entry in the same change set when a change:

- changes user-visible or API behavior;
- changes database schema, migrations, synchronization, retention, or filesystem behavior;
- changes authentication, authorization, security, logging, background-job, deployment, or recovery behavior;
- records an implemented or adopted architectural decision that materially constrains future development; or
- fixes a production incident or records an operational investigation with a resulting action.

Do not add an entry for documentation- or specification-only edits, exploratory research, formatting, comments, routine no-behavior refactors, test-only changes, dependency-lock regeneration, or branch merges unless they accompany a journal-worthy change. If an operator, future maintainer, or incident responder would not need the information to understand a deployed system, it probably does not belong in the journal.

Each required entry should include the date, context, behavior or decision, affected runtime components, verification, and follow-on operational work. When catching up a neglected journal, review every commit since the last entry and summarize only the journal-worthy changes; do not infer behavior from commit subjects alone.

## Agent-Specific Instructions

Do not perform large filesystem mutations directly in routes. Use `ServerEdit(...)` and enqueue reconciliation tasks through `RQTaskUtils.enqueue_new_task(...)`. Task functions must accept `queue_id`, enter `with app.app_context():`, and update `WorkerTaskModel` status. For bulk or destructive operations, check directory quantities first and preserve the existing exclusion behavior for filenames and extensions.
