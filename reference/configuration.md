# Configuration reference

The application configuration describes the existing PPDO archive environment: database and queue connections, mounted storage, filing conventions, roles, and operational limits.

The package currently selects `deploy_app_config.json` and `google_client_secret.json` in `archives_application/__init__.py`. Both files are loaded at import time and ignored by Git. The OAuth file contains a top-level `web` object with `client_id` and `client_secret`. A `test_config*.json` filename is not selected automatically, and `credentials.env` is not automatically loaded. The worker separately reads its Redis address from the `REDIS_URL` environment variable.

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
  "REDIS_LOCATION": {"VALUE": "localhost", "DESCRIPTION": "Redis hostname"},
  "REDIS_PORT": {"VALUE": "6379", "DESCRIPTION": "Redis port as a string"},
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
| `REDIS_LOCATION`, `REDIS_PORT` | Build the app's Redis URL. Use a string port and match the worker's `REDIS_URL`. A direct `REDIS_URL` setting can be used when `REDIS_LOCATION` is absent. |
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

Configuration changes take effect when processes reload their settings. Restart the web app and workers after editing JSON directly. Older environment files may contain `FILEMAKER_*` settings; the current app does not perform FileMaker synchronization itself.

See [Operations](operations.md) for process and storage requirements, or return to the [reference index](README.md).
