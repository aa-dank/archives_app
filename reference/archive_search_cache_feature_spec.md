# Archive Search Cache Feature Specification

**Date:** 2026-10-09

**Last updated:** 2026-10-13

**Status:** Implemented; browser caching remains disabled by default pending deployment opt-in

**Branch:** `feature/search-cache`

## Purpose and selected design

Reuse completed Archive Search results for up to five days so repeated searches
avoid repeating scope resolution, database queries, result enrichment, coverage
calculations, and Excel generation.

Implement caching for the browser workflow at `/archives_search` and its
`/file_search` compatibility alias. Keep `/api/archives_search` fresh in this
release. Give the shared search service an explicit cache policy so a later API
change can adopt caching without changing the browser implementation.

Use a small, reusable key-value storage interface with a Redis-compatible
implementation. Store structured search data as versioned JSON. Reuse existing
temporary Excel workbooks through a separate artifact reference associated with
each cached result generation. Retain Archive Search workbooks for six days by
default under their own maintenance policy; regenerate a workbook only when
its reference or file is unavailable.

Future routes can use the same storage interface and cache conventions for their
own data. Future image/file caching can use artifact storage alongside it.
Dashboard chart caching, a general file-storage service, and AWS infrastructure
provisioning are outside this implementation.

## Existing behavior and integration points

- [Archive Search service](../archives_application/archiver/archive_search.py):
  `ArchiveSearchRequest` normalizes validated input; `ArchiveSearchRun.execute()`
  records lifecycle telemetry around `_execute_search()`;
  `prepare_archive_search_page()` builds the browser view and writes a workbook.
- [Archive routes](../archives_application/archiver/routes.py):
  browser searches use validated GET parameters; legacy POST forms redirect to
  GET; `timestamp` identifies an existing temporary workbook download.
- [Results template](../archives_application/templates/archive_search_results.html):
  displays a bounded HTML result list, coverage, snippets, and an Excel link.
- [Search telemetry model](../archives_application/models.py):
  `ArchiveSearchRunModel` stores parameters, request user/source, status,
  duration, result count, and coverage; it does not store result snapshots.
- [Temporary-file maintenance](../archives_application/main/main_tasks.py):
  cleanup removes temporary files older than the configured lifespan. The
  current maintenance route supplies three days; cleanup runs when invoked.
- [URL tests](../tests/test_shareable_search_urls.py):
  currently assert that reopening a search URL executes a fresh search and
  creates another workbook.

Entries 044 and 046 in the [development journal](development_journal.md) document
the current fresh-search URL contract. The implementation will intentionally
change normal browser URL reuse when caching is enabled.

## First-release scope

### Required behavior

1. A valid browser search may reuse a matching, unexpired successful result.
2. A miss runs the existing search and attempts to cache its complete result.
3. Results expire 432,000 seconds (five days) after successful search completion
   by default, with the lifetime selected explicitly for each cached generation.
4. Viewing cached results does not extend their expiration.
5. The result page shows the source-data generation time and offers
   **Run fresh search**.
6. Cached searches reuse a workbook when it exists. Maintenance retains Archive
   Search workbooks for six days by default, independently of other temporary
   files. A missing workbook is regenerated from the cached result without
   repeating the database search.
7. Cache problems do not prevent the existing fresh-search workflow.
8. Every validated browser search request still receives its own best-effort
   telemetry lifecycle, including requests served from cache.

Cache successful zero-result searches and successful searches with scope or
coverage warnings. Do not cache validation errors, search exceptions, incomplete
runs, rendered pages, or exception responses.
Empty successful results use the same five-day default as nonempty results;
a separate shorter empty-result lifetime is not required in this release.

### Exclusions

- API caching or API request/response changes.
- Dashboard charts, previews, original archive files, or other route integration.
- Saved-search history or guarantees of exact historical database snapshots.
- Automatic invalidation after every archive, extraction, or project update.
- Search ranking changes, larger result limits, or new database indexes.
- Database schema changes, migrations, or a cache table in PostgreSQL.
- A new cleanup scheduler, cache administration UI, or distributed generation
  locks in the initial release.

## User and URL contract

