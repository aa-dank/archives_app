# Restricted File Retention Specification

Date: 2026-11-19
Status: Proposed implementation; runtime behavior has not changed.

## Purpose

Preserve a file's active content restriction when its last indexed location is
removed. If identical content returns to the file server, indexing must reuse
the retained canonical file record and its restriction.

Several dozen to a few hundred restricted files are expected. Retaining these
small identity records does not require a separate retention table or an
additional stored availability flag. Availability is derived from the existence
of `file_locations` rows.

Implementation in this effort is confined to `archives_app`, including its own
metadata-scraping and reconciliation tasks. Changes to `archives_scraper`,
`desktop_archives_scraper`, and external restriction-management workflows are
separate efforts. Their known issues are recorded below as an aside, not as
acceptance criteria or prerequisites for completing this app change.

This specification does not implement changes or claim that the restriction
flag is already enforced throughout the system. Database readiness and index
work are handled separately and are outside this specification.

## Delivery phases and terminology

Use **text suppression**, **Suppress extracted text**, and **Remove text
suppression** in the UI. Keep the existing `content_restricted_at` and
`restriction_reason` database names. Suppression hides extracted file text and
match snippets; it does not remove metadata, content-search matching, or access
to the physical file.

**Phase 1: enforce existing suppression flags and retain restricted identities.**
Prioritize suppression of extracted text and snippets in pages, API responses,
and Excel exports. Implement the retention and location-related behavior below
without waiting for stakeholder agreement on a management form. The display
changes can ship before the retention changes if that gets suppression into
place sooner; record the delivered subset explicitly. Existing flags may be
maintained through SQL or another existing workflow during this phase.

Phase 1 includes clearing old cached output at rollout and a documented way to
clear it after SQL changes. That can be an operator maintenance procedure; it
does not require the Phase 2 form or suppression-writing endpoint.

**Phase 2: manage suppression and clear cached output through the app.** After
stakeholder agreement, add one form and endpoint supporting apply suppression,
remove suppression, and cache-only purge. Applying or removing suppression
clears all Archive Search cached results and Excel exports. Cache-only purge
does the same cleanup without changing file flags.

Use this global purge policy rather than per-file artifact tracking, selective
workbook repair, or new cache-generation database records. Losing a few minutes
of cached work is acceptable and makes the effect easy to explain: cached
search results and exports are discarded, and subsequent searches rebuild them.

## Identity and lifecycle rules

`files.hash` identifies byte content. A `files` row records known content;
`file_locations` records where copies are indexed. An indexed location is not a
guarantee that a physical copy still exists before the next confirmation.

`content_restricted_at IS NOT NULL` means an active content restriction. Keep
that timestamp, `restriction_reason`, canonical ID, hash, size, and extension
when all locations disappear. Reappearance must not reset the timestamp or
reason. A renamed identical file has the same identity; an edited or
re-exported document with different bytes does not inherit the restriction
automatically.

| Indexed locations remain | Active restriction | Required behavior |
| --- | --- | --- |
| Yes | No | Retain the file and continue ordinary processing. |
| Yes | Yes | Retain the file and locations; allow search matches and metadata display, but hide extracted file text and match snippets. |
| No | Yes | Retain the identity and restriction; remove derived content and processing failures. |
| No | No | Apply ordinary canonical-file cleanup, including unlinking archive events before deleting the file. |

The last row describes cleanup when a file is affected by app reconciliation.
It does not authorize a bulk purge of existing historical unrestricted rows
without a separate inventory and review.

Preserve the daily metadata scraper's existing historical-row retention when
it changes a location's content association. This proposal changes cleanup
decisions where cleanup already occurs; it does not add deletion of that
scraper's historical records.

Restrictions survive indefinitely while active. Clearing a restriction is an
explicit change by its owning workflow; it is not triggered by deletion,
re-upload, rename, missing content, or a failed extraction. This is retention of
an active restriction, not a permanent history of restrictions after clearance.

## Findings in the current code

The following findings were checked against the local repositories during the
2026-11-18 review and rechecked for this specification:

