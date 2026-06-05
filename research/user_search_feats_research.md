# User Search Features Research Memo

Last updated: 2026-06-03

## Purpose

This memo curates the design context for adding common document search functionality to the Flask archives application. It is meant to guide a later feature specification and implementation plan, not to lock in UI copy or final route/API details.

The near-term feature target is a user-facing search workflow for common archive discovery tasks:

- search file names and paths
- search extracted document text
- scope search by file server location, project, or CAAN
- show users what was searched and what was not searchable
- provide HTML results plus Excel exports for downstream work

## Sources Reviewed

Local application and reference files:

- `/home/projects/archives_app/archives_application/archiver/routes.py`
- `/home/projects/archives_app/archives_application/archiver/forms.py`
- `/home/projects/archives_app/archives_application/models.py`
- `/home/projects/archives_app/archives_application/templates/file_search.html`
- `/home/projects/archives_app/archives_application/templates/file_search_results.html`
- `/home/projects/archives_app/archives_application/project_tools/routes.py`
- `/home/projects/archives_app/archives_application/templates/caan_info.html`
- `/home/projects/business_services_db/AGENTS.md`
- `/home/projects/business_services_db/reference/ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`
- `/home/projects/business_services_db/reference/business_services_db_schema_20260520.md`
- `/home/projects/business_services_db/reference/historical/archive_search_chunked_fts_plan.md`
- `/home/projects/business_services_db/misc/archive_app_search_research.md`
- `/home/projects/archives_scraper/AGENTS.md`
- `/home/projects/archives_scraper/development/large_text_content_analysis_20260414.md`
- `/home/projects/archives_scraper/development/scraper_improvement_suggestions_20260414.md`

Live read-only database profiling was also performed on 2026-06-03.

## Current App Baseline

The Flask app already has a path/filename search endpoint at `/file_search`, implemented in `archives_application/archiver/routes.py`.

Current behavior:

- The form accepts a required `search_term`.
- Users can search filenames only or include directory path matches.
- Users can limit results to a Windows file server location copied from File Explorer.
- The route uses `FileLocationModel.filepath_search_query()`, which builds PostgreSQL full-text search over `file_locations.filename` and optionally `file_locations.file_server_directories`.
- Results are converted to a Pandas dataframe, rendered as an HTML table, and written to an `.xlsx` file in the app temp directory.
- The HTML table is capped at 1,000 rows; the spreadsheet contains the full result set.

Relevant existing patterns to preserve:

- User-facing file paths are converted through app utilities rather than built ad hoc.
- Long result sets already have an Excel download path.
- The UI already disables the submit button while searching.
- CAAN search and CAAN detail pages already expose CAAN -> projects -> project root locations.

Current limitations:

- The existing route searches file locations, not extracted document content.
- Results are location rows, not file-hash-level results.
- It does not explain whether content was unavailable, failed extraction, not chunked yet, or excluded due to low search value.
- It does not currently offer a broader structured result export beyond Excel.
- It does not support project or CAAN scope directly in the search form.

## Current Database Capabilities

The database is already well shaped for search.

Core tables:

- `files`: unique file identity by content hash.
- `file_locations`: one or more file server locations for each file.
- `file_contents`: extracted text and file-level MiniLM embeddings.
- `file_content_fts_chunks`: chunked full-text-search rows derived from `file_contents.source_text`.
- `file_content_failures`: extraction/embed failure records.
- `projects`, `caans`, `project_caans`: project and building scope metadata.
- `file_date_mentions`: date mentions extracted from document text; useful for later date filtering.

Important architectural fact:

```text
file hash = canonical result identity
file locations = access/display metadata
content chunks = retrieval evidence
```

This means content search should usually return one result per file hash, then display one preferred location plus an affordance or export field for additional locations.

The 2026-05-20 schema snapshot showed:

