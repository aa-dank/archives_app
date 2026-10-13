# Configuration reference

The application configuration describes the existing PPDO archive environment: database and queue connections, mounted storage, filing conventions, roles, and operational limits.

The package currently selects `deploy_app_config.json` and `google_client_secret.json` in `archives_application/__init__.py`. Both files are loaded at import time and ignored by Git. The OAuth file contains a top-level `web` object with `client_id` and `client_secret`. A `test_config*.json` filename is not selected automatically, and `credentials.env` is not automatically loaded. The web queue, application cache, and worker share one `REDIS_URL`: process environment first, explicit JSON value second, then legacy `REDIS_LOCATION`/`REDIS_PORT`. The worker reads the same selected JSON file as the web app and requires the application configuration/OAuth files at startup.

## JSON format

Each top-level setting is an object with a `VALUE`. `DESCRIPTION` provides help text for the configuration editor. Keys are case-sensitive; the spelling `Sqalchemy_Database_Location` is part of the existing loader contract.

<details>
<summary><strong>Illustrative configuration format</strong></summary>

These values illustrate the format and types. They do not represent the deployed credentials, paths, filing choices, or edit limits.

```json
{
  "SECRET_KEY": {"VALUE": "replace-with-a-long-random-secret", "DESCRIPTION": "Session signing secret"},
  "Sqalchemy_Database_Location": {"VALUE": "localhost", "DESCRIPTION": "PostgreSQL hostname"},
  "POSTGRESQL_DATABASE": {"VALUE": "archives_dev", "DESCRIPTION": "PostgreSQL database"},
  "POSTGRESQL_USERNAME": {"VALUE": "archives_dev", "DESCRIPTION": "PostgreSQL user"},
  "POSTGRESQL_PASSWORD": {"VALUE": "replace-with-database-password", "DESCRIPTION": "PostgreSQL password"},
  "POSTGRESQL_PORT": {"VALUE": "5432", "DESCRIPTION": "PostgreSQL port as a string"},
  "POSTGRESQL_SSL": {"VALUE": "False", "DESCRIPTION": "SSL requirement as a string"},
  "REDIS_URL": {"VALUE": "redis://localhost:6379/0", "DESCRIPTION": "Shared endpoint for cache, RQ producer, and worker; process REDIS_URL overrides this value"},
  "ARCHIVES_LOCATION": {"VALUE": "/mnt/archives/Records", "DESCRIPTION": "Archive root visible to the app"},
  "USER_ARCHIVES_LOCATION": {"VALUE": "N:\\PPDO\\Records", "DESCRIPTION": "The same root visible to Windows users"},
  "ARCHIVIST_INBOX_LOCATION": {"VALUE": "/mnt/archives/Inbox", "DESCRIPTION": "Root for archivist inboxes"},
  "DATABASE_BACKUP_LOCATION": {"VALUE": "/var/backups/archives", "DESCRIPTION": "Writable database-backup directory"},
  "POSTGRESQL_EXECUTABLES_LOCATION": {"VALUE": "/usr/bin/", "DESCRIPTION": "Prefix for PostgreSQL client executables"},
  "DIRECTORY_CHOICES": {"VALUE": ["A - General", "G - Construction"], "DESCRIPTION": "Archive filing choices"},
  "ROLES": {"VALUE": ["ARCHIVIST", "STAFF"], "DESCRIPTION": "Roles offered during registration"},
  "DEFAULT_DATETIME_FORMAT": {"VALUE": "%m/%d/%Y, %H:%M:%S", "DESCRIPTION": "Display date/time format"},
  "SERVER_CHANGE_FILES_LIMIT": {"VALUE": 100, "DESCRIPTION": "Non-admin file-count limit"},
  "SERVER_CHANGE_DATA_LIMIT": {"VALUE": 1073741824, "DESCRIPTION": "Non-admin data limit in bytes"},
  "SQLALCHEMY_ECHO": {"VALUE": false, "DESCRIPTION": "Enable SQL debugging output"},
  "SQLALCHEMY_LOG_FILE": {"VALUE": "sqlalchemy.log", "DESCRIPTION": "SQL log filename in the backup directory"},
  "GOOGLE_MAPS_EMBED_API_KEY": {"VALUE": "", "DESCRIPTION": "Optional key for CAAN maps"}
}
```

