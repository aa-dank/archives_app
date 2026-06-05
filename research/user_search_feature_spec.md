# V1 User-Facing Archive Search Feature Specification

Last updated: 2026-06-05

## Purpose

Add user-facing document-content search to the Flask archives app so project managers, archivists, and related staff can find archive files by filename/path and extracted document text, with clear scope, coverage, and export behavior.

This specification is implementation-oriented but does not prescribe final code structure. It should guide the first build pass and the validation work that follows.

## Source Context

Primary research memo:

- `/home/projects/archives_app/research/user_search_feats_research.md`

Reference and implementation context:

- `/home/projects/business_services_db/reference/ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`
- `/home/projects/business_services_db/reference/business_services_db_schema_20260520.md`
- `/home/projects/business_services_db/reference/historical/archive_search_chunked_fts_plan.md`
- `/home/projects/business_services_db/notebooks/fts_feat_research.ipynb`
- `/home/projects/business_services_db/output/*.csv`
- `/home/projects/archives_app/archives_application/archiver/routes.py`
- `/home/projects/archives_app/archives_application/archiver/forms.py`
- `/home/projects/archives_app/archives_application/models.py`
- `/home/projects/archives_app/archives_application/templates/file_search.html`
- `/home/projects/archives_app/archives_application/templates/file_search_results.html`

## V1 Goals

- Provide one practical archive search workflow for filename/path search and document-content keyword search.
- Use PostgreSQL-native full-text search over `file_content_fts_chunks` for document content.
- Support scoping by direct file server location, project number, and CAAN.
- Return HTML results centered on unique file hashes, not raw location rows.
- Show a primary/best location for each result and preserve access to additional locations.
- Explain what was searched and what was not content-searchable.
- Preserve Excel export for downstream filtering and handoff.
- Keep scoped searches suitable for synchronous Flask request/response use where latency testing supports it.
- Make result and export limits configurable.

## V1 Non-Goals

- No semantic/vector search.
- No hybrid keyword/vector search.
- No JSON export.
- No search logging or analytics capture.
- No durable saved searches.
- No asynchronous export job system unless synchronous export proves unusable during implementation testing.
- No current/final document inference model.
- No page-level preview, PDF viewer integration, or file content viewer.
- No new scraper extraction work as part of the web feature.
- No broad rework of the existing project/CAAN pages beyond links or query parameter handoff if useful.

## Users

Primary users:

- Project managers looking for known documents or topical documents inside a project/building/archive scope.
- Archivists looking for duplicate files, filing context, and archive-ready source paths.

Secondary users:

- Contracts staff.
- Planning staff.
- Other internal users who need archive discovery but may not understand the database structure.

## Product Model

The search product should preserve the database's natural identity model:

```text
file hash = canonical result identity
file locations = access/display metadata
content chunks = retrieval evidence
```

V1 HTML should therefore show one row per file hash. Multiple file locations should not create duplicate HTML result rows, because that overrepresents duplicated files and distorts ranking. Excel should expose the full location detail separately.

## Existing App Baseline

The current `/file_search` route:

- Uses `FileSearchForm`.
- Accepts `search_term`, `filename_only`, and optional `search_location`.
- Uses `FileLocationModel.filepath_search_query()`.
- Searches PostgreSQL FTS over `file_locations.filename` and optionally `file_locations.file_server_directories`.
- Returns location-level rows as a Pandas dataframe.
- Writes one Excel sheet to a temporary `.xlsx`.
- Displays up to 1,000 HTML rows.

V1 may either extend `/file_search` or introduce a clearer route such as `/archive_search`. If a new route is introduced, keep the old route working or redirect it until users have migrated.

## User Workflows

### Known File Or Report

1. User enters a known phrase, title fragment, or filename fragment such as `soil report`.
2. User optionally selects a project, CAAN, or location scope.
3. System searches filename/path and document content according to selected mode.
4. Results show filenames, primary paths, match type, snippets when content matched, and duplicate-location counts.
5. User copies a path or downloads Excel for further review.

### Project-Scoped Content Search

1. User enters a project number.
2. System resolves matching `projects` rows.
3. System uses non-null `projects.file_server_location` values as scope roots.
4. System reports any matching project rows with missing file server locations.
5. System searches files with at least one location under the resolved root paths.
6. Results prefer a primary location under the selected project scope.

