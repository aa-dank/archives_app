# Development reference

Repository conventions and verification for maintaining the existing application.

The repository targets Python 3.14, with the patch version selected by
`.python-version`. Install dependencies from `uv.lock` with `uv sync --locked`.
The lock selects redis-py 8.1.0 and RQ 2.12.0 for Valkey 9.0.6; keep web and
worker dependencies aligned. Shared RQ connection policy lives in
`archives_application/key_value_store.py`; cache policy remains in `cache.py`.
Select `ARCHIVES_CONFIG_FILE=test_config_wsl.json` for local commands, including
the test suite. The deployment configuration must be migrated separately before
it can be used with the required `KEY_VALUE_STORE_URL` setting.

The existing regression suite requires the import-time configuration files described in [Configuration](configuration.md):

```bash
ARCHIVES_CONFIG_FILE=test_config_wsl.json uv run pytest
```

The tests cover project metadata search and workbook exports, shareable CAAN/archive search URLs, Archive Search caching/refresh/retention, and missing-path server-change responses. They use SQLite fixtures, temporary directories, mocked search queries, and a deterministic test-only cache with a controllable UTC clock; they do not validate PostgreSQL full-text search, live SMB operations, OAuth, or an end-to-end worker deployment. Opt-in live key-value store contract checks are documented in [Operations](operations.md#archive-search-caching). Tests still need the configuration/OAuth files because importing the app loads its default configuration first.

For changes affecting those services, verify the relevant workflow in a configured development environment as well. Check formatting before submitting:

```bash
git diff --check
```

Follow [AGENTS.md](../AGENTS.md) for repository conventions. Keep routes focused on validation and authorization; delegate filesystem operations to `ServerEdit` and enqueue reconciliation with `RQTaskUtils.enqueue_new_task`. Tasks accept `queue_id`, enter an app context, and update `WorkerTaskModel`. Convert user paths with `FlaskAppUtils.user_path_to_app_path` before I/O and build display paths with `FileServerUtils.user_path_from_db_data`. Preserve existing filename/extension exclusions and quantity checks in bulk operations.

Update [the development journal](development_journal.md) with changes to deployed behavior, security, data, filesystem operations, or operational contracts. Documentation-only changes do not require a journal entry. Pull requests should explain the user-visible change, database/filesystem effects, and verification; include screenshots for template or CSS changes.

The app version comes from `pyproject.toml`. `requirements.txt` is a generated export used by Docker; keep it aligned with the lockfile when changing dependencies:

```bash
uv export --locked --no-dev --format requirements-txt --no-hashes -o requirements.txt
```

The archiving dashboard and single-contract project schedule load Plotly.js
from Plotly's CDN only when a figure is present. Shared CDN settings live in
`archives_application/plotting.py`, and `static/plotly_charts.js` initializes
the safely serialized figures on both pages.
Its explicit JavaScript version comes from the installed Python package's
`get_plotlyjs_version()`, so upgrading the locked Python dependency updates the
CDN reference automatically. When updating Plotly, sync and verify:

```bash
uv sync --locked
uv run --locked pytest tests/test_archiving_charts.py tests/test_contract_timeline.py
```

Commit the lockfile and requirements export with dependency changes and restart
the web processes after deployment. Python and JavaScript have separate version
numbers; the regression suite checks the CDN URL against Plotly's own HTML
export. Browsers need HTTPS access to `cdn.plot.ly` to display charts. No local
Plotly.js bundle or bundle-generation step is required; the chart initializer
remains in the application's static directory.

Return to the [reference index](README.md).