Preserve the current search parameters, validation, legacy POST redirect,
compatibility alias, and timestamp-download behavior.

Add one browser control parameter:

| Parameter | Accepted value | Behavior |
| --- | --- | --- |
| `refresh` | `1`, or omitted | `1` bypasses result lookup and runs a fresh search. |

Reject duplicate `refresh` parameters and all other explicit values, including
an empty value, with `400 Bad Request`. A refresh request must also contain a
valid search. A timestamp download remains a separate request and cannot be
combined with search or refresh parameters.

The fresh-search link is built from the validated active search and
`refresh=1`. Normal search-form submissions and normal shareable search links
omit `refresh`. An explicitly shared URL containing `refresh=1` deliberately
requests a fresh search whenever opened. Do not add a canonical-URL redirect to
normal GET submissions.

For every result page:

- Show **Results generated at ...**, using the source-data timestamp.
- For a hit, identify the results as cached and show their expiration time.
- Provide **Run fresh search** with every active search option preserved.
- Explain that cached results can omit recent archive/index changes.
- Use an explicit timezone. Store cache metadata in UTC and display UTC in the
  first release; avoid relying on the web host's local timezone.
- Keep HTML limits, ranking, path whitespace, escaping, and snippet highlighting
  consistent with fresh results.

Send `Cache-Control: private, no-store` on search-result HTML responses so an
intermediary or browser does not substitute its own result-page caching policy
for application expiration and refresh behavior. Preserve workbook-download
response behavior.

## Reusable storage boundary

Add a small module such as `archives_application/cache.py`, independent of
archive search, Flask routes, database models, and artifact rendering.

The initial interface needs only:

```python
get(key: str) -> bytes | None
set(key: str, value: bytes, ttl_seconds: int) -> None
delete(key: str) -> None
```

Callers own key construction, serialization, payload validation, and freshness
metadata. The backend owns connection details and atomic per-key writes.
Translate backend failures to a specific cache-unavailable exception; consumers
handle that exception without swallowing database/search exceptions.

### Lifetime policy per entry

Every cache write supplies its own `ttl_seconds`. The reusable backend has no
global expiration setting and must not infer lifetime from a key prefix or
content type. Search results, chart images, and other entries can have different
lifetimes even when they share one backend.

Each consumer resolves a server-side policy before lookup/generation:

- Its configuration supplies the default lifetime.
- A particular caching operation may supply an explicit server-side override.
- The resolved lifetime belongs to that entry/generation and determines its
  expiration. Reads never renew it.
- An independently generated file/image may use its own lifetime. A reference
  derived from another entry is capped by that source entry's remaining lifetime.

For Archive Search, `ARCHIVE_SEARCH_CACHE_TTL_SECONDS` supplies the five-day
default. Pass the resolved lifetime into the run's explicit cache policy.
Do not make the generic storage adapter read Archive Search configuration.
Users control fresh versus cached results through `refresh`; they do not
choose lifetimes through request parameters in this release.

For example, a future chart consumer could configure a one-hour default and
choose a different lifetime for a particular chart generation without affecting
search results. That is an extension example, not a chart feature or new chart
configuration setting in this release.

Validate lifetimes as positive integer seconds, rejecting booleans,
nonpositive values, and malformed values. Use the explicit enable policy to
disable caching; do not use zero to mean either disabled or never-expiring.

### Client and backend integration

Register a separately configured client in `app.extensions`. Construct one
connection pool per application process and reuse it across requests.
Do not access the cache through `app.q.connection` or change queue configuration.
Caching must work without requiring an RQ worker.

The first backend uses ordinary `GET`, `SET` with expiration, and `DEL`.
Use a namespaced key prefix rather than Redis logical-database selection.
Do not require Redis modules, RedisJSON, Lua, keyspace notifications, or
multi-key transactions.

This command subset is supported by
[Amazon ElastiCache for Valkey/Redis OSS](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/SupportedCommands.html).
A migration may still require TLS, authentication, network, and cluster-client
configuration. The adapter owns those changes; plain Redis and AWS clients must
not be assumed interchangeable for every deployment topology.

Implement only the backend needed for the current deployment. Keep cluster-aware
client support as a later adapter/configuration change when the AWS topology is
selected.