### CAAN-Scoped Content Search

1. User enters or selects a CAAN.
2. System resolves the CAAN row.
3. System expands through `project_caans` to directly linked projects.
4. System uses linked projects' non-null `file_server_location` values as scope roots.
5. System reports linked projects excluded because they have no file server location.
6. If no linked projects or no usable roots exist, system explains that CAAN search depends on CAAN-project linkage and returns no scoped content results.

### Location-Scoped Search

1. User pastes a Windows File Explorer path or enters a database-relative Records path.
2. System normalizes the path to a database-relative `file_locations.file_server_directories` prefix.
3. System searches files with at least one location at or below that prefix.
4. Results prefer a primary location under that location scope.

### No Results Or Low Coverage

1. User submits a search and receives few or no hits.
2. System displays coverage counts for the selected scope.
3. System distinguishes no matching content from content unavailable, extraction failed, not chunked, unsupported/not attempted, and filename-only coverage.
4. User can broaden the scope, switch search modes, or download available result/coverage detail.

### Export For Review

1. User runs a search.
2. System displays up to the HTML file-level limit.
3. System generates an Excel workbook up to the file-level export limit.
4. Workbook includes file-level results, all locations for exported files, and coverage/search parameters.

## Search Modes

V1 should expose these modes:

| Mode | Meaning | Retrieval Source |
|---|---|---|
| Filename only | Match file names only | `file_locations.filename` FTS |
| Filename/path | Match file names and directory path text | `file_locations.filename` plus `file_locations.file_server_directories` FTS |
| Document text | Match extracted document text only | `file_content_fts_chunks.search_vector` |
| Filename/path + document text | Combined common search | File-level union of filename/path and content matches |

Recommended default:

- Use `Filename/path + document text` when a project, CAAN, or location scope is provided.
- For unscoped searches, either default to `Filename/path` or show an explicit warning before running unscoped document-content search. Empirical profiling showed one unscoped content query with about 45 seconds median latency.

Mode behavior:

- Content matches should show a snippet from the best matching chunk when practical.
- Filename/path matches should not pretend to have document snippets.
- Combined results should label match source as `content`, `filename/path`, or `both`.
- Combined ranking should keep content and filename/path scoring explainable. A simple v1 approach is to rank `both` above content-only and filename/path-only within comparable score bands, then sort by content rank or filename/path rank as available.

## Filters And Scope Semantics

### Scope Types

V1 should support one primary scope at a time:

- `all archives`
- `location prefix`
- `project`
- `CAAN`

If more than one scope input is supplied, the form should reject the submission or apply a documented precedence. Rejecting ambiguous scope is safer for v1.

### Scope Compilation

All scopes compile to zero or more database-relative Records directory prefixes. Paths in both `file_locations.file_server_directories` and `projects.file_server_location` are relative to the Records root and use forward slashes.

Path matching should use directory-boundary semantics:

```sql
fl.file_server_directories = :prefix
OR fl.file_server_directories LIKE :prefix || '/%'
```

Do not use `LIKE '%prefix%'` for scoped search, because it can match unrelated paths and prevent useful index planning.

### File-Level Scope

A file is in scope when at least one of its locations is under any compiled scope prefix.

For content search, the meaning should be:

```text
matching file with at least one location inside the selected scope
```

If a matching file also exists outside the selected scope, keep the file result and prefer an in-scope primary location.

### Location Scope

Accepted input:

- Windows user path rooted at configured `USER_ARCHIVES_LOCATION`, such as `N:\PPDO\Records\12xx   Hahn`.
- Database-relative Records path, such as `12xx   Hahn/1200/1200`, if implemented as an advanced or validated option.

Implementation should reuse existing path utilities where possible:

- `utils.FlaskAppUtils.user_path_to_app_path()`
- `utils.FileServerUtils.app_path_to_db_dir()`
- `utils.FileServerUtils.user_path_from_db_data()`
- database function `file_locations_user_path()` where SQL-side path formatting is better.

### Project Scope

Project scope resolves by project number. Because `projects.number` is not documented as unique in the schema, implementation should handle multiple rows with the same number.

Rules:

- Exact project number match is the v1 lookup behavior.
- Include all matching project rows with non-null `file_server_location`.
- Exclude project rows with null `file_server_location` and report them.
- If no matching project exists, show a validation-style message.
- If matching projects exist but none has a usable root, show a scoped-search coverage message explaining that no project folder location is recorded.
- If a recorded project root has no matching indexed files, report that the root produced zero indexed files.