- The restriction columns exist in the Archives App model, but the app's search
  and file-information reads do not currently enforce them.
- Indexing in `add_file_to_db_task`, `_persist_scraped_file`, and the move fallback
  already looks up an existing hash and can reuse a locationless record without
  overwriting its restriction fields.
- Canonical-file deletion is duplicated across confirmation, replacement, and
  server-edit reconciliation.
- The web Archived or Not page treats a hash match without locations as an
  exception. Its API already returns 404 for that case; the Inbox page tolerates
  an empty location list.
- Unscoped content search and coverage begin with every `files` row. They can
  include absent files in counts and, if chunks remain, in content results.
- File-information HTML and JSON already support zero locations. A retained
  hash therefore remains addressable after its last location is removed.
- The daily metadata scraper already retains historical file records when it
  changes an existing location's content association. A universal invariant that
  every file has a location is already unsafe.

Review verification used an isolated SQLite database to confirm hash matching,
restriction-preserving reattachment, and the current deletion of a restricted
file during last-location confirmation. Four focused existing tests passed.
These checks did not exercise a deployed PostgreSQL database or SMB share.

## Shared file-location removal and canonical cleanup

Consolidate database file-location deletion in an instance method on
`FileLocationModel` in `archives_application/models.py`. Use these model methods:

```python
location.remove_from_db()       # FileLocationModel
file.cleanup_if_unlocated()     # FileModel
```

`FileLocationModel.remove_from_db()` removes the `file_locations` row and applies the canonical-file
lifecycle policy as one operation. Callers must not each implement their own
remaining-location check, dependent deletion, restriction guard, or archive-event
unlinking. Its responsibilities are:

1. Resolve the location and its canonical file, using the transaction/locking
   protocol below. An already-removed location is a harmless no-op.
2. Delete the location row and flush the removal before checking for remaining
   locations. Do not commit.
3. Leave the canonical file and its dependents intact if locations remain.
4. When no locations remain, remove derived data as described below.
5. Retain a restricted file and preserve its archive-event links.
6. For an unrestricted file, clear associated `archived_files.file_id` links and
   delete the canonical row.
7. Return explicit outcomes such as `location_removed`, `file_removed`, and
   `restricted_file_retained` so task counters reflect what actually happened.

Use this operation across general and scoped confirmation, queued indexing's
displaced-location replacement, single-file and directory deletion, and move
reconciliation, including removal of missing locations during a move. Audit all
app location-deletion paths for adoption. Routes delegate through their existing
helpers/tasks; the unused broken route-module removal helper must be removed or
replaced with delegation rather than remain a second implementation.

Put the last-location decision on `FileModel.cleanup_if_unlocated()`, which
`FileLocationModel.remove_from_db()` calls on the associated file after deleting
the location. Phase 2 suppression removal calls this same file method after
clearing both restriction fields, without inventing or deleting a location row.
It must independently verify that no locations remain. Both methods use the
existing `db.session` in `models.py`, with no dependency on route handlers or
task app instances and no app creation. A separate lifecycle module is not
needed.

These methods manage database records only. Physical filesystem mutations
continue through `ServerEdit`. Preserve the daily metadata scraper's existing
historical-row policy for content reassignment, which does not delete a location
row and is not a new cleanup trigger in this effort.

Location removal, dependent cleanup, and any archive-event unlinking or file
deletion must commit together. Neither method commits independently of its
caller. Callers provide the app context and own commits and rollback. Task
progress updates must not commit partially completed cleanup.

The deletion decision must not rely on a stale restriction or location check.
Use a consistent transaction/locking protocol across the app's indexing and
cleanup callers, and recheck the restriction before deleting a canonical row.
Validate it in PostgreSQL with concurrent restriction updates and app location
attachment. A conditional delete alone does not replace coordination with
concurrent location insertion. Changes to external writers and global
publication guarantees are outside this effort.

### Derived data

Retaining the parent prevents its deletion cascades from running. Explicitly
remove these records when the last location is removed, including for a retained
restricted file:

- `file_contents`, including source text and embeddings;
- `file_content_fts_chunks`;
- `file_date_mentions`;
- `file_content_failures`, including stored failure metadata.