## Cache identity

Build the identity from the validated `ArchiveSearchRequest`, effective search
configuration, and a versioned payload contract. Serialize identity fields as
deterministic JSON and hash them with SHA-256.

Example key layout:

```text
<CACHE_NAMESPACE>:archive-search:v1:result:<identity_digest>
<CACHE_NAMESPACE>:archive-search:v1:workbook:<generation_id>
```

Include:

- trimmed query text, preserving its remaining case, punctuation, quotes, and
  internal whitespace;
- normalized search mode;
- requested scope type and its normalized value;
- normalized extension filters, preserving their current request order;
- the effective search file limit, currently the browser Excel limit;
- content candidate limit and candidate multiplier;
- the effective lifetime selected for this result generation, including any
  server-side override, so different lifetime policies cannot reuse an entry
  written with the previous policy;
- archive mount and user-facing path mapping configuration;
- a non-secret database-target identity: host, port, and database name, or
  equivalent target fields for the configured SQLAlchemy connection;
- explicit payload/search-format version.

Use the existing request normalization. Do not introduce query lowercasing,
phrase rewriting, location-path case folding, or whitespace collapsing.
Repeated internal path spaces remain meaningful. Extension order stays in the
first-release key because the output and workbook display that order.

The browser HTML display limit is not part of the data key: cache the full
bounded search result, then apply the current HTML limit per response.
`refresh`, URL field ordering, inactive form fields, session data, user ID,
telemetry ID, and workbook filename are not part of result identity.

Resolve project/CAAN/location scopes only on a miss or refresh. Cache hits reuse
the resolved scope and its messages. Changes to project roots, CAAN links,
indexed locations, extracted text, or coverage are intentionally subject to the
same selected freshness policy (five days by default).

Use a separate namespace for each environment and archive dataset. Change it
when replacing/restoring the underlying dataset or intentionally invalidating
all application cache entries. Bump the payload/search-format version when
ranking, result structure, interpretation, or serialization changes.

The current browser search returns the same archive data for every viewer, so
result entries may be shared across viewers. Continue any applicable route
authorization before lookup. A future consumer with per-user or per-role data
must include its access scope in its own identity and check authorization on
every request.
Successful refreshes update the entry used by subsequent viewers of that same
search. This shared behavior is intentional.

## Payload and expiration

Cache an envelope containing:

| Field | Meaning |
| --- | --- |
| `schema_version` | Explicit supported serialization version. |
| `identity_digest` | Identity checked during decoding. |
| `generation_id` | Unique opaque identifier for this successful execution. |
| `generated_at` | Successful search-completion time, UTC ISO 8601. |
| `ttl_seconds` | Effective lifetime selected for this generation. |
| `expires_at` | `generated_at + ttl_seconds`, UTC ISO 8601. |
| `search_data` | Complete bounded result from `_execute_search()`. |

The payload includes every location attached to each returned file, coverage,
scope-resolution facts, messages, and warnings. It excludes request user/source,
telemetry IDs, credentials, SQLAlchemy objects, Flask objects, page-only fields,
and workbook-export failure warnings.

Use explicit JSON encoding/decoding:

- Serialize all required `ScopeResolution` fields and reconstruct that type.
- Encode matching-location ID sets as deterministic arrays and restore their
  membership behavior.
- Encode location confirmation datetimes as ISO strings and restore datetimes
  with their original naive/aware state; do not invent a timezone for existing
  database timestamps.
- Encode ranking values as finite JSON numbers and preserve nulls and ordering.
- Preserve Unicode, repeated spaces, existing snippet escaping/highlighting,
  booleans, and integer counts.
- Validate required fields, types, version, identity, timestamps, and result
  bounds on read. Treat malformed or incompatible entries as misses.
- Do not use pickle or a generic `default=str` serializer.

Keep cached search data independent of response objects. HTML truncation,
workbook preparation, and response warnings must not mutate data later reused
by another request. In particular, the existing workbook writer appends an
export warning to its input; isolate that mutation or return the warning
separately.