- `file_content_fts_chunks` exists.
- It has a generated `search_vector` column using `to_tsvector('simple', chunk_text)`.
- It has a GIN index on `search_vector`.
- `file_locations.filename` and `file_locations.file_server_directories` both have GIN expression indexes for full-text search.
- `file_contents.minilm_emb` has an IVFFlat pgvector cosine index.

## Live Profiling Snapshot

Read-only database profiling on 2026-06-03 returned:

| Metric | Count |
|---|---:|
| files | 757,220 |
| file_locations | 995,864 |
| file_contents | 423,281 |
| file_contents with nonempty text | 352,156 |
| file_content_fts_chunks | 706,049 |
| distinct chunked file hashes | 339,417 |
| file_content_failures | 41,217 |

All current `file_content_failures` rows returned by the stage aggregate were stage `extract`.

Top extension coverage from the same live profile:

| Extension | Files | Nonempty Text | Chunked | Failures | % Text | % Chunked |
|---|---:|---:|---:|---:|---:|---:|
| pdf | 301,324 | 206,647 | 199,699 | 19,551 | 68.6 | 66.3 |
| jpg | 121,623 | 24,433 | 23,613 | 2,010 | 20.1 | 19.4 |
| tif | 114,074 | 73,109 | 70,034 | 12,672 | 64.1 | 61.4 |
| dwg | 64,502 | 235 | 235 | 0 | 0.4 | 0.4 |
| doc | 21,976 | 13,799 | 13,141 | 2,412 | 62.8 | 59.8 |
| msg | 10,937 | 6,747 | 6,586 | 1,679 | 61.7 | 60.2 |
| xls | 8,885 | 6,905 | 6,628 | 115 | 77.7 | 74.6 |
| docx | 8,873 | 5,993 | 5,737 | 330 | 67.5 | 64.7 |
| zip | 4,073 | 0 | 0 | 0 | 0.0 | 0.0 |
| lnk | 3,207 | 0 | 0 | 0 | 0.0 | 0.0 |
| mov | 1,752 | 0 | 0 | 0 | 0.0 | 0.0 |

Design implications:

- Content search coverage is substantial but incomplete.
- PDF, TIFF, Office, and email results need first-class support.
- DWG, ZIP, link, video, and many image files should be represented as not content-searchable, not silently ignored.
- OCR/image formats need nuanced messaging: some have text, many do not.
- Coverage reporting should be part of the result page and exports.

## User Search Model

The most useful product model is one search page with scoped retrieval modes.

Recommended first modes:

- `Filename/path`: current behavior, improved only as needed.
- `Document text`: PostgreSQL FTS over `file_content_fts_chunks`.
- `Filename/path + document text`: combined common search, but with clear labels showing where the match came from.

Recommended later modes:

- `Similar documents`: file-level vector search using `file_contents.minilm_emb`.
- `Hybrid`: combine FTS and vector rankings with rank fusion after keyword search is working and users have examples.

Recommended shared filters:

- Location prefix: direct file server path or database-relative path.
- Project: project number lookup resolves to `projects.file_server_location` when present; projects without a file server location should be called out as unscoped/unsearchable for project-limited file search.
- CAAN: CAAN resolves to all directly linked projects through `project_caans`, then uses those projects' file server roots when present.
- File type/extension.
- Searchable status: content searchable, filename-only, extraction failed, not yet scraped, unsupported/low-value format.
- Optional later date filters using `file_date_mentions`.

## Scope Semantics

Location, project, and CAAN should probably all compile into directory-prefix constraints over `file_locations.file_server_directories`.

Important nuance:

- Project scoping depends on `projects.file_server_location`. If that value is null, no files should be included for that project-limited search unless a separate fallback rule is explicitly designed later.
- CAAN scoping can expand to many directly linked project roots through `project_caans`; linked projects with null file server locations should be counted and explained.
- The same file hash can appear inside and outside the selected scope.

Recommended behavior:

- For scoped searches, first compile the selected location/project/CAAN scope to a distinct set of file hashes, then search/rank content within that scoped file set.
- The important constraint is to avoid expanding to all `file_locations` rows before file-level ranking; duplicate locations can distort ranking and waste query time.
- If a file has multiple locations, display the location that satisfies the active scope when possible.
- If the user searches by content with a CAAN/project scope, the result should mean "matching file with at least one location under the selected CAAN/project roots."
- If the requested project or some CAAN-linked projects have no file server location, include a narrative note above the results rather than silently omitting them.

## Proposed Phase 1 Feature Shape

Phase 1 should be content keyword search, not semantic search.

Core behavior:

- Add a new document/content search UI or extend `/file_search` into a broader archive search page.
- Search text chunks using `file_content_fts_chunks.search_vector @@ websearch_to_tsquery('simple', :query)`.
- Rank chunks using `ts_rank_cd`.
- Limit chunk candidates early.
- Group by `file_hash`.
- Join top file hashes to `files` and `file_locations` for display.
- Generate snippets with `ts_headline` only after candidate narrowing.
- Preserve Excel export.
- Defer JSON export for v1.
- Include result-level and query-level coverage counts.
- Default HTML display limit: approximately 300 results.
- Default Excel export limit: approximately 3,000 file-level results, plus associated locations for those results.

Potential result columns:

- Rank
- Match type: content, filename/path, or both
- Filename
- Extension
- Size
- Primary location
- Additional location count
- Snippet
- Matching chunk count
- Text status
- Extraction failure status, if any
- File hash or equivalent duplicate identifier, useful for recognizing identical files across multiple locations

Potential coverage summary above results:

- Files in selected scope
- Files with extracted nonempty text
- Files included in FTS chunks
- Files with extraction failures
- Files not attempted or not content-searchable
- Files searched only by filename/path

This is especially important because users may otherwise read "no content hits" as "the archive does not contain it."

Potential narrative companion above the result tables:

- Restate the query and active scope in plain language.
- Report result limits, for example "Showing the top 300 results; download Excel for up to 3,000 results."
- Report projects/CAAN-linked projects excluded from scope because no `file_server_location` is recorded.
- Summarize searchable coverage, extraction failures, and filename-only/unsearchable files in the selected scope.
- Warn when the result set hit the display or export limit.

## Query Shape Direction

A scoped content search should apply scope before result limiting, but avoid expanding to every matching location row before ranking. Draft shape:

```sql
WITH q AS (
    SELECT websearch_to_tsquery('simple', :query_text) AS query
),
scope_paths AS (
    -- Compile explicit location, project, or CAAN filters to archive-root-relative
    -- path prefixes. For an unscoped search this CTE can be omitted.
    SELECT :path_prefix AS path_prefix
),
scoped_file_hashes AS (
    SELECT DISTINCT f.hash AS file_hash
    FROM file_locations fl
    JOIN files f ON f.id = fl.file_id
    JOIN scope_paths sp
      ON fl.file_server_directories LIKE sp.path_prefix || '%'
),
matching_chunks AS (
    SELECT
        c.file_hash,
        c.id AS best_chunk_id,
        c.chunk_index,
        ts_rank_cd(c.search_vector, q.query) AS chunk_rank
    FROM file_content_fts_chunks c
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
        (array_agg(best_chunk_id ORDER BY chunk_rank DESC))[1] AS best_chunk_id
    FROM matching_chunks
    GROUP BY file_hash
)
SELECT *
FROM file_scores
ORDER BY best_rank DESC, matching_chunks DESC
LIMIT :result_limit;
```

Then join the final file hashes back to scoped `file_locations` for display/export locations.

For unscoped or very broad searches, the best physical query plan may be to let the GIN FTS index find matching chunks first and then intersect with scope. For narrow project/location/CAAN searches, starting from scoped file hashes may be faster. The product semantics should be the same either way:

```text
results = files in selected scope AND files matching the search query
```