Do not infer project scope from filename/path project-number matches in v1. That can be offered later as a fallback or separate mode.

### CAAN Scope

CAAN scope resolves through `caans` and `project_caans`.

Rules:

- Exact CAAN value is the v1 lookup behavior.
- Expand only to directly linked projects in `project_caans`.
- Include linked projects with non-null `file_server_location`.
- Exclude linked projects with null `file_server_location` and report them.
- If no linked projects exist, explain that CAAN search depends on project-CAAN linkage.
- If linked projects exist but no usable roots exist, show the linked-project count and the missing-root count.

Important preflight:

- Before enabling CAAN scope in production, verify `project_caans` population in the target database. The schema/reference docs describe about 12,023 project-CAAN rows, but the recent notebook output looked unexpectedly sparse for the analyzed CAAN expansion.

### Additional Filters

Recommended v1 filters:

- File extension, using `files.extension` with case-normalized matching.
- Search mode.
- Primary scope.
- Optional result limit controls only if restricted to safe configured maxima.

Recommended later filters:

- Date mentions from `file_date_mentions`.
- Searchable status as a user-facing facet.
- Open/closed project.
- Drawings flag.
- File size ranges.

## Query Semantics

### Text Query Parsing

Use PostgreSQL `websearch_to_tsquery` for user-entered search text.

Recommended configs:

- Document content: `websearch_to_tsquery('simple', :query_text)` to match the chunk table's generated `to_tsvector('simple', chunk_text)`.
- Filename/path: keep the current `english` configuration initially because the app and indexes already use `to_tsvector('english', filename)` and `to_tsvector('english', file_server_directories)`.

User-facing semantics:

- Plain words default to AND-like behavior under PostgreSQL web search parsing.
- Quoted phrases should be supported.
- `OR` should be supported.
- `-term` exclusion should be supported.
- The UI should explain this compactly, but search syntax documentation should not dominate the page.

### Content Search SQL Shape

Scoped content search should avoid joining chunks to all locations before ranking. Recommended logical shape:

```sql
WITH q AS (
    SELECT websearch_to_tsquery('simple', :query_text) AS query
),
scope_paths AS (
    SELECT unnest(:scope_prefixes) AS path_prefix
),
scoped_file_hashes AS (
    SELECT DISTINCT f.hash AS file_hash
    FROM file_locations fl
    JOIN files f ON f.id = fl.file_id
    JOIN scope_paths sp
      ON (
          fl.file_server_directories = sp.path_prefix
          OR fl.file_server_directories LIKE sp.path_prefix || '/%'
      )
),
latest_chunk_sets AS (
    SELECT file_hash, max(chunked_at) AS chunked_at
    FROM file_content_fts_chunks
    GROUP BY file_hash
),
matching_chunks AS (
    SELECT
        c.file_hash,
        c.id AS chunk_id,
        c.chunk_index,
        ts_rank_cd(c.search_vector, q.query) AS chunk_rank
    FROM file_content_fts_chunks c
    JOIN latest_chunk_sets lcs
      ON lcs.file_hash = c.file_hash
     AND lcs.chunked_at = c.chunked_at
    JOIN scoped_file_hashes sfh ON sfh.file_hash = c.file_hash
    CROSS JOIN q
    WHERE c.search_vector @@ q.query
    ORDER BY chunk_rank DESC
    LIMIT :chunk_candidate_limit
),
file_scores AS (
    SELECT
        file_hash,
        max(chunk_rank) AS best_rank,
        count(*) AS matching_chunks,
        (array_agg(chunk_id ORDER BY chunk_rank DESC))[1] AS best_chunk_id
    FROM matching_chunks
    GROUP BY file_hash
)
SELECT *
FROM file_scores
ORDER BY best_rank DESC, matching_chunks DESC
LIMIT :file_result_limit;
```

Notes:

- The exact physical plan may need adjustment after `EXPLAIN ANALYZE`.
- For broad scopes, letting the GIN FTS index find matching chunks first and then intersecting with scope may be faster.
- Product semantics must stay stable: scope applies before final file-level result limiting.
- Do not globally limit content matches before applying scope in a way that can drop valid scoped results below out-of-scope matches.
- Generate `ts_headline` snippets only after narrowing to final or near-final candidate chunks.