Use backend TTL plus an application-level `expires_at` check. Reads must not
rewrite the entry or reset TTL. Publishing a workbook reference must not extend
the source result's expiration. Apply the remaining lifetime when writing any
associated reference; skip caching if that lifetime is exhausted.
For delayed result publication, also pass only the remaining lifetime to the
backend rather than starting another full lifetime at write time.

Cache timestamps describe one completed execution, not a transactionally
consistent database snapshot. Preserve the existing database query semantics.

## Execution and failure flow

Keep `ArchiveSearchRun.execute()` as the telemetry lifecycle boundary. Pass it
an explicit cache policy, including the resolved per-generation lifetime, from
the browser page service; default the policy to disabled so existing API use
remains fresh.

1. Validate parameters and applicable authorization in the route.
2. Create this request's incomplete telemetry record using the existing
   best-effort behavior, then start its duration timer.
3. If enabled and not refreshing, construct the key and attempt a bounded read.
4. On a valid hit, decode independent search data and retain its source
   generation/expiration metadata.
5. Otherwise execute the existing search. On success, create a new generation
   and attempt to store its envelope. Return the fresh data even if storage
   fails or the payload exceeds the size limit.
6. Finalize this request's telemetry as successful or failed using the existing
   lifecycle and rollback handling.
7. Prepare the HTML slice and workbook outside the search timer, as today.

Expose generation ID, source generation/expiration times, and hit/fresh state
as run/page metadata separate from `search_data`. Keep `execute()` returning the
existing search-data contract and keep these browser fields out of the API
response. Fresh searches also receive a source generation timestamp when caching
is disabled; only cache-backed generations need an expiration notice.

A refresh skips lookup and replaces the matching entry only after a successful
search. If the fresh search fails, preserve the previous entry for other normal
requests, but report the search failure to the refresh caller. Do not substitute
old results for a failed explicit refresh.

Cache reads/writes/serialization failures and timeouts fall back to fresh data
or the existing fresh response as appropriate. Never return expired data as an
outage fallback. Unavailability must not cause retries that make a failed cache
connection dominate request latency.

Concurrent misses and refreshes may duplicate generation work in this release.
Publish complete entries atomically; the last successfully published generation
wins. Each generation retains its own timestamp and fixed lifetime.
Do not delete the old entry before refreshing or expose partially written data.
This simple publication rule is accepted for the first release, including the
possibility that an earlier-started search publishes after a later refresh.
Do not add ordering guarantees or locking solely to prevent that case.
Distributed generation locks can be added later if measured concurrent misses
justify them.

## Workbook reuse and file-cache extension

Cache workbook references separately from result envelopes, keyed by
`generation_id`. A late workbook operation for an older generation must never
overwrite a newer result entry.

An initial workbook reference contains a validated download timestamp and its
storage kind, `local-temp`. Derive the path server-side through the existing
temporary-file helper; never accept a client-supplied path or store credentials.
Keep the existing 14- and 20-digit download timestamp formats readable.

For each page request:

1. Look up this generation's workbook reference, if result caching is available.
2. Confirm the referenced file exists before offering its download link.
3. If absent or missing, build the workbook from the same search data.
4. Publish a complete file before publishing its reference. Write through a
   temporary file on the same filesystem and rename atomically.
5. Ensure the returned timestamp exactly identifies the actual published
   filename. The current helper's random collision suffix must not create an
   identifier the download route cannot resolve.
6. Store the reference for the source result's remaining lifetime.

Keep the Coverage sheet's generation timestamp tied to source search data,
including when a deleted workbook is regenerated later. Use an explicit UTC
label. A workbook download identifier may differ from the source timestamp.

If workbook creation fails, preserve usable on-page results, omit the download
link, and show the existing export-unavailable warning for that response.
Do not cache that transient warning or a failed artifact reference.
If reference storage fails, a successfully written workbook can still be
offered for the current response.

Existing timestamp downloads continue serving retained files after result-cache
expiration. They are temporary exports, not a way to reopen cached HTML results.
Preserve legacy download handling while applying the workbook-specific
retention policy described below.
Cached workbooks are shared artifacts and must not be added to the per-user
list of files deleted at logout.

### Workbook retention and cleanup