Do not globally limit content matches before applying scope, because that can drop valid scoped results that ranked below out-of-scope matches.

Open technical question: because `chunked_at` allows multiple chunk sets, confirm whether production currently has multiple chunk sets per file hash. If yes, query logic needs to select the current/latest chunk set per file hash before ranking.

## UX Considerations

Users should not have to understand PostgreSQL FTS to use the tool.

Recommended UI choices:

- Default to searching both filenames/paths and document text, but visually label where each result matched.
- Keep a filename-only mode for known file names.
- Provide project and CAAN inputs as structured fields, not only free-text location fields.
- Offer an advanced section for raw file server location prefix, extension filters, and result limits.
- Keep search synchronous for normal keyword queries.
- Show "Searching..." and prevent duplicate submissions, as the current page does.
- For very large exports, consider generating the export asynchronously later; do not block phase 1 on that unless early testing proves it necessary.

Potential result grouping:

- Default HTML should group by file hash: one result row per unique file, a primary/best scoped location in the row, and an affordance to view/copy additional matching locations.
- Excel export should include both a file-level `Results` sheet and a `Locations` sheet so downstream users can inspect every path without making the HTML table unwieldy.
- JSON export is deferred for v1; when it is added later, it should expose stable fields and avoid embedding raw HTML snippets only.

Suggested Excel workbook:

- `Results`: one row per file hash.
- `Locations`: all result locations, one row per location.
- `Coverage`: scope-level counts and search parameters.

V1 limit recommendation:

- HTML: show up to 300 file-level results by default. This is enough for browsing without making the page unwieldy.
- Excel: export up to 3,000 file-level results by default. This is large enough for downstream filtering while keeping request-time memory and workbook size bounded.
- Make both limits configurable constants so they can be raised after latency and memory testing.

## Searchable Status Categories

The UI and exports need vocabulary for coverage. Draft statuses:

- `content_searchable`: nonempty text exists and chunks exist.
- `text_extracted_not_chunked`: nonempty text exists but no FTS chunks.
- `empty_or_thin_text`: content row exists but text is empty or below a threshold.
- `extraction_failed`: `file_content_failures` row exists.
- `not_attempted`: no content row and no failure row.
- `unsupported_or_low_value_format`: extension/path category normally excluded from content extraction.
- `filename_only`: not content-searchable but included in filename/path search.

The status can be computed at query time initially. If it becomes expensive, materialize a view/table later.

The "thin text" threshold needs empirical tuning. For a first pass, use reporting only rather than exclusion. Example: flag files with fewer than 50 or 100 extracted characters as thin, but still allow filename/path matching.

## Data Analysis Needed Before Build Spec

Useful database analyses:

- Scope sizes: file counts and content-searchable counts by top-level Records directory.
- Project scope quality: count projects with null `file_server_location`; count project roots that actually match file_locations.
- CAAN expansion size: distribution of project counts and file counts per CAAN.
- Multiple-location behavior: how many hashes have more than one location, and how often locations cross project/CAAN boundaries.
- Chunk freshness: identify hashes where `file_contents.updated_at` is newer than latest chunk `chunked_at`.
- Multiple chunk sets: count hashes with more than one `chunked_at`.
- Query latency: run representative content searches with and without project/CAAN/location filters.
- Thin text distribution: count files by extracted text length buckets and extension.
- Failure taxonomy: top failure messages by extension and extractor stage.
- Unsupported format inventory: extensions with high file counts and no text.
- OCR usefulness: sample JPG/TIF text quality, especially for onsite photos vs scanned plan sheets.
- Result evaluation: manually test 20-30 likely user searches and judge relevance.

Potential profiling SQL should be written as saved research queries or notebooks, not embedded directly into the Flask request path.

## Stakeholder Context

Known v1 assumptions:

- Primary users are project managers and archivists.
- Secondary users may include contracts staff and planning staff.
- Recurring searches are not yet known from logs or observation.
- Users will search both for one known document and for all documents matching a term.
- Example known-document search: find a soil report generated during a project.
- Example broad topical search: search for `fixture` or `lighting` to find submittals related to a lighting fixture needing replacement.
- Users mostly care about the most current/final version of a document, but the database does not yet have a reliable version/finality model.
- Duplicate or repeated files should remain inspectable through all matching locations. The result table/export should include a file hash or equivalent duplicate identifier to show when multiple rows/locations represent the same file content.
- Project scope should include files only when the project has a `file_server_location`; if it does not, the result narrative should say that no project folder location is recorded for that project.
- CAAN scope should include files from projects directly linked in `project_caans`. It should not infer nearby or multiple-location projects unless those projects are linked.
- There are no current records sensitivity concerns with showing content snippets.
- JSON export is deferred for v1.
- Search logging is deferred for v1, though it may be useful later for analytics and search-quality improvement.

Remaining design questions:

- What are the top 10-20 real searches users perform or wish they could perform?
- Should current/final documents be approximated with path/filename heuristics in v1, or simply left to ranking and user inspection?
- Validate whether users prefer the recommended HTML grouping by file hash, or whether they need one row per matching location with a duplicate hash column.
- Decide whether the Excel export's 3,000 limit should apply to file-level results, with all locations for those files included, or to total location rows.
- Which fields are essential for PMs versus archivists in the first result table?

## External Research That Could Help

Recommended targeted research before final implementation:

- Review search UX patterns in document management systems such as Mayan EDMS, Paperless-ngx, Docspell, OpenKM, and SharePoint/OneDrive search. Focus on filters, result grouping, snippets, and "unsearchable document" messaging rather than adopting code.
- Review PostgreSQL FTS query UX, especially `websearch_to_tsquery`, `ts_headline`, phrase behavior, stopwords, and the practical difference between `simple` and `english` configurations for technical archives.
- Review faceted search patterns for archives/document repositories.
- Review accessible table/export UX for large result sets.
- Review hybrid lexical/vector search only after content FTS is usable.

This research can be delegated as separate ChatGPT tasks. The useful output would be a compact pattern inventory, not a broad literature review.

## Risks And Traps

Key risks:

- Joining chunks to all locations too early can distort rankings and create slow queries.
- Presenting location rows as primary results can hide duplicate-file semantics.
- A "no results" page can mislead users if it does not explain content coverage.
- Content quality varies sharply by extension and extractor; junk text can create false confidence.
- Some documents have real but low-signal text, especially drawing sets with repeated title blocks.
- Search scoped by CAAN may surprise users because CAAN/project relationships are many-to-many and project folder locations are incomplete.
- Excel exports can become memory-heavy if generated from very large dataframes inside a request.
- Future JSON export can accidentally become an unsupported public API unless versioned/defined.
- The app's current `db_query_to_df()` pattern may be too blunt for large search/export workflows.
- Query latency needs measurement with realistic terms before UI promises are made.

Operational risks:

- The scraper backlog is still moving, so coverage numbers will change.
- Search chunks are derived data; stale chunks must be detectable.
- Failure rows are extraction failures, but unsupported formats may have no failure row and no content row.
- If semantic search is added too early, users may treat vague similarity as authoritative.

## Recommended Next Step

Before writing the implementation specification, produce a short empirical profiling notebook or SQL report answering:

1. How many files are content-searchable within common location/project/CAAN scopes?
2. How fast are representative content searches with the expected filters?
3. How many files fall into each proposed searchable-status category?
4. How stale are FTS chunks relative to `file_contents.updated_at`?
5. What result format best handles duplicate locations?

After that, write a feature spec for a PostgreSQL-native phase 1 search page:

- content keyword search over `file_content_fts_chunks`
- path/name search reuse
- project, CAAN, and location filters
- coverage summary
- file-hash-level HTML results
- Excel export
- explicit deferral of semantic/hybrid search
- explicit deferral of JSON export and search logging
