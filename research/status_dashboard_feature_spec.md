# System Status Dashboard Feature Specification

Last updated: 2026-10-06

Status: proposed implementation specification for `feature/status-panel`.

## Purpose

Replace `/test/rq`, `/test/file_server_access`, and `/test/database_info`
with an admin system-status dashboard at `/status`. Provide a consistent
assessment of database connectivity, Redis health, queue activity, worker
availability, and file-server access, with useful partial results during outages.

This document specifies future behavior; it does not record an implemented change.

## Current context

- The three existing routes are in `archives_application/main/routes.py` and
  require the ADMIN role.
- `/test/database_info` runs `SELECT 1`, exposes the database URI and pool status,
  and returns JSON even when the connectivity test fails.
- `/test/rq` pings the app queue connection and returns Redis INFO data. It does
  not inspect workers or backlog.
- `/test/file_server_access` creates, edits, and deletes temporary files on a GET
  request. It checks backup, archives, and archivist inbox locations.
- The app constructs `app.q` from application configuration. Diagnostics inspect
  only that configured Redis instance/database and the app's queue.
- The supplied Compose configuration defines Redis and the web app, but no
  worker service. Workers may therefore be managed separately.

## Scope

Deliver an HTML dashboard, a JSON snapshot endpoint, an explicit filesystem
permission probe, and shared diagnostic helpers.
Remove the three old routes in the same release; update any links and documented
operator workflows. Old URLs return 404, including POST to `/test/rq`; do not
redirect the old filesystem GET to a mutating operation.

Keep `/test/see_config`, `/test/logging`, and `/admin/sql_logging` outside this
replacement. Do not add restarts, job cancellation/requeue, automatic repairs,
database migrations, service-manager integrations, or a public health endpoint.
Worker Redis URL configuration and app/worker URL mismatch detection or correction
are outside this feature's scope.

## Routes and access

| Route | Method | Contract |
| --- | --- | --- |
| `/status` | GET | Admin HTML dashboard shell; obtains results from the JSON endpoint. |
| `/api/status` | GET | Fresh, read-only component snapshot. |
| `/api/status/file_server_probe` | POST | Explicit read/write/edit/delete probe of configured locations. |

Require an authenticated, active ADMIN user on every new route. Follow the
existing browser login flow for the HTML route. JSON routes return JSON 401 for
missing/inactive authentication and 403 for an authenticated non-admin; they
must not redirect to login HTML. Use session authentication for this first
release, and protect the POST with CSRF validation. Never run checks before
authorization succeeds.

Set `Cache-Control: no-store` on diagnostic responses. Do not expose passwords,
credential-bearing URLs, raw INFO dumps, raw exception strings, or job arguments.
Return stable error codes and short sanitized messages; log useful sanitized
technical details server-side without connection credentials.

## Dashboard behavior

- Add an admin navigation link using the existing layout and styling.
- Show overall status, app version, snapshot time, and total check duration.
- Show separate database, Redis, queue, workers, and file-server sections.
- Use text labels as well as color; show failures and unknown results clearly.
- Provide manual Refresh only. Allow only one outstanding refresh per browser.
- Preserve the last displayed results after a failed refresh, visibly mark them
  stale, and show the failed refresh time. Never relabel old results as fresh.
- Provide a clearly labeled “Test file write/edit/delete access” action explaining
  that it creates and removes temporary files. Display its results separately
  with their own timestamp. Routine refresh must not rerun this action.
- Keep deep-probe results in the current page only in the first release; no
  persistent shared probe history is required. Show “Not tested” after reload.
- Cap worker details at a configurable limit and indicate truncation while
  keeping aggregate counts accurate. Avoid fetching unbounded job lists.

## Checks

### Database

Execute `SELECT 1` using a dedicated short-lived connection. Report connectivity,
duration, and sanitized pool information. Do not expose the database URI. Bound
connection acquisition, connection establishment, and statement execution;
release the connection on every path. Do not invalidate or roll back the
request's authentication session as part of diagnostic error handling.

This proves a trivial query succeeds, not schema correctness or synchronization
freshness.

### Redis

Use the same configured Redis instance and logical database as `app.q`. Run PING
and collect an allowlisted subset of INFO: version, uptime, memory usage/limit,
eviction policy, evicted keys, rejected connections, and applicable persistence
status. Show persistence mode so disabled persistence is not labeled a failure.

Successful PING establishes a reachable Redis server responding to commands.
Connection refusal, DNS failure, authentication failure, and timeout receive
distinct sanitized codes where distinguishable. Do not infer whether an OS
process exists from an unreachable connection.

PING failure is unhealthy. PING success with INFO denied remains reachable but
degraded because detailed diagnostics are unavailable. An applicable failed
persistence operation is degraded. Historical eviction/rejection counts are
informational; cumulative nonzero values alone do not establish a current fault.

### Queue

Inspect the actual app queue, rather than an independently hardcoded queue name.
Report queued, running, failed, scheduled, and deferred counts, suspension state,
and oldest waiting job age when available. Do not return payloads or tracebacks.
Use bounded reads for age calculation; mark unavailable metrics unknown rather
than scanning the entire backlog. Historical failed jobs alone are informational.