### Unscoped Content Search

Unscoped content search is an explicit latency risk. Recent profiling for `fixture` showed roughly:

- CAAN-scoped: about 1.1 seconds, but likely not representative because CAAN linkage was sparse.
- Project-scoped: about 1.5 seconds.
- Location-scoped: about 1.8 seconds.
- Unscoped: about 45 seconds.

V1 options, in recommended order:

1. Require a project, CAAN, or location scope for document-content search.
2. Allow unscoped document-content search only behind a warning and stricter limit.
3. Defer unscoped document-content search until an asynchronous path exists.

The first implementation can support unscoped filename/path search normally.

### Filename/Path Search SQL Shape

Current app behavior can be retained conceptually, but result grouping must change from location rows to file hashes.

Recommended shape:

- Search `file_locations` using existing GIN-backed FTS expressions.
- Join to `files`.
- Apply scope path constraints at the `file_locations` level.
- Group by `files.hash`.
- Score with best location-level rank.
- Keep the best matching location id for display.

For combined search:

- Produce a file-level result set for content.
- Produce a file-level result set for filename/path.
- Combine by `file_hash`.
- Mark match source as `content`, `filename/path`, or `both`.
- Use deterministic ordering for ties: match source, rank, matching chunk count, filename, hash.

## Result Grouping And Display Model

### HTML Result Identity

One row per `files.hash`.

Recommended columns:

- Rank or result number.
- Match source.
- Filename.
- Extension.
- Size.
- Primary location.
- Additional location count.
- Snippet, for content matches.
- Matching chunk count, for content matches.
- Text/searchable status.
- Failure status if applicable.
- File hash or shortened duplicate identifier.

### Primary Location Selection

Choose one primary location per file:

1. In-scope location with filename/path match, if the search mode included filename/path.
2. Any in-scope location.
3. Any location, only as a fallback for inconsistent data.

If multiple in-scope locations remain, prefer deterministic ordering:

- Shorter path first, then filename/path alphabetical.
- Or explicit rank if a filename/path match produced a best location score.

### Additional Locations

HTML should expose additional locations without turning them into primary rows. Acceptable v1 UI:

- Count plus expandable detail.
- Count plus "download Excel for all locations".
- Count plus modal/list if simple to implement.

Excel must include all locations for exported file-level results.

### Snippets

Content snippets should:

- Come from the best matching chunk.
- Use `ts_headline` where practical.
- Be escaped/sanitized before rendering.
- Avoid showing huge text blocks.
- Be omitted for filename/path-only matches.

## Coverage And Status Messaging

Coverage reporting is central to v1. Users must not interpret "no content hits" as "the archive does not contain this."

### Query-Level Coverage Summary

Show a summary above results:

- Query text.
- Search mode.
- Active scope label.
- Scope roots searched.
- Files in selected scope.
- Files with nonempty extracted text.
- Files with FTS chunks.
- Files with extraction failures.
- Files with empty or thin text.
- Files not attempted or not content-searchable.
- Filename/path-searchable files.
- HTML and Excel result limits.
- Whether limits were hit.

If project or CAAN scope omits records due to missing `file_server_location`, show:

- Number of projects found.
- Number with usable file server locations.
- Number missing file server locations.
- Number whose roots matched no indexed file locations.

### Result-Level Status Vocabulary

Use stable status values internally and friendly labels in the UI/export.

| Status | Meaning |
|---|---|
| `content_searchable` | Nonempty text exists and current FTS chunks exist |
| `text_extracted_not_chunked` | Nonempty extracted text exists but no FTS chunks exist |
| `empty_or_thin_text` | Content row exists but text is empty or below threshold |
| `extraction_failed` | `file_content_failures` row exists |
| `not_attempted` | No content row and no failure row |
| `unsupported_or_low_value_format` | Extension/category is normally not useful for content extraction |
| `filename_only` | Not content-searchable but searchable by filename/path |
| `image_ocr_thin` | Image-derived text exists but is likely too sparse for confident content search |
| `image_ocr_searchable` | Image-derived OCR appears substantial enough for content search |

Recommended threshold:

- Treat text under 50 characters as definitely thin.
- Consider reporting under 200 characters as thin/low-context in coverage, but do not exclude it from search in v1.