Use verified database cascades or delete in foreign-key-safe order. Audit the
existing `_delete_file_hash_dependents` helper and deployed constraints rather
than assuming deletion of `file_contents` handles every environment. Delete only
the affected hash's dependents.

While locations remain, a restriction controls display of extracted file text
and search-match snippets. It does not require purging the stored content or
excluding the file from content matching. Apply display suppression even when
derived rows exist or are recreated by another service. The last-location
cleanup above is separate from this display policy.

### Archive-event history and restriction clearance

Retain `archived_files.file_id` when the canonical restricted row survives.
Archive-event destination paths remain historical and must not be interpreted as
current locations. Existing archiving totals and timekeeper reports must remain
unchanged.

Phase 1 adds no restriction-management UI and does not modify external clearance
workflows. If a restriction has been cleared externally, fresh app reads treat
it as unrestricted; cached output follows the purge procedure below. A future
cleanup of that row uses the ordinary policy. Phase 2 removal through the app
also invokes ordinary cleanup immediately if no locations remain.

## Indexing and physical filesystem operations

Preserve hash-based reuse in all indexing paths, including uploads, daily
metadata scraping, location-specific child tasks, and move fallbacks. A returning
restricted file gains a location on the same canonical ID. It remains restricted
even if its filename, directory, or filename extension changes.

Do not require an existing location when looking up identity for indexing or
restriction inheritance. Size/extension prechecks used by duplicate-check pages
are an optimization, not the authoritative restriction lookup.

Preserve existing filename/extension exclusions, empty-file eligibility rules,
oversized daily-scrape behavior, quantity limits, and path conversions. Retaining
a database record does not prevent authorized deletion of the physical file.
Filesystem edits continue through `ServerEdit` and queued reconciliation.

## User-visible and API behavior

### Archived or Not and Inbox

Treat "known hash with zero indexed locations" as an ordinary result:

- The web Archived or Not page reports that no indexed copies are available,
  instead of raising an exception or presenting an empty successful table.
- `/api/archived_or_not` preserves its existing 404 behavior for no locations.
- Inbox duplicate-location notices remain conditional on actual indexed
  locations; a retained hash alone is not evidence that the item is archived.

Keep identity lookup and current-location lookup separate so this behavior does
not undermine restriction inheritance.

### File information

Keep hash-based access to retained metadata. For an existing retained row,
`/file_info/<file_hash>` and authenticated `/api/file_info?file_hash=...` return
200 with zero locations. A former `user_path` returns 404 after its location row
is removed. Unknown hashes continue to return 404.

Apply the same text-display policy to HTML and JSON, and document the additive
API change in the API references:

- Include `content_restricted_at` as an ISO-8601 timestamp or null.
- For restricted files, omit source text unless `include_text=true`, in which
  case return `source_text: null`. Do not retrieve the restricted source text
  merely to discard it during rendering or serialization.
- Preserve the existing metadata display and serialization, including file and
  location metadata, detected dates, text lengths, indexing timestamps, and
  text/indexing status. A display restriction does not make indexed content
  unsearchable or change its extraction status.
- Keep `location_count` and `locations` as the availability indicators; do not
  add a separately persisted availability state.

The HTML page explains both the restriction and the absence of locations, when
applicable. In the extracted-text area, explain that text is not displayed for
this file rather than claiming that no text was extracted. Suppress restricted
extracted text regardless of authentication or whether derived rows still exist.
Do not publicly display `restriction_reason` or add it to ordinary API output.

Metadata/path access otherwise follows existing authentication and authorization
rules. A content restriction does not introduce a new physical-file access or
download policy in this specification.

### Search and coverage

Require at least one indexed location for ordinary archive search membership and
coverage in every scope. For unscoped queries, use an `EXISTS` location predicate
or equivalent that preserves one row per hash. Filter before candidate limits
and ranking; dropping absent results after limiting can hide valid matches.

