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

## Testing Guidelines

There is no formal test suite in the repository yet; `dev_files/sql_test.py` is an ad hoc helper. When adding tests, prefer `pytest`, place tests under `tests/`, and name files `test_<feature>.py`. For endpoints that enqueue background work, use the existing `?test=true` synchronous mode where available.

## Commit & Pull Request Guidelines

Recent commits use short, direct subjects such as `fixed issue related to illegal chars...` or `removed redundant...`. Keep commits focused and use concise imperative or past-tense summaries. Pull requests should describe the user-facing change, note database/filesystem side effects, list manual verification steps, and include screenshots for template or CSS changes.

## Agent-Specific Instructions

Do not perform large filesystem mutations directly in routes. Use `ServerEdit(...)` and enqueue reconciliation tasks through `RQTaskUtils.enqueue_new_task(...)`. Task functions must accept `queue_id`, enter `with app.app_context():`, and update `WorkerTaskModel` status. For bulk or destructive operations, check directory quantities first and preserve the existing exclusion behavior for filenames and extensions.
