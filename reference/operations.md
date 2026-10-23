# Operations reference

Operational responsibilities for the existing archives application include maintaining the file/database index, monitoring queued work, retaining backups, and managing generated artifacts.

## Web and worker processes

The Flask application is exposed as `run:app`; Gunicorn is included as its production WSGI server dependency. Executing `run.py` directly starts Flask with debug mode enabled. `worker.py` consumes RQ's `default` queue. Web queue, cache, and worker resolve one shared `REDIS_URL`: process environment first, explicit selected-JSON value second, then legacy host/port fallback. The worker reads the application's selected JSON file instead of using an independent localhost default. Apply any environment override consistently to all process roles.

Web and worker processes rely on matching dependency versions, configuration files, and filesystem mounts. Their service accounts need access to the archives and inboxes, plus write access to the backup directory and `archives_application/static/temp_files/`. The supplied worker uses RQ's `Worker`; its entry point targets Linux/WSL rather than a native-Windows worker implementation.

Worker startup now imports the application's configuration and therefore needs
the selected JSON and OAuth files available in the working directory, as the web
process already does. Restart web and worker processes together after changing
the shared endpoint or its process-environment override.

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

Maintenance expires ordinary temporary files after **3 days**, Archive Search
workbooks after **6 days** by default, timestamped database backups after **2
days**, and task records according to the per-function map in
[main/routes.py](../archives_application/main/routes.py). Archive Search
workbooks use `ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS` and completed-file
modification time; downloads/hits do not renew their age. Both supported
14-digit legacy and 20-digit current timestamp filenames receive this policy.
Invalid workbook retention defers their deletion and logs an error while other
cleanup continues. Cleanup runs when maintenance is invoked; the repository
does not install a scheduler. Review these retention periods and retain
independent recovery copies before scheduling cleanup.

RQ jobs are tracked in the `worker_tasks` table through `WorkerTaskModel`, including task ID, status, and results. Check both the worker logs and those records when an accepted operation does not finish. Task timeout values are measured in **seconds** throughout the queue helpers.

Requires database revision `8e2b7c4d9a10`: task JSON is now `status_details`,
and the model includes nullable `time_started` and `time_updated` columns.
The shared queue helpers record `time_updated` at enqueue and every status/details
save, `time_started` on task initiation, and `time_completed` on normal completion
or explicitly handled failure. Timestamps follow the existing naive datetime
convention; historical rows are not backfilled. Deploy and restart matching web
and worker code together against the migrated schema.

The file scraper commits a directory checkpoint before processing each directory
and saves the current file before its filesystem I/O. Hashing refreshes progress
every 30 seconds while reads return. `status_details` includes relative
`Next Start Location`, `Current File`, `Current File Started`, `Last Completed File`,
counters, errors, elapsed time, and `Stop Reason` (`time_limit`, `scan_complete`,
or `failed`). A budget-limited run is normally `finished` with `time_limit`; this
does not mean the whole archive was scanned. RQ timeouts propagate after saving
a failed task record. A hard termination can still leave status `started`, with
its last committed checkpoint and timestamps available.

Daily requests without `start_path` resume the newest non-test scraper task with
a valid checkpoint, including interrupted/failed runs. Selection uses enqueue
time and ID, not later edits to an older task. Explicit starts retain precedence;
Windows paths are mapped to the application mount. Missing checkpoint directories
fall back to their nearest existing ancestor. Resume always rechecks the saved
directory from its first file. Files and locations commit together, and changed
content updates the existing location's file association. Existing duplicate
locations and obsolete content records are not cleaned up by this change.

A run makes one circular pass from the starting directory, wrapping through the
earlier directories, or stops at its time budget. Seeking follows the starting
branch without enumerating earlier subtrees; those are visited on wraparound.
A complete pass resets the next
start to the archive root (`.`); root-level files are included. Existing filename,
extension, and empty-file exclusions remain in force. Enumeration errors fail the
run instead of silently skipping a subtree. A failed checkpoint save also stops
the run, preserving the exception cause and retry directory, rather than being
treated as an ordinary per-file error. The route rejects nonpositive budgets
and sets RQ's timeout to the full duration in seconds plus 300 seconds.

Deadline checks occur between files and between 1-MiB hash reads; incomplete hashes
are discarded. A blocked SMB read cannot report progress or observe a cooperative
deadline until it returns, so RQ remains the outer limit. A single file or directory
whose replay exceeds the whole budget can still prevent forward progress. Use the
saved current-file/start-time information to investigate those cases; this release
does not introduce a file-level resume cursor or skip a repeatedly slow file.

File-location confirmation also saves its active location ID, relative file path,
and check start time before filesystem I/O. Its `status_details` retains the
existing confirmation/removal/error counters and adds `Locations Checked`,
`Last Completed Location ID`, `Last Completed File`, elapsed time, and `Stop Reason`
(`time_limit`, `no_locations`, or `failed`). Progress is refreshed before every
check and after slow checks when at least 30 seconds have elapsed. Counters count
committed checks; small tables can be revisited while the budget remains.