Prefer retaining generated workbooks throughout the search-cache lifetime to
recreating them during normal cache hits. Add
`ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS`, defaulting to 518,400 seconds
(six days), independently of the five-day result-cache default.

Extend the existing queued temporary-file cleanup task with an Archive Search
workbook policy. Identify published workbooks by the server-controlled
`WORKBOOK_FILENAME_PREFIX`, a valid supported download timestamp, and the
`.xlsx` extension. Apply this policy to both new and supported legacy workbook
filenames. Other temporary files, including uploads and previews, keep their
existing three-day policy; do not extend the whole temporary directory's
retention.

Measure workbook age from its completed file publication/last write rather than
source-data age or last access. Downloads and cache hits must not touch that
timestamp or extend retention. Maintenance removes eligible workbooks when it
runs; six days is a cleanup threshold, not a scheduled deletion guarantee.
Keep cleanup inside the existing task/context/status conventions without
introducing a new scheduler or request-time bulk deletion.

Validate workbook retention as positive integer seconds and greater than the
effective search-cache lifetime, including any server-side override. If a longer
search lifetime is adopted, configure a longer workbook retention as well.
An invalid pairing disables result caching with a clear configuration/policy
error while leaving fresh searches usable. An invalid workbook-retention value
must defer workbook deletion and log an error; other valid cleanup policies can
still run.

Regeneration remains a fallback for manual deletion, storage loss, or an
unavailable reference. Rebuild from still-valid cached data, preserve its source
generation time, and give the new reference only the remaining source lifetime.
The replacement file receives its own publication time and retention period;
this never renews or extends the source result's expiration.

For the default policy, results generated on day zero expire on day five.
Their workbook remains downloadable through day five and becomes eligible for
cleanup on day six; result reuse stops at its own expiration. A search at or
after that expiration runs fresh even if the old workbook remains on disk.
A forced fresh search likewise creates a new generation without deleting the
previous retained download.

### Deployment and future artifact storage

The initial deployment uses the existing temporary directory on one host, or
an existing shared volume visible at the same path to every serving web host.
A shared data cache alone does not make local workbook downloads available on
multiple hosts. Other deployment layouts require an artifact-storage follow-up.

For future files/charts, retain these extension conventions:

- A data entry can reference an opaque artifact identity plus storage kind;
  route-specific code resolves and authorizes delivery.
- Artifact bytes can live on disk, shared storage, or S3 while their freshness
  metadata lives in the key-value store.
- Logical expiration and physical file cleanup are separate.
- Each consumer chooses its own identity fields, payload version, and default
  lifetime, with an explicit resolved lifetime supplied for each write.
- Shared artifacts have cache-owned cleanup rather than session/logout cleanup.

Do not build unused S3 adapters, chart code, or a generic file-serving route now.
When S3 is adopted, application expiry checks remain authoritative because
[S3 lifecycle deletion is asynchronous](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html).

## Configuration and resource bounds

Use the existing JSON `VALUE`/`DESCRIPTION` format and documented defaults:

| Setting | Default | Purpose |
| --- | --- | --- |
| `ARCHIVE_SEARCH_CACHE_ENABLED` | `false` | Opt in to browser search/result and workbook-reference caching. |
| `ARCHIVE_SEARCH_CACHE_TTL_SECONDS` | `432000` | Search consumer's default lifetime of five days; positive integer, resolved per generation. |
| `ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS` | `518400` | Workbook cleanup threshold of six days; greater than the effective search-cache lifetime. |
| `CACHE_URL` | Unset | Explicit cache endpoint; supports the selected client's TLS/auth options. |
| `CACHE_NAMESPACE` | Unset | Required nonempty environment/dataset prefix when enabled. |
| `CACHE_TIMEOUT_SECONDS` | `0.5` | Positive connect/read timeout; bounded cache operations. |
| `CACHE_MAX_ENTRY_BYTES` | `5242880` | Maximum encoded entry size, initially 5 MiB. |

Require `CACHE_URL` and `CACHE_NAMESPACE` when enabling the feature.
Future consumers define separate default-lifetime settings when implemented;
there is no shared `CACHE_TTL_SECONDS` that governs all cached data and files.
Do not silently fall back to queue settings. The cache may explicitly use the
same service, but it still owns a separate client/configuration boundary.
Accept a real boolean for the enable setting; do not treat the string
`"False"` as truthy. Invalid cache configuration disables caching with a clear
configuration error in logs while preserving normal searches.