Restricted files with locations remain eligible for filename/path, content, and
combined searches under the existing matching, ranking, scope, and limit rules.
Do not add a restriction predicate that excludes them from content candidates.
Show their metadata and match information as usual, but omit the match snippet.
Skip restricted chunk IDs when fetching snippets, and use an empty string for
the existing JSON `snippet` field.

Excel exports must exclude restricted extracted file text and search-match
snippets from the workbook itself, not merely hide their columns in the web
page. Keep the matching file's row and permitted metadata; leave a shared
snippet/text column blank for that row if unrestricted rows use the column.
This applies to newly generated exports and reused/cached workbooks.

This deliberately permits inference that a restricted file contains a searched
term, including PII, from its appearance in results or match/ranking information.
That inference is an accepted limitation of this display policy. This effort
hides extracted text and snippets; it does not conceal search-match existence.

Coverage counts describe files with indexed locations. No new
restriction-specific coverage category is required. Keep counting restricted
files with searchable chunks as content-searchable under the existing criteria;
restriction alone must not classify a file as "not attempted" or filename-only.
Update HTML, JSON, workbook, telemetry, and cache handling consistently for
location-based membership and the text/snippet display policy.

### Cached results and exports: global purge

Search-result caches and generated Excel files can contain snippets from before
a suppression change. The chosen invalidation operation discards **all Archive
Search cached results and generated Excel exports**, regardless of which file
or search they concern. It does not inspect artifacts for PII or attempt to
identify only affected searches.

The operation must:

- Remove Archive Search result-cache entries and workbook references in the
  configured environment/dataset namespace. Do not use `FLUSHDB`/`FLUSHALL` on
  the shared key-value store or remove unrelated cache entries or queued jobs.
- Remove all recognized Archive Search Excel files from the server's temporary
  storage, including workbooks no longer referenced by a live cache entry.
  Removing only references leaves existing download URLs usable. The current
  workbooks are under `static/temp_files`, so physical removal also matters for
  direct static URLs. Do not remove unrelated temporary previews or uploads.
- Account for in-progress searches/exports so an old operation cannot republish
  pre-purge output after completion is reported. Use a bounded publication/purge
  coordination mechanism; an operator procedure may pause/drain generation
  during Phase 1. No per-file artifact registry is required.
- Make old export links return a clear no-longer-available result directing users
  to rerun the search. New searches and exports use current suppression flags.
- Be repeatable and report failures separately for cache and filesystem cleanup;
  a partial purge must not be presented as complete.

Phase 1 must remove old cached results and exports before suppression is
considered deployed. Document the same purge procedure for any later SQL or
external flag changes. Disabling result-cache lookup alone is insufficient:
old Excel files can still be downloaded. Phase 2 exposes this existing policy
as a user action and invokes it after flag updates.

This policy intentionally depends on a purge after out-of-app changes; it does
not add automatic SQL-change detection or require per-download file-hash
revalidation, private workbook storage, or selective workbook regeneration.
Copies already delivered to users are outside the server cache purge.

## Phase 2: suppression management form and endpoint

This section is deferred until stakeholder agreement. It must not delay Phase 1.
Proposed route: `GET /content_suppression` displays the form and
`POST /content_suppression` performs one explicit action. Final naming and
authorized roles are Phase 2 stakeholder decisions.

Use the same form and endpoint for these actions:

| Action | Database effect | Cache/export effect |
| --- | --- | --- |
| Suppress extracted text | Set the selected file's active timestamp and optional reason. | Purge all Archive Search cached results and Excel exports. |
| Remove text suppression | Clear the selected file's timestamp and reason; clean up the canonical row if no locations remain. | Purge all Archive Search cached results and Excel exports. |
| Clear cached search results and Excel exports | No flag or canonical-file changes. | Perform the same global purge, including after a SQL change. |

Choose an explicit action rather than toggling based on the displayed state.
GET must never mutate data. POST requires authentication, the agreed role
authorization, CSRF protection, and ordinary form validation.

For apply/remove, accept exactly one canonical hash or indexed user path. Resolve
a path to its hash with existing normalization and ambiguity handling. Hash
selection also works for retained files with no locations. Show the identified
file's metadata, current suppression state, and location count so the user can
review what will change; explain that the action affects all copies of that
hash. Cache-only purge needs no file selector.