</details>

### Settings to understand

| Setting | Behavior |
| --- | --- |
| `SECRET_KEY` | Signs Flask sessions and supports form protection. Keep it stable across app processes. |
| `Sqalchemy_Database_Location`, `POSTGRESQL_*` | Build a `postgresql+psycopg` SQLAlchemy URI. Ports and `POSTGRESQL_SSL` are strings; `"True"` adds `sslmode=require`. Credentials must be valid in the assembled URI, including appropriate encoding of reserved characters. |
| `REDIS_URL` | Shared cache/RQ/worker endpoint. The process environment overrides the JSON/class value for every role. Explicit URLs preserve authentication/TLS options; use database zero while enabling the current cache adapter. |
| `REDIS_LOCATION`, `REDIS_PORT` | Compatibility fallback when neither environment nor explicit `REDIS_URL` is present. Ports remain strings in legacy JSON. An explicit URL takes precedence even if these old fields remain. |
| `ARCHIVES_LOCATION` | Filesystem root used for actual archive I/O by the app and worker. |
| `USER_ARCHIVES_LOCATION` | Windows drive/UNC root used in displayed paths and user input. It must correspond to the same directory as `ARCHIVES_LOCATION`. |
| `ARCHIVIST_INBOX_LOCATION` | Parent directory for individual archivists' inbox folders. |
| `DIRECTORY_CHOICES` | Filing-directory labels; use the archive's established `CODE - Name` convention. |
| `ROLES` | Roles available in registration forms. Route permissions recognize `ADMIN` and `ARCHIVIST`; other roles can be stored for general users. This list does not grant an existing user a role; assignments are stored in `users.roles`. |
| `SERVER_CHANGE_FILES_LIMIT`, `SERVER_CHANGE_DATA_LIMIT` | Integer limits for affected files and bytes in non-admin server edits. Admins bypass these limits. |
| `DATABASE_BACKUP_LOCATION`, `POSTGRESQL_EXECUTABLES_LOCATION` | Backup destination and executable prefix. Include a trailing separator in the prefix, or use an empty string to find `pg_dump` on `PATH`. |
| `APP_RESTART_COMMAND`, `APP_WORKERS_RESTART_COMMAND` | Deployment-specific shell commands used by the configuration editor to restart the app and workers. Configure these before using `/admin/config`. |
| `GOOGLE_MAPS_EMBED_API_KEY` | Optional Google Maps Embed key. CAAN maps also require valid stored coordinates. |
| `SQLALCHEMY_ECHO`, `SQLALCHEMY_LOG_FILE` | SQL debug logging and its filename under the backup directory. Leave echo disabled for normal operation. |

Database paths are relative to the archive root. For example, a stored directory `12xx Example/1234 Project` maps to `/mnt/archives/Records/12xx Example/1234 Project` for app I/O and `N:\PPDO\Records\12xx Example\1234 Project` for users with the example configuration. JSON requires escaped backslashes (`\\`). For filesystem operations on a mounted Linux archive, use the configured user-path style; the current conversion helper cannot map an arbitrary UNC alias to a local mount.

### Optional search and file-view limits

Add these using the same nested `VALUE` format when overriding defaults:

| Setting | Default | Purpose |
| --- | --- | --- |
| `ARCHIVE_SEARCH_HTML_LIMIT` | `300` | Maximum canonical files in browser results. |
| `ARCHIVE_SEARCH_EXCEL_LIMIT` | `3000` | Maximum canonical files in the archive workbook. |
| `ARCHIVE_SEARCH_API_RESULT_LIMIT` | `3000` | API result ceiling; the API caps it at 3,000. |
| `ARCHIVE_SEARCH_API_QUERY_MAX_LENGTH` | `1000` | Maximum API query characters. |
| `ARCHIVE_SEARCH_API_EXTENSIONS_MAX_LENGTH` | `500` | Maximum API extension-filter characters. |
| `ARCHIVE_SEARCH_CHUNK_CANDIDATE_LIMIT` | `50000` | Upper limit on content-search chunk candidates. |
| `ARCHIVE_SEARCH_CHUNK_CANDIDATE_MULTIPLIER` | `20` | Candidate budget multiplier relative to requested results. |
| `FILE_INFO_TEXT_WINDOW_CHARS` | `10000` | Extracted-text window on the HTML file-information page. |
| `FILE_INFO_DATE_MENTION_LIMIT` | `50` | Maximum date-mention rows on the file-information page. |

### Archive Search cache and workbook retention

Browser result caching is opt-in. Missing settings receive the defaults below
at app creation; no existing deployment JSON or credentials need to change for
fresh searches. The API remains fresh. Six cache-specific settings accompany
the existing shared `REDIS_URL`; no separate `CACHE_URL` is needed.

| Setting | Default | Validation and behavior |
| --- | --- | --- |
| `ARCHIVE_SEARCH_CACHE_ENABLED` | `false` | Real JSON boolean. Enables browser result and workbook-reference caching. Strings such as `"False"` are invalid. |
| `ARCHIVE_SEARCH_CACHE_TTL_SECONDS` | `432000` | Positive JSON integer seconds. Five days from successful search completion. Each generation receives an explicit lifetime; a server-side operation can override it. Reads never renew it. |
| `ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS` | `518400` | Positive JSON integer seconds. Separate six-day cleanup threshold, also used when result caching is disabled. Must exceed the effective result lifetime, including overrides. |
| `REDIS_URL` | Shared connection setting | Required through environment, explicit JSON/class value, or legacy host/port resolution. `redis://` or `rediss://` with optional authentication/TLS client options. Cache uses database zero (omitted or `/0`) and namespaced keys; RQ uses this same endpoint. |
| `CACHE_NAMESPACE` | Unset | Required nonempty string when enabled. Choose a distinct prefix for each environment and dataset, for example `archives:development:dataset1`. Change it after dataset restoration/replacement or to invalidate all results. |
| `CACHE_TIMEOUT_SECONDS` | `0.5` | Positive finite JSON number. Applies to connection and read timeouts, with no connection retries. URL query options cannot override these limits or byte decoding. |
| `CACHE_MAX_ENTRY_BYTES` | `5242880` | Positive JSON integer bytes. Five MiB per encoded entry; checked before writes and before decoding reads. Oversized results stay complete and run fresh. |

Use the existing configuration editor's `VALUE`/`DESCRIPTION` format. Merge
these optional entries into the selected JSON file; replace the illustrative
endpoint/prefix before explicitly enabling the feature:

```json
{
  "ARCHIVE_SEARCH_CACHE_ENABLED": {
    "VALUE": false,
    "DESCRIPTION": "Opt in to Archive Search browser result and workbook-reference caching; API searches remain fresh."
  },
  "ARCHIVE_SEARCH_CACHE_TTL_SECONDS": {
    "VALUE": 432000,
    "DESCRIPTION": "Positive integer result lifetime in seconds from search completion; reads do not renew it."
  },
  "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS": {
    "VALUE": 518400,
    "DESCRIPTION": "Positive integer Archive Search workbook cleanup age in seconds, greater than every effective result lifetime."
  },
  "REDIS_URL": {
    "VALUE": "redis://localhost:6379/0",
    "DESCRIPTION": "Shared cache/RQ/worker endpoint; process REDIS_URL overrides this JSON value. Cache has its own pool and timeouts."
  },
  "CACHE_NAMESPACE": {
    "VALUE": "archives:development:dataset1",
    "DESCRIPTION": "Distinct environment/dataset key prefix; change to invalidate application cache entries."
  },
  "CACHE_TIMEOUT_SECONDS": {
    "VALUE": 0.5,
    "DESCRIPTION": "Positive finite cache connection/read timeout in seconds; no retries."
  },
  "CACHE_MAX_ENTRY_BYTES": {
    "VALUE": 5242880,
    "DESCRIPTION": "Positive integer maximum encoded cache-entry size in bytes; oversized results stay usable."
  }
}
```