The status can be computed at query time for v1. If coverage queries are expensive, materialize a status table or materialized view later.

## Excel Export Behavior

V1 should produce an `.xlsx` workbook with multiple sheets.

### Sheet: Results

One row per exported file hash.

Recommended columns:

- `result_rank`
- `match_source`
- `file_hash`
- `filename`
- `extension`
- `size_bytes`
- `size_display`
- `primary_location`
- `additional_location_count`
- `best_rank`
- `matching_chunks`
- `snippet`
- `text_status`
- `failure_stage`
- `failure_summary`

### Sheet: Locations

One row per location for each exported file hash.

Recommended columns:

- `file_hash`
- `location_rank`
- `in_scope`
- `location_matched_query`
- `filename`
- `file_server_directories`
- `user_path`
- `existence_confirmed`
- `hash_confirmed`

### Sheet: Coverage

One row per metric or a compact table of search parameters and counts.

Recommended fields:

- Query text.
- Search mode.
- Scope type.
- Scope display value.
- Scope roots.
- Project/CAAN resolution counts.
- Files in scope.
- Content-searchable files.
- Filename/path-searchable files.
- Extraction failures.
- Empty/thin text counts.
- Not attempted counts.
- Unsupported/low-value format counts.
- HTML limit.
- Excel file-level limit.
- Whether result limits were hit.
- Generated timestamp.

### Limits

Recommended defaults:

- HTML: 300 file-level results.
- Excel: 3,000 file-level results.

Both should be configurable constants. The Excel limit applies to file-level results; the `Locations` sheet should include all locations for those exported files, even if that produces more than 3,000 location rows.

## Performance Constraints And Open Latency Risks

### Constraints

- Scoped searches should target low-single-digit second latency for typical project/location scopes.
- HTML result rendering should avoid large Pandas dataframes where a streamed or bounded query result would be safer.
- Excel generation should stay bounded by file-level export limits.
- Snippet generation must happen after candidate narrowing.
- Scope resolution should not perform expensive full-table prefix scans repeatedly when avoidable.
- Search should not join content chunks to all location rows before file-level ranking.

### Known Risks

- Unscoped content search may be too slow for synchronous UX.
- Large location scopes such as `49xx   Long Marine Lab` may be slow and have low content-search coverage.
- Scope membership summaries can be expensive if recomputed from raw `file_locations` on every request.
- CAAN scope depends on `project_caans` data quality.
- Project scope depends on `projects.file_server_location`, which is missing for many project rows.
- Excel workbooks can become memory-heavy in request context.
- `db_query_to_df()` may be too blunt for the content search path.

### Recommended Mitigations

- Start with strict result limits and candidate limits.
- Require or strongly encourage scope for document-content search.
- Add `EXPLAIN ANALYZE` checks for representative terms and scopes before considering the feature done.
- Consider a cached/materialized scope map if project/location/CAAN prefix resolution is repeatedly expensive.
- Treat async export as a follow-up only if bounded synchronous export fails validation.

## Decisions Already Made

- Primary users are PMs and archivists.
- Secondary users may include contracts and planning staff.
- V1 uses PostgreSQL-native search over `file_content_fts_chunks`.
- Semantic/vector/hybrid search is out of v1.
- JSON export is out of v1.
- Search logging is out of v1.
- HTML should show one row per file hash, with primary/best scoped location and access to additional locations.
- Excel should include file-level results and separate location detail.
- Suggested limits are 300 HTML file-level results and 3,000 Excel file-level results, configurable.
- Project scope requires `projects.file_server_location`; missing locations should be explained.
- CAAN scope should use projects linked in `project_caans`; CAAN linkage needs verification.
- Scoped content search should be encouraged.
- Unscoped content search is likely too slow for normal synchronous UX until further tuning proves otherwise.
- Coverage/status reporting is a core user-facing feature.

## Remaining Open Questions

- Should v1 require a scope for document-content search, or allow unscoped content search with warnings and stricter limits?
- Should the default search mode be combined search for all scoped searches?
- What are the top 10-20 real user searches to test before rollout?
- Which result columns are essential for PMs versus archivists in the first HTML table?
- Should same-number project records be merged automatically in the UI or shown in a project resolution summary?
- Should CAAN scope ship in v1 if production `project_caans` linkage is sparse?
- Should `empty_or_thin_text` use a 50-character or 200-character reporting threshold?
- Is a materialized scope/status table needed before launch?
- How should combined filename/content ranking be tuned after first user evaluation?
- Should current/final document hints be deferred entirely, or approximated later with path/filename heuristics?