Check encoded size before writes and returned size before decoding. Never
truncate results or drop locations merely to fit an entry. Oversized successful
searches remain usable, with caching skipped.

TTL and an entry-size limit do not bound aggregate service memory. The rollout
must assign a cache memory budget and an eviction policy suitable for disposable
entries. Prefer an independently bounded cache deployment; if sharing a Redis
service with RQ, verify resource/eviction behavior separately. Key namespaces,
separate clients, and logical databases do not provide memory isolation.
Do not change queue eviction settings as part of this feature.

## Telemetry and operations

Keep a new `ArchiveSearchRunModel` row per valid search request when telemetry
is available, attributed to the current request's user and source.
Never reuse the original search's telemetry ID or user.

On hits, duration measures the timed cache lookup/decoding phase. On misses and
refreshes, it measures the search phase plus cache work. Initial telemetry
creation, final telemetry persistence, workbook generation, and HTML rendering
remain outside that timer. Result count and coverage come from the returned
source data.
Telemetry failures remain best effort and do not block a cache hit.

Add structured application log events for lookup outcome, refresh, invalid
payload, oversized entry, backend failure, workbook reuse/regeneration, and
write outcome. Include namespace/consumer, identity digest, generation ID,
resolved lifetime, payload bytes, source age, and relevant elapsed times.
Do not log raw queries, file-server paths, connection credentials, or payload
contents.

No telemetry schema migration is required. Use logs to distinguish hits from
fresh executions; do not infer hit rate from `duration_ms` alone.

Record effective enabled/disabled state at startup. Document namespace changes
as the initial whole-cache invalidation mechanism. Do not use `FLUSHDB` or
`FLUSHALL`, especially on a service shared with RQ.

Implementation must update the configuration/operations references and append a
journal entry describing the changed browser freshness contract, separate
workbook retention policy, configuration, verification, and rollout work.
This specification-only change requires no journal entry.

## Verification and acceptance

Use a deterministic in-memory backend only for automated tests, with a
controllable UTC clock. It is not a production cache or outage fallback.
Keep a small real-backend contract check for TTL and atomic entry publication
against the selected Redis-compatible service before enabling production.

Required automated coverage:

1. First request executes search/workbook generation; an identical request hits
   and reuses both, with unchanged ordering, locations, coverage, and warnings.
2. Both browser aliases share identity; API calls remain fresh.
3. Query/mode/scope/extensions, effective search limits, candidate settings,
   lifetime policy/override, path mapping, database target, namespace, and
   version isolate entries.
   Different HTML display limits reuse full data and slice independently.
4. Safe existing input normalization produces the intended identity; phrases,
   punctuation, internal path spaces, and extension display order survive.
5. At expiration the next request runs fresh; repeated hits and workbook-reference
   writes never extend source lifetime.
   The default expires exactly five days after generation. Two independent
   entries on one backend can use different lifetimes and expire independently;
   server-side per-operation overrides reach the backend correctly. Reject
   malformed/nonpositive lifetimes. Delayed publication uses remaining lifetime.
6. Fresh-search URLs preserve active options and bypass lookup. Successful
   refresh replaces the entry; failed refresh retains it for normal requests.
7. Successful empty results and scope/coverage warnings cache; exceptions and
   invalid requests do not.
8. Disabled, misconfigured, unavailable, malformed, incompatible, and oversized
   cache cases preserve usable fresh-search behavior.
9. JSON round-trips preserve scope state, sets, confirmation datetimes,
   ranks/nulls, Unicode, spaces, snippets, and nested data independence.
10. Every hit/miss/refresh gets current-request telemetry; telemetry outages
    remain nonfatal and export time remains outside search duration.
11. A missing workbook regenerates from cached data, retains the source
    generation time, and publishes a filename matching its download identifier.
    Export failure does not contaminate later cached responses.
    Cover unexpected file deletion while search data is still valid: regenerate
    only the workbook and preserve original search expiration.
