# Operations reference

Operational responsibilities for the existing archives application include maintaining the file/database index, monitoring queued work, retaining backups, and managing generated artifacts.

## Web and worker processes

The Flask application is exposed as `run:app`; Gunicorn is included as its production WSGI server dependency. Executing `run.py` directly starts Flask with debug mode enabled. `worker.py` consumes RQ's `default` queue and reads `REDIS_URL` from the environment, defaulting to `redis://localhost:6379`. The application constructs its queue connection from JSON configuration. Both must reach the same Redis instance.

Web and worker processes rely on matching dependency versions, configuration files, and filesystem mounts. Their service accounts need access to the archives and inboxes, plus write access to the backup directory and `archives_application/static/temp_files/`. The supplied worker uses RQ's `Worker`; its entry point targets Linux/WSL rather than a native-Windows worker implementation.

Process-supervisor, reverse-proxy, and scheduling configuration live outside this repository. The application's configuration editor invokes the configured `APP_RESTART_COMMAND` and `APP_WORKERS_RESTART_COMMAND` hooks. The app uses `ProxyFix`, and the configuration factory enables `OAUTHLIB_INSECURE_TRANSPORT`; the deployed proxy must supply trusted forwarded headers and enforce HTTPS.

Archive search runs synchronously in the web process. Large unscoped content queries can require longer server/proxy timeouts. Text extraction, chunk population, and project/contract synchronization are responsibilities of the associated ingestion and synchronization services. Database schema and migrations are maintained in `business_services_db`; this app does not migrate the database at startup. In particular, the generated `file_content_fts_chunks.search_vector` column used by content queries is not mapped in this app's ORM, so `db.create_all()` does not reproduce the complete database schema.

The Docker files record a development web/Redis stack with site-specific CIFS mounts. They do not include PostgreSQL, an RQ-worker service, or a maintenance scheduler. They should not be read as a complete description of the deployed infrastructure.

## Administrative tools

| Endpoint | Action |
| --- | --- |
| `/scrape_files`, `/scrape_location` | Queue indexing of files and locations. |
| `/confirm_file_locations` | Queue confirmation of indexed file locations. |
| `/confirm_project_locations` | Refresh recorded project roots; unresolved roots can clear stale stored locations. |
| `/admin/db_backup` | Queue a `pg_dump` backup; optional `timeout` is in seconds, default `5400`. |
| `/admin/maintenance` | Queue temporary-file, backup, and task-record cleanup. |
| `/admin/config` | Edit JSON settings and invoke the configured worker/app restart commands. |
| `/admin/sql_logging` | Toggle SQL debugging output. |

Backups are streamed to `db_backup_<timestamp>.sql.bz2.part` and renamed to `.sql.bz2` after successful completion. They contain a compressed plain SQL dump; test recovery into a separate database using PostgreSQL tools. Maintain separate backups of the archive shares: a database dump does not include document files.

Maintenance currently expires temporary files after **3 days**, timestamped database backups after **2 days**, and task records according to the per-function map in [main/routes.py](../archives_application/main/routes.py). Cleanup runs when maintenance is invoked; the repository does not install a scheduler. Review these retention periods and retain independent recovery copies before scheduling cleanup.

RQ jobs are tracked in the `worker_tasks` table through `WorkerTaskModel`, including task ID, status, and results. Check both the worker logs and those records when an accepted operation does not finish. Task timeout values are measured in **seconds** throughout the queue helpers.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Import fails with a missing config/OAuth JSON file | Run from the repository root and supply both selected JSON files; loading happens at import time. |
| Database connection error | Verify `Sqalchemy_Database_Location`, database name, credentials, string port/SSL values, network access, and URI encoding. |
| Missing relation, column, `vector` type, or `search_vector` | Confirm the canonical database migrations/extensions are installed. This app does not migrate the database at startup. |
| Jobs remain queued | Confirm a worker listens on `default` and its environment `REDIS_URL` reaches the same Redis instance as the app's JSON configuration. |
| File edit fails or paths point to the wrong place | Confirm mounts, service-account permissions, and the correspondence between app and user archive roots. Use the configured user-path prefix. |
| Content search misses a known document | Check extraction status and chunk coverage; run location/index maintenance if metadata is stale. Filename indexing does not imply text extraction. |
| Project/CAAN searches have no archive scope | Check synchronized relationships and recorded `projects.file_server_location`; confirm project locations when appropriate. |
| Google rejects the redirect URI | Match the OAuth web client's callback URI to the actual origin and `/google_auth/callback`, including scheme and port. |
| API returns `401` for a Google user | Supply an active application account with an application password for Basic authentication, or use the supported session flow. |
| Backup fails | Check `pg_dump` availability/version, executable prefix, database connectivity, worker timeout, and backup-directory permissions. |

Admin diagnostics include `/test/database_info`, `/test/see_config`, and `/test/rq`. Database/config diagnostics can expose connection details, so treat their output as sensitive. `/test/file_server_access` checks permissions by creating, editing, and deleting temporary files in the configured directories; it is an active filesystem check.

See [Configuration](configuration.md) for setting definitions, or return to the [reference index](README.md).