## Recommended Implementation Phases

### Phase 0: Preflight And Query Validation

- Verify `project_caans` population in the target database.
- Run latency tests for several terms and scope sizes.
- Validate query plans for content-only, filename/path-only, and combined scoped search.
- Decide whether unscoped content search is enabled, warned, or blocked.
- Confirm configurable limits and timeout behavior.

### Phase 1: Search Service Contract

- Build a bounded search service/helper that returns file-level result objects.
- Implement scope resolution for location, project, and CAAN.
- Implement content search over `file_content_fts_chunks`.
- Implement filename/path search with file-hash grouping.
- Implement combined result merging.
- Compute primary location and additional location counts.
- Compute coverage/status summaries.

### Phase 2: Forms And Templates

- Extend `FileSearchForm` or add a new archive search form.
- Add search mode and scope fields.
- Update results template for file-level results, snippets, status, and coverage.
- Preserve duplicate-submit prevention.
- Add clear warnings for missing project roots, weak CAAN linkage, broad scopes, and limit hits.

### Phase 3: Excel Export

- Generate `Results`, `Locations`, and `Coverage` sheets.
- Enforce file-level export limit.
- Include all locations for exported files.
- Keep the existing timestamp/download pattern if adequate.
- Validate workbook size and generation time.

### Phase 4: Hardening And Rollout

- Add tests for scope resolution, query semantics, grouping, coverage, and exports.
- Test with known user scenarios.
- Review search quality with PMs/archivists.
- Adjust ranking/display only based on observed issues.
- Document known coverage limitations in the UI.

## Testing And Validation Plan

### Unit Tests

- Path normalization from Windows user paths to database-relative prefixes.
- Directory-boundary path matching behavior.
- Project scope resolution with one project, duplicate project numbers, null roots, and unmatched roots.
- CAAN scope resolution with linked projects, no linked projects, null roots, and sparse linkage.
- Search mode parsing and validation.
- Status classification for content searchable, thin text, extraction failure, not attempted, and unsupported extensions.
- Primary location selection for single-location, multi-location, in-scope, and out-of-scope cases.
- Excel sheet construction from a small deterministic result set.

### Integration Tests

- Filename-only search returns grouped file hashes.
- Filename/path search can match directory text.
- Content search returns one row per file hash and includes snippets.
- Combined search marks `both` when a file matches content and filename/path.
- Scoped content search does not return files with no in-scope locations.
- Project missing-root messaging appears.
- CAAN no-linkage or sparse-linkage messaging appears.
- HTML limit and Excel limit behavior are correct.

### Database Validation

- Run `EXPLAIN ANALYZE` for representative searches:
  - `soil`
  - `lighting`
  - `fixture`
  - `submittal`
  - at least one known report title
- Test scopes:
  - small project
  - large project
  - large top-level location such as `49xx   Long Marine Lab`
  - CAAN with many linked projects, if available
  - unscoped content search, if enabled
- Confirm candidate limits do not hide obvious expected results in scoped searches.
- Confirm latest chunk-set logic does not duplicate stale chunks.

### User Acceptance Checks

- PM can find a known document in a project folder.
- PM can search a topical term in a project/building scope and understand where matches came from.
- Archivist can identify duplicate-location files from the HTML row and Excel locations sheet.
- User can understand why a no-results search may be caused by content coverage gaps.
- Excel output is usable without reading implementation documentation.

### Regression Checks

- Existing filename search workflow still works or redirects cleanly.
- Existing Excel download timestamp flow still works if reused.
- CAAN info pages remain unchanged unless intentionally linked to search.
- Project location API behavior remains unchanged.

## Launch Criteria

- Scoped content search works for project and location scopes with acceptable latency on representative terms.
- CAAN scope is either verified and enabled, or clearly disabled/explained until linkage is corrected.
- HTML results are file-hash grouped.
- Excel export includes file-level and location-level sheets.
- Coverage/status summary appears on every result page, including no-results pages.
- Query and export limits are configurable.
- Tests cover scope resolution, result grouping, status messaging, and export structure.
- Known risks and limitations are documented in the UI or release notes.