Report degraded when the queue is suspended or the oldest waiting job exceeds
the configured warning age. Report metrics unknown when Redis is unavailable;
never substitute zero counts for failed reads.

### Workers

Use RQ worker registrations associated with the app queue and inspect state,
heartbeat, name, hostname, PID, listened queues, and current job identifiers.
Use APIs supported by the locked RQ version. RQ documents registration lookup
and heartbeat metadata in its [worker documentation](https://python-rq.org/docs/workers/).

Classify workers as responsive, stale, or unknown. A responsive worker has a
valid recent heartbeat and an eligible serving state; idle and busy are both
normal. Suspended workers do not count toward serving capacity. Missing or
unreadable heartbeat/state is unknown, not healthy. Show workers starting up
separately until they become eligible. Use UTC for heartbeat comparisons, and
flag future timestamps beyond an allowed clock skew as unknown.

- Zero responsive eligible workers: unhealthy.
- Some responsive workers but fewer than the configured minimum: degraded.
- Minimum met, with stale/unknown registrations: degraded, with explanatory text.
- Minimum met and no adverse registrations: healthy.
- Redis unavailable or worker enumeration fails: unknown.

Show stale registrations separately from responsive worker counts. A Redis
registration and PID do not prove that an OS process currently exists; label this
section “RQ worker availability.” Direct host process checks are out of scope.
A responsive busy worker does not prove that its current job is making progress.

Inspect only worker registrations visible in the app-configured Redis database
and associated with the app queue. Do not inspect worker connection configuration
or diagnose why a worker is absent. No changes to `worker.py`, worker startup,
or Redis configuration resolution are required.

### File server: routine checks

Check `ARCHIVES_LOCATION`, `ARCHIVIST_INBOX_LOCATION`, and
`DATABASE_BACKUP_LOCATION` separately. Validate configuration, directory
existence, and readability where required. For archives/inbox, attempt a bounded
directory read without recursive traversal or inventory. A readable empty
directory is valid. Backup availability requires an existing directory; its
write/delete capability requires the explicit probe.

Show configured display paths through `FileServerUtils.user_path_from_db_data`
where applicable. Accept no client-supplied target paths. If future inputs accept
Windows/UNC paths, convert them with `FlaskAppUtils.user_path_to_app_path` before IO.

Missing paths, invalid configuration, and required read failures are unhealthy.
Timeouts are unknown. A local directory at a mountpoint does not establish that
the expected SMB share is mounted. Where an expected mount identity is configured,
verify it using deployment-appropriate metadata; a confirmed mismatch is
unhealthy. Without this configuration, explicitly show mount identity as
unverified and do not claim confirmed SMB connectivity.

Routine results must state that write/edit/delete permissions have not been tested.

### File server: explicit probe

For backup, test write/delete. For archives/inbox, test read/write/edit/delete,
including reading back the generated content. Use uniquely named, exclusively
created temporary files in only the configured locations. Clean up in `finally`
and delete only files created by this probe. Surface cleanup failure separately;
log the generated orphan path for operator cleanup. Preserve per-step results
when later steps fail. Never use existing archive content as a test file.

Run through a bounded helper independent of RQ so the action remains useful when
Redis or workers fail. Limit concurrent deep probes across web processes, returning
409 when one is already running. Use an expiring local interprocess lock, not a
Redis lock. A timeout may interrupt cleanup: report possible orphan creation and
provide an operator cleanup procedure limited to verified probe-owned files.
Do not add automatic directory-wide cleanup.

This small diagnostic probe belongs in a helper, not a route. It must not invoke
ServerEdit or enqueue reconciliation because it creates no archived content or
database records. Any later bulk filesystem action must follow repository
ServerEdit/RQ requirements.

## Result contract and aggregation

Every component contains `status`, `checked_at` (UTC ISO-8601), `duration_ms`,
`code`, `message`, and `details`. Include `schema_version: 1`, `generated_at`,
`duration_ms`, app version, and overall `status` at the top level. Missing metrics
are null or explicitly unknown, never fabricated zeros. Separate Redis reachability
from metadata availability, and routine file availability from deep-probe results.

Example snapshot shape (details shortened):

```json
{
  "schema_version": 1,
  "generated_at": "2026-10-06T18:00:00Z",
  "duration_ms": 125,
  "app_version": "example",
  "status": "unhealthy",
  "components": {
    "workers": {
      "status": "unhealthy",
      "checked_at": "2026-10-06T18:00:00Z",
      "duration_ms": 8,
      "code": "no_responsive_workers",
      "message": "No responsive workers are serving the application queue.",
      "details": {"queue": "default", "responsive_count": 0, "minimum": 1}
    }
  }
}
```

Overall aggregation, in precedence order: any unhealthy component makes the
snapshot unhealthy; otherwise any unknown required component makes it unknown;
otherwise any degraded component makes it degraded; otherwise healthy.
Unperformed deep probes and unconfigured optional mount verification are explicit
coverage limitations, not failed required checks. The overall label describes
the checks performed, not proof that every app operation works.

`/api/status` returns 200 for healthy/degraded and 503 for unhealthy/unknown,
always with the complete available snapshot. The HTML shell returns 200 when
authorized even during dependency failures, and must render JSON bodies from
503 responses. The probe returns 200 when all required steps succeed and 503
for failed/unknown steps, retaining per-location details. Unexpected application
errors return sanitized JSON 500; authorization, CSRF, and concurrency errors
retain their own HTTP statuses.

## Execution limits and configuration

Proposed defaults, to be verified against deployed driver/RQ settings:

| Setting | Default | Purpose |
| --- | --- | --- |
| `STATUS_CHECK_TIMEOUT_SECONDS` | 2 | Budget per routine component/check. |
| `STATUS_SNAPSHOT_TIMEOUT_SECONDS` | 10 | Total routine collection budget. |
| `STATUS_FILE_PROBE_TIMEOUT_SECONDS` | 10 | Total deep-probe budget. |
| `STATUS_MIN_WORKERS` | 1 | Minimum responsive workers on the app queue. |
| `STATUS_WORKER_STALE_SECONDS` | 180 | Heartbeat threshold; must exceed normal heartbeat spacing. |
| `STATUS_CLOCK_SKEW_SECONDS` | 30 | Allowed future heartbeat skew. |
| `STATUS_QUEUE_WARNING_AGE_SECONDS` | 300 | Oldest waiting job warning threshold. |
| `STATUS_WORKER_DETAIL_LIMIT` | 50 | Maximum worker detail rows. |
| `STATUS_EXPECTED_MOUNTS` | empty | Optional configured target identities for file locations. |

Validate positive budgets/counts and sensible threshold relationships at startup.
Use explicit Redis connect/read timeouts, database acquisition/connect/statement
timeouts, and a bounded filesystem execution mechanism. A thread future timeout
alone does not stop blocked SMB IO. Use killable isolated helper processes with
bounded concurrency for filesystem checks; close/reap helpers and document that
an OS-level uninterruptible IO operation can still require host recovery. Do not
claim an unconditional hard deadline under that condition.

Checks must be independent except for Redis-dependent queue/worker inspection.
Catch component failures individually; continue collecting other results within
the total budget. Reuse one Redis health result rather than repeating failed
connections for every dependent metric. No automatic retries during a snapshot.

Authentication currently depends on database-backed users. If authentication
cannot complete during a database outage, the dashboard may itself be inaccessible.
Document this limitation; do not bypass authorization or add a public diagnostics
fallback in this feature.

## Implementation organization

Keep handlers thin in the main blueprint. Put collection, classification,
redaction, and probe logic in a dedicated main status module, with template and
browser code following existing repository conventions. Share helpers rather
than calling the removed route handlers. No database schema changes are needed.

## Acceptance and verification

1. Authorized admins can open the dashboard and retrieve JSON; anonymous,
   inactive, and non-admin users cannot trigger checks. Missing/invalid CSRF
   prevents the probe.
2. A healthy fixture produces healthy results; database failures, Redis failures,
   and denied INFO produce the specified independent outcomes and HTTP statuses.
3. Worker tests cover idle/busy workers, no workers, insufficient workers, stale
   or missing heartbeats, future timestamps, suspension, unrelated queues, and
   Redis failure. Unknown results are never shown as zero workers.
4. Queue tests cover suspension, old backlog, historical failures, empty queues,
   and bounded/unavailable age lookup.
5. Routine filesystem checks create no files. Tests cover missing/inaccessible
   directories, empty readable directories, mount mismatch, and helper timeout.
6. Probe tests use temporary directories, verify actual read-back and cleanup,
   preserve step failures, and cover cleanup failure, concurrency, and timeout.
   No production share is mutated by automated tests.
7. Credentials and sensitive job data never appear in JSON, HTML, or captured
   diagnostic logs. One dependency failure does not erase other results.
8. Diagnostic tests prove Redis, queue, and worker checks use the app-configured
   Redis connection and app queue, without inspecting worker configuration.
9. Old routes return 404; route index and navigation reflect the replacements.
10. Manual staging verification covers normal operation, stopped workers,
    disconnected Redis, unavailable database after authentication where feasible,
    inaccessible test shares, explicit probe, manual refresh, and stale refresh display.
    Capture dashboard screenshots for the implementation PR.

## Rollout and operational follow-on

Inventory consumers of the removed endpoints before deployment and migrate them
to `/api/status`; document the changed response format and 503 behavior. Configure
worker count, heartbeat threshold, and expected mounts for the actual deployment.
Verify workers are provisioned separately where Compose
does not supply them. Record the probe orphan cleanup procedure and authentication
outage limitation in operator documentation.

Append a development-journal entry when the implementation changes deployed
behavior, covering route removal, diagnostics, filesystem side effects,
verification, and operator work. This specification-only change
does not require a journal entry.