On 2026-10-13, caching was explicitly enabled and connection settings were
consolidated in the local `test_config_wsl.json` and `deploy_app_config.json`.
Both now use one `REDIS_URL`; `REDIS_LOCATION`, `REDIS_PORT`, and `CACHE_URL`
were removed from these files. The current overrides are:
Every entry has a `DESCRIPTION` for the configuration editor:

| Setting | `test_config_wsl.json` | `deploy_app_config.json` |
| --- | --- | --- |
| `ARCHIVE_SEARCH_CACHE_ENABLED` | `true` | `true` |
| `ARCHIVE_SEARCH_CACHE_TTL_SECONDS` | `432000` | `432000` |
| `ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS` | `518400` | `518400` |
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://localhost:6379/0` |
| `CACHE_NAMESPACE` | `archives:wsl:dataset1` | `archives:deploy:dataset1` |
| `CACHE_TIMEOUT_SECONDS` | `0.5` | `0.5` |
| `CACHE_MAX_ENTRY_BYTES` | `5242880` | `5242880` |

The explicit URL preserves each file's previous Redis endpoint. Cache and RQ
use independent clients/pools but share that service's memory; namespaces prevent
environment key collisions and do not provide memory isolation. No queue or
Redis eviction settings were changed. The application still selects
`deploy_app_config.json`; the WSL file applies only when explicitly selected.
Running processes adopt these values after reload/restart. Both JSON files are
ignored by Git, so these local file edits must be copied to the intended
deployment separately; this table is the version-controlled configuration record.

Set `REDIS_URL` in the process environment only when intentionally overriding the
selected file, and apply the same override to web and worker processes. An
explicit empty or invalidly typed URL is an error and never falls back to legacy
fields. Legacy host/port files still load, but they cannot overwrite a supplied
URL. `CACHE_URL` is retired and ignored; remove it from older files to avoid a
misleading configuration-editor entry. There is no separate cache endpoint
override in this release; splitting cache and queue services would be a later
configuration change.

Booleans, strings, fractions, zero, and negatives are invalid for integer
lifetimes and size. Invalid configuration logs the setting/policy error and
disables caching while preserving fresh searches. Invalid workbook retention
defers deletion of Archive Search workbooks; other cleanup continues. An invalid
result/retention pairing disables caching but a valid workbook retention still
applies to cleanup. A longer result lifetime requires a longer retention value.
The storage adapter has no global TTL; every write receives its entry's
remaining lifetime. There is no `CACHE_TTL_SECONDS` setting.

Cache-policy validation remains best effort. Because `REDIS_URL` also configures
RQ, a malformed shared endpoint can fail queue initialization as well; this is
an invalid queue configuration rather than a cache-only outage.

Startup logs record the effective enabled/disabled state. One separate cache
connection pool is registered in `app.extensions["cache"]` per application
instance/process, without requiring an RQ worker or a startup connection.
The initial adapter supports a standalone Redis-compatible service; cluster
topologies require a future adapter change. Cache entries are shared across
browser viewers, so successful refresh updates subsequent matching searches.

For rollout sizing, storage layout, backend verification, and operational
invalidation, see [Archive Search caching in Operations](operations.md#archive-search-caching).

Configuration changes take effect when processes reload their settings. Restart the web app and workers after editing JSON directly. Older environment files may contain `FILEMAKER_*` settings; the current app does not perform FileMaker synchronization itself.

See [Operations](operations.md) for process and storage requirements, or return to the [reference index](README.md).