12. Separate artifact references prevent an older workbook operation overwriting
    a newer result. Concurrent requests never expose partial JSON or files.
13. Unknown/repeated parameters, malformed refresh values, refresh without a
    valid search, mixed timestamp/search requests, and invalid download
    timestamps retain explicit validation.
14. HTML results have the application cache-control policy; legacy workbook
    downloads and retained files still work.
15. Maintenance retains Archive Search workbooks on days three through five,
    removes eligible workbooks after six days when invoked, and retains the
    existing three-day rule for other temporary files. Viewing/downloading a
    workbook never renews its age. Supported legacy workbook names receive the
    same separate policy. Invalid retention values defer workbook deletion;
    invalid lifetime pairings disable caching without breaking fresh searches.
16. Result expiration on day five triggers a fresh search even when an old
    workbook remains downloadable. Regenerating a missing file starts only its
    own file-retention period, not a new result-cache lifetime.

Retain the existing repeated-fresh-search test with caching disabled, and add
enabled-cache route tests rather than losing coverage of the fallback contract.
Run focused tests, Python compilation, Jinja parsing, and `git diff --check`.
Include screenshots of the fresh/cached result notices and fresh-search control
in the eventual implementation PR.

Before rollout, obtain a rough upper estimate of cache memory and workbook disk
use from existing traffic/resource information. Verify warm hits skip the
scope/search/metadata/snippet/coverage queries and Excel writing; remaining
telemetry writes are expected. Enable caching explicitly after the backend
contract and deployment storage checks pass; disabling it restores fresh browser
searches without DDL or cache deletion.

An offline sizing analysis used the supplied 435-row run export and 26 saved
search workbooks. It found a peak of 50 successful browser searches in five days.
Reconstructed JSON for the
largest local result set (3,000 files, 3,834 locations) is approximately 4.54 MiB,
supporting the initial 5 MiB entry cap. The reconstruction is approximate because
the workbooks omit some IDs and snippet highlight tags.

Workbook-calibrated rough allowances, including a factor of two for
overhead/headroom, range from about 256 to 441 MiB under those assumptions.
A 512 MiB service allowance remains a conservative planning value, subject to
available capacity beyond existing Redis/RQ usage; it is not expected memory
consumption or a directive to change a shared service limit.

Refine limits from actual serialized-size/cache logs after
implementation; a full profiling exercise is not required.

## Deferred API integration

API caching is a contained follow-up because the endpoint already uses
`ArchiveSearchRequest`, `ArchiveSearchRun`, and the shared JSON response builder.
Retain the reusable run policy and transport-neutral result envelope now.

When API caching is explicitly implemented:

- Authenticate and validate each request before cache lookup.
- Supply an explicit API enable/lifetime policy, with independent configuration
  if the API needs a different default from the browser.
- Add and validate a fresh-result request option and expose source generation,
  expiration, and hit/fresh state in a documented response field.
- Continue generating a new current-request `search_run_id` and
  `request_source="api"` telemetry lifecycle on hits.
- Keep the effective API result limit in identity. Browser and API requests can
  share data only when their effective identity/policy fields match; a smaller
  API limit does not automatically reuse a larger browser entry in this design.
- Use the existing API serializer; API calls do not generate Excel workbooks.
- Test authentication on hits, limit/policy isolation, response metadata,
  refresh, failure fallback, and telemetry.

No second cache backend or search implementation is needed. API request and
response contract changes remain deferred rather than silently enabling caching
for existing clients.

## Suggested implementation sequence

1. Add the reusable storage adapter, configuration validation, and an in-memory
   backend only for automated tests.
2. Add Archive Search identity/envelope codecs and explicit run cache policy.
3. Integrate the browser path, independent workbook references, and separate
   workbook retention in the existing maintenance task.
4. Add freshness notices, refresh validation/control, and focused verification.
5. Update runtime documentation and journal; perform measured opt-in rollout.

Later consumers should add their own namespace, codec, policy, and artifact
handling to this boundary. Dashboard charts would key on chart type, archivist
where applicable, resolved date range, rolling window, and rendering version;
they would check reuse before querying/processing chart data. That integration
can be specified separately when its freshness and file-delivery requirements
are chosen.