Confirmation continues from the oldest `existence_confirmed` timestamps, with
never-confirmed NULL values first and location ID breaking ties. It does not use
the scraper's resume-directory selector. Missing-location removal, last-copy file
cleanup, and unlinking historical archive-event file IDs commit together before
progress is saved. Per-location errors roll back that location and remain in the
summary; progress-save failures and RQ timeouts fail the task and propagate.
Hard kills can leave `started`, with the last saved snapshot available. Deadline
checks use monotonic time before each location and after filesystem checks, so a
slow check returning after the budget cannot start a removal. A blocked check
still relies on RQ's outer timeout. Empty location tables finish immediately;
the route requires a positive duration and uses its full seconds plus 600 seconds
for the RQ timeout, including durations longer than a day.

Location-specific scraping reports `Phase` (`validating`, `confirming_locations`,
`discovering_files`, `complete`), current directory/file/location ID, elapsed time,
and phase-specific counters in `status_details`. Confirmation reuses the atomic
per-location helper and saves the active path before filesystem checks. Saves
refresh after 30 seconds of returning work, at phase/directory transitions, and
before/after enqueue calls. `Last Checked File`, `Last Completed Location ID`,
`Last Enqueued File`, and `Child Task IDs` retain completed work diagnostics.
`Files Enqueued to Add` advances only after a successful enqueue.

The confirmation query matches the exact requested directory and, for recursive
scans, its separator-delimited descendants; SQL wildcard characters in names are
escaped. Nonrecursive scans restrict both confirmation and discovery to the
selected directory. Archive-root scans support empty/NULL root-directory records.
Confirmation uses bounded ID batches and a set of known paths for discovery
membership checks. A missing path is removed from that set only after cleanup
commits, so a file that reappears can be discovered again. With `confirm_data=False`,
confirmation and known-path skipping remain disabled. Existing filename/extension
exclusions and eligibility of empty files remain unchanged.

A parent finishes when confirmation/discovery/enqueueing completes; indexing
outcomes belong to the child tasks. Individual confirmation/enqueue errors stay
in the summary with `Stop Reason=completed_with_errors`; clean completion uses
`completed`. RQ timeouts, progress-save failures, and enumeration errors use the
failed-task path and propagate. Invalid, unavailable, or outside-archive target
directories fail before cleanup. A hard kill can leave the last saved phase and
child IDs under status `started`. These snapshots do not implement automatic
resume or deduplicate pending child jobs; a rerun can queue a file again before
its earlier indexing job has created its location record.

## Archive Search caching

With `ARCHIVE_SEARCH_CACHE_ENABLED=true`, `/archives_search` and `/file_search`
reuse complete successful results for five days by default. This intentionally
changes the fresh-on-every-open browser URL behavior documented in journal
entries 044 and 046. A **Cached results** or **Fresh results** badge sits beside
**Run fresh search** in the **Results** panel header, alongside **Download Excel**.
The workbook note sits beneath that header; the top toolbar contains navigation
and help. Result actions wrap below the heading on narrow screens.
The **Coverage & freshness** dropdown contains source
generation time in UTC, cache expiration when available, and the note about
recent archive/index changes. **Run fresh search** preserves active search options
and adds `refresh=1`, bypassing lookup and publishing a new generation only on
success. A failed refresh reports its failure and preserves the previous entry
for normal requests. Search-result HTML uses `Cache-Control: private, no-store`.
API searches remain fresh with their existing response contract.

Freshness covers scope resolution, file results, all attached locations,
snippets, and coverage. Indexing/extraction/project updates do not invalidate
entries automatically. Empty results and scope/coverage warnings also cache.
No database schema change or cache migration is needed. Disabling the flag and
restarting restores fresh browser searches without deleting entries or files.