Preserve the original timestamp when suppression is already active. Repeating
apply or remove should not fail solely because the requested state is already
present, and should still allow the purge to run. A reason records only a
category/rationale without sensitive values. Removing suppression clears the
active reason along with the timestamp. Do not delete stored extracted content
merely to apply suppression while locations remain: matching stays available.

After removal, text/snippets may appear again if derived content still exists.
Removal does not recreate content previously removed by last-location cleanup
or trigger a new extraction job. Call `FileModel.cleanup_if_unlocated()` in the
same database transaction after clearing suppression, including archive-event
unlinking if the now-unrestricted canonical row is deleted. Explain that outcome
in the form result.

Keep route handlers thin and use shared application helpers. Record the actor,
time, action, hash where applicable, and database/purge outcomes using existing
operational logging. Persist the requested flag change before purging; if the
purge fails, preserve the new flag state and report the incomplete cleanup
clearly, with the cache-only action available for retry. Do not report the whole
operation as successful until the purge finishes.

Use queued cleanup for substantial filesystem work rather than performing a
large deletion in the route. If cleanup is queued, show a pending outcome and
the existing worker-task status; pending is not a completed purge.

The UI must plainly state the broad effect: all cached Archive Search results
and exports will be cleared, other users' old export links will stop working,
and later searches will rebuild their results. No cache is precious enough to
justify per-file purge bookkeeping for this effort.

## Separate follow-up: content scrapers

Both external scraper candidate queries can select locationless files and
record a "no path available" failure; default failure exclusion may then prevent
processing after the file returns. A separate effort should address location
eligibility in normal/retry/targeted modes, transient skip handling, and the
desired restriction policy for extraction. Review any resulting
failure-marker cleanup in that effort. No scraper code, model changes,
publication protocol, tests, or deployment work is included here.

The app must enforce restrictions on its own reads even if these services have
not yet been updated. It does not promise to prevent their processing or writes.

## Implementation map

Paths below are relative to `archives_application/` in `archives_app`.

| Repository/component | Required work |
| --- | --- |
| Archives App: `models.py` | Add `FileLocationModel.remove_from_db()` and `FileModel.cleanup_if_unlocated()`; centralize restriction retention, dependent cleanup, archive-event unlinking, and outcomes without committing. |
| Archives App: `archiver/archiver_tasks.py` | Call the location removal method in `_confirm_file_location` and displaced-location replacement in `add_file_to_db_task`; preserve hash reuse and historical-row policy in `_persist_scraped_file`. |
| Archives App: `archiver/server_edit.py` | Call the location removal method in `record_file_server_file_removal`, both branches of `add_deletion_to_db_task`, and move reconciliation's direct location-deletion paths. |
| Archives App: `archiver/routes.py` | Normal empty-location duplicate result; audit/remove the unused broken `remove_file_location` helper; preserve file-info selector semantics. |
| Archives App: `archiver/file_info.py`, templates | Preserve metadata/status output, suppress restricted source text, and explain restrictions and zero locations. |
| Archives App: `archiver/archive_search.py` | Location-based membership; preserve restricted content matches and metadata while suppressing snippets in results and exports. |
| Archives App: `archiver/archive_search_cache.py` and export storage | Phase 1 global purge procedure and safe publication boundaries; update codec if needed. Phase 2 reusable bulk purge action. |
| Archives App: routes, forms, template, shared helpers | Phase 2 only: one suppression-management endpoint/form with apply, remove, and cache-only purge actions; removal uses `FileModel.cleanup_if_unlocated()`. |

Inspect all canonical-file deletions and bulk maintenance SQL within this app
before implementation. External jobs are outside this effort; app-level
retention does not guarantee that another writer cannot delete a retained row.

## Phase 1 acceptance and verification

Use isolated databases/filesystems for automated tests. Cover at least:

1. Removing the last restricted location retains ID, hash, timestamp, reason,
   and archive-event links, while deleting derived rows.
2. Removing the last unrestricted location preserves ordinary deletion and
   archive-event unlinking; removing one of several locations retains the file.