The local `test_config_wsl.json` and `deploy_app_config.json` were opted in on
2026-10-13. Both now explicitly set the shared `REDIS_URL` to
`redis://localhost:6379/0`, preserving their previous endpoint, with independent cache clients and the distinct
`archives:wsl:dataset1` / `archives:deploy:dataset1` namespaces. See
[Configuration](configuration.md#archive-search-cache-and-workbook-retention)
for the complete override record. These ignored JSON files are local settings;
running processes and remote deployments require their normal configuration
reload/copy steps. The application still selects the deployment file by default.

Use the shared endpoint with a separate cache client/pool and a distinct environment/dataset namespace;
see [Configuration](configuration.md#archive-search-cache-and-workbook-retention)
for all settings and a JSON example. Namespace changes are the initial bulk
invalidation mechanism; never use `FLUSHDB`/`FLUSHALL` on a service shared with
RQ. Old namespaced entries expire normally. Payload/search versions belong to
the consumer and must advance when result interpretation or ranking changes.

Workbooks live in the existing temporary directory on a single host, or a shared
volume mounted at the same path on every serving host. Verify web/worker write
and cleanup access and this storage layout before enabling caching. A shared
Redis cache does not make another host's local downloads available. Completed
XLSX files publish through same-directory staging and atomic rename; workbook
references use generation IDs so an older export cannot replace a newer result.
Files are shared artifacts and are never added to a viewer's logout-deletion
list. Missing files/references regenerate from still-valid cached data, retaining
the original Coverage timestamp and result expiration. The replacement file has
its own publication age. Downloads continue to serve retained files after the
result expires; a normal search at expiration runs fresh.

The five-day lifetime and five-MiB per-entry cap do not bound aggregate Redis
memory. The selected deployment shares this service with RQ, so verify spare
capacity and queue-safe resource/eviction behavior. A separate bounded cache
service would require a later configuration split. No global memory limit or
queue eviction policy is set by these application settings. The offline analysis in the
[feature spec](archive_search_cache_feature_spec.md#verification-and-acceptance)
found a peak of 50 successful browser searches per five days and estimated
256–441 MiB with overhead/headroom; 512 MiB is a conservative planning allowance,
not expected usage or a request to alter an existing Redis limit.

The 26 locally retained example workbooks total 2,259,171 bytes, average 86,891
bytes, and peak at 1,032,493 bytes. A rough six-day allowance of 60 generations
(scaling the observed 50/five-day workload) gives about 5 MiB at the sample mean,
or 59 MiB if each reaches the sample maximum; allow about 120 MiB with twofold
headroom. This is a planning estimate, not a measured six-day traffic bound.
Refreshes, concurrent misses, failed reference writes, and manual deletion can
create extra files. Confirm capacity beyond other temporary artifacts and refine
both cache and disk allowances from actual traffic after rollout.

Before enabling on the selected deployment, run the disposable-key Redis
contract check with `ARCHIVES_CACHE_TEST_URL` set to that endpoint:

```bash
.venv/bin/pytest -q tests/test_cache_redis_contract.py
```

This opt-in test uses unique `archives-cache-contract:<uuid>` keys, checks
independent TTLs and complete atomic publication, and deletes only its own keys.
It does not change server configuration. The test-only
`ARCHIVES_CACHE_TEST_SOCKET` option supports a disposable local Redis service;
neither test variable is an application configuration setting. Validate TLS,
authentication, network access, and shared workbook storage on the actual
deployment separately.

Application `archive_search_cache` log events distinguish lookup hit/miss,
refresh, malformed/oversized payloads, backend failure, serialization and write
outcomes, and workbook reuse/regeneration. Fields include namespace/consumer,
identity digest, generation ID, lifetime, payload size, source age, and relevant
elapsed times. Raw queries, file-server paths, credentials, and payloads are
excluded from these cache events. Every valid request still gets best-effort
current-viewer telemetry; warm hits retain those writes, so duration alone is
not a hit-rate metric. Backend failures/timeouts fall back to fresh searches
without retries or serving expired entries. Concurrent misses/refreshes may
duplicate work; the last completed atomic publication wins.

For a staged opt-in rollout, verify a warm repeat skips scope/search/metadata/
snippet/coverage queries and XLSX writing, compare output and current-viewer
telemetry, exercise refresh and a missing workbook, and capture fresh/cached
notices plus the fresh-search control for the implementation PR. Monitor cache
size/outcome logs and temporary-disk use, then adjust limits from measured data.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Import fails with a missing config/OAuth JSON file | Run from the repository root and supply both selected JSON files; loading happens at import time. |
| Database connection error | Verify `Sqalchemy_Database_Location`, database name, credentials, string port/SSL values, network access, and URI encoding. |
| Missing relation, column, `vector` type, or `search_vector` | Confirm the canonical database migrations/extensions are installed. This app does not migrate the database at startup. |
| Jobs remain queued | Confirm a worker listens on `default`, loads the same selected JSON file, and has the same `REDIS_URL` environment override as the web process. |
| File edit fails or paths point to the wrong place | Confirm mounts, service-account permissions, and the correspondence between app and user archive roots. Use the configured user-path prefix. |
| Content search misses a known document | Check extraction status and chunk coverage; run location/index maintenance if metadata is stale. Filename indexing does not imply text extraction. |
| Project/CAAN searches have no archive scope | Check synchronized relationships and recorded `projects.file_server_location`; confirm project locations when appropriate. |
| Google rejects the redirect URI | Match the OAuth web client's callback URI to the actual origin and `/google_auth/callback`, including scheme and port. |
| API returns `401` for a Google user | Supply an active application account with an application password for Basic authentication, or use the supported session flow. |
| Backup fails | Check `pg_dump` availability/version, executable prefix, database connectivity, worker timeout, and backup-directory permissions. |

Admin diagnostics include `/test/database_info`, `/test/see_config`, and `/test/rq`. Database/config diagnostics can expose connection details, so treat their output as sensitive. `/test/file_server_access` checks permissions by creating, editing, and deleting temporary files in the configured directories; it is an active filesystem check.

See [Configuration](configuration.md) for setting definitions, or return to the [reference index](README.md).