3. Re-uploading identical bytes under a different name/path reattaches to the
   same restricted ID. Different bytes do not acquire that restriction.
4. Confirmation, scoped confirmation, queued indexing replacement, single-file
   deletion, directory deletion, and move reconciliation use the model removal
   method and follow the same policy, including harmless repeated removal of
   an already-deleted location.
5. Injected dependent-cleanup or final-delete failures roll back the complete
   operation; counters reflect committed outcomes. A retained row is not counted
   as a removed file.
6. Duplicate checks handle zero locations normally; hash/path file-info selectors
   return the specified results and restriction indicator without changing the
   existing text/indexing status semantics.
7. Absent files are excluded from search and coverage before ranking/limits;
   duplicate locations do not multiply file counts. Restricted files with
   locations remain eligible for filename/path, content, and combined results,
   including the normal content-searchable coverage counts.
8. Restricted extracted text and match snippets are absent from HTML, JSON,
   cached responses, and workbooks even when stored content exists. Metadata,
   detected dates, indexing status, match information, and result ranking remain
   available under existing rules. Unrestricted files retain their text/snippets.
   Inspect generated Excel cell values to verify restricted text/snippets are
   absent while matching rows and metadata remain. A workbook created before a
   suppression change must be removed by the global purge and cannot be reused
   or downloaded afterward.
9. PostgreSQL concurrency tests cover restriction updates and app location
   attachment during cleanup. SQLite tests alone cannot establish these
   guarantees.
10. An externally applied or cleared restriction is reflected in fresh app reads
    and in rebuilt results/exports after the documented purge; app cleanup uses
    the current flag value.
11. Existing exclusions, quantity checks, task lifecycle/progress, archive-history
    totals, and existing API authentication remain intact.
12. Global purge clears this environment's complete Archive Search cache and
    export set, including unreferenced files, while preserving unrelated keys,
    jobs, and temporary files. Exercise old download/static URLs, incomplete
    cleanup, repeated purge, and concurrent generation across the purge boundary.

Run relevant existing confirmation, scrape, scoped-scrape, duplicate-check,
file-info, and search/cache/export tests within `archives_app`. Add focused cases
where current coverage is missing. Capture screenshots for changed templates
and manually verify one restricted remove/re-upload cycle against a disposable
test share.

### Phase 2 acceptance and verification

Add focused coverage for apply/remove/cache-only actions on the same endpoint;
authentication/roles/CSRF and selector validation; repeated actions; timestamp
and reason semantics; locationless cleanup on removal; independent database and
purge failure reporting; and clear feedback about invalidated export links.
Verify that SQL flag changes followed by cache-only purge produce the same
display behavior as app flag changes. Stakeholder decisions and this coverage
are not Phase 1 release gates.

## Rollout and operational follow-up

1. Inventory app writers/deleters of canonical files. Inventory restricted rows,
   their locations, derived content, and relevant failures without exposing
   sensitive text or reasons in reports.
2. Validate the app's new location-membership queries against its deployment
   database; database readiness and index changes are managed elsewhere.
3. Deploy compatible app web and worker versions. Pause or drain incompatible
   app jobs where needed; deploying external scrapers is a separate effort.
4. Purge all pre-change Archive Search cached results and Excel exports using
   the documented operator procedure, coordinating in-flight publishers. Deploy
   suppression without waiting for the Phase 2 form. Do not purge historical
   unrestricted orphans or external scraper failures as an incidental step.
5. Confirm a retained hash has zero locations, its restriction survives a
   re-upload, app reads suppress restricted extracted text and snippets while
   preserving matches/metadata, and task counters and location-based archive
   totals remain understandable.
6. Update API/operations references and append a development-journal entry with
   implemented app behavior, verification, and follow-up.
7. After stakeholder agreement, implement Phase 2 as a separate change set and
   journal its management actions, role decisions, and global purge behavior.

Rolling back to code that unconditionally deletes last-location files would
again lose retained restrictions. Preserve/export the small restriction identity
set through the approved backup process before such a rollback; do not assume
an application rollback preserves the new policy.
