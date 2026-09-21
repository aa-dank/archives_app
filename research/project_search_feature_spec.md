# Project Search Feature Specification

Last updated: 2026-09-21

## Purpose

Add a dedicated project-search workflow to the Archives App without combining project and CAAN results into one interface.

The selected design is:

- Add a new `/project_search` page for finding projects.
- Preserve `/caan_search` as the dedicated CAAN-search page.
- Share search-service conventions and database-resolution rules where useful, but do not make either page a mixed project/CAAN search.
- Keep `/archives_search` as the separate file and document-retrieval workflow.

The project search page is a discovery and navigation feature. Its primary destination is the existing read-only `/project_info?project_id={id}` page. It must not perform filesystem operations, modify synchronized project data, or infer a file-server location when the database does not contain one.

## Current context

### Existing project information

`archives_application/project_tools/project_info.py` and the `/project_info` route already provide:

- strict `project_id` and `project_number` selector validation;
- case-insensitive, trimmed exact project-number resolution;
- `409 Conflict` behavior for duplicate project numbers;
- canonical project-ID links;
- project facts, CAAN relationships, contract data, and recorded archive-root state;
- one path-boundary-safe indexed-file-location count for the selected project; and
- a read-only authenticated `/api/project_info` endpoint.

Project numbers are not schema-unique. Search results must therefore always retain and link the database project ID rather than treating a visible project number as a unique identifier.

### Existing CAAN search

`/caan_search` currently uses `CAANSearchForm` and searches:

- CAAN number;
- CAAN name; and
- CAAN description.

Terms are matched case-insensitively with AND semantics across whitespace-separated terms. An exact CAAN input redirects to `/caan_info/<caan>`.

This behavior remains available in the first project-search release. The project-search feature must not silently change CAAN search result semantics or URLs.

### Existing archive search

`/archives_search` is the canonical file-retrieval workflow. It already supports:

- filename-only search;
- filename/path search;
- extracted document-text search;
- combined filename/path plus document-text search;
- all-archive, location, project, and CAAN scopes;
- file-hash-level result grouping;
- coverage and extraction-status messaging; and
- Excel export.

Project search must not duplicate these file-search modes or merge file rows into project metadata results.

### Database context

The current database reference documents:

- 10,059 projects;
- 1,393 CAANs;
- 12,828 project-CAAN relationships;
- 5,711 contracts;
- 760,000 unique files; and
- 1,000,000 file-location rows.

As of 2026-09-16, `projects` has the deployed non-unique normalized-number index `ix_projects_number_normalized` on `lower(btrim(number))`. `project_caans` also has the deployed reverse index `ix_project_caans_caan_id_project_id` on `(caan_id, project_id)` for the separate CAAN workflows. There is no dedicated project or contract text-search index.

The authoritative schema and migration source are in `business_services_db`. New indexes must be added through that repository's Alembic migrations and applied with the database administration role. The Archives App must not create production DDL at request time.

Reference material:

- `C:\Users\adankert\projects\business_services_db\reference\ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`
- `C:\Users\adankert\projects\business_services_db\reference\business_services_db_schema_20260916.md`
- `research/project_info_feature_spec.md`
- `research/user_search_feature_spec.md`

## Goals

- Provide a dedicated, bookmarkable project-search page.
- Make project number and project name easy to find.
- Support useful project and contract metadata matches without exposing a large detail record in the result list.
- Preserve duplicate project-number safety.
- Link every result to the canonical project-information page.
- Make a future project-to-file-search handoff possible without introducing ambiguous project-number behavior.
- Provide a bounded top-300 HTML result list and a complete, on-demand spreadsheet export without loading either complete result set into application memory.
- Use database-backed search and indexes appropriate for the current data size and future growth.
- Keep project search understandable as a metadata lookup tool distinct from archive document retrieval.

## Non-goals

The first release does not:

- combine project and CAAN rows into a single mixed result page;
- search CAAN fields or relationships from Project Search; users should use the dedicated CAAN Search workflow for that discovery path;
- replace or redesign `/caan_search`;
- replace `/archives_search`;
- search extracted document text;
- enumerate or scan SMB/file-server directories;
- infer a project archive root from the project number;
- modify `projects`, CAANs, contracts, or synchronized FileMaker-owned fields;
- calculate indexed-file counts for every result row;
- infer current/final document status;
- add saved searches, analytics, or search-history UI;
- add a public unauthenticated JSON search API; or
- introduce a denormalized project-search table unless measured query behavior later requires it.

## Route and request contract

### HTML route

Add:

`GET /project_search`

The route is read-only and should use query parameters so result pages can be bookmarked and shared.

The initial page with no search parameters renders the form. A submitted search returns the same page with results and the active query/filter state.

The route should reject unknown or repeated query parameters rather than silently ignoring them.

### Query parameters

The first release may accept:

- `query`: optional free-text query, trimmed and limited to 200 characters;
- `status`: optional `any`, `open`, `closed`, or `unknown`;
- `drawings`: optional `any`, `yes`, `no`, or `unknown`;
- `has_archive_location`: optional `any`, `yes`, or `no`;

A request must contain either a non-empty `query` value or at least one active filter. `any` values are inactive. A blank or whitespace-only `query` with no active filter renders the empty form and must not render all projects by default. A blank `query` with an active filter, such as `status=open`, is a valid filtered search.

`query` is a search query, not a project selector with redirect semantics. Even when it exactly matches one project number, the response remains a result page. This avoids special behavior for duplicate numbers and lets users see the selected record before opening its detail page.

### Validation outcomes

- Empty initial request: `200 OK`, empty search form.
- Valid query or filter: `200 OK`, ranked results.
- Unknown/repeated/malformed parameters: `400 Bad Request`.
- Valid query with no matches: `200 OK`, no-results state with the active query and filters preserved.

## User interface

### Navigation

Add a navigation item such as **Project Search** or **Find Projects** under the project/file-server tools area.

Do not rename the existing CAAN Search item in the first release. The two pages should remain visibly distinct:

- Project Search — construction project records.
- CAAN Search — campus building and asset records.
- Archive Search — files and document contents.

### Search form

The page should contain:

1. A prominent **Search projects** text field.
2. A short explanation that the query searches project metadata and linked contract metadata.
3. An advanced filter section containing status, drawings, and archive-location-state filters.
4. A submit button that disables itself while the request is being submitted, following the existing CAAN and archive-search behavior.

The form should explain:

- exact project-number matches are ranked first;
- duplicate project numbers may produce multiple project rows;
- a missing archive location means no recorded project root, not proof that the project has no files; and
- file/document searching is available through Archive Search rather than this page.

### Result presentation

Render one row per `projects.id`. Do not render one row per contract or file location.

The HTML table is a compact, ranked navigation view. Its columns are:

- Project number, linked to Project Information;
- Project name, with project manager as compact secondary text when present;
- Project status: Open, Closed, or Unknown;
- Drawings: Yes, No, or Unknown;
- Initial contract value, aggregated from linked contracts' recorded original contract costs;
- Archive root status: Recorded or Not recorded; and
- Matched in: Project and/or Contract.

The visible project-number link must use:

`url_for('project_tools.project_info', project_id=row.id)`

It must never construct a project-information link from the project number alone. The table provides no other per-row action; a future project-to-file-search action belongs on the Project Information page, where the recorded archive-root state is already visible.

For Initial contract value, distinguish missing-record states rather than implying a zero-dollar amount:

- `No contract data` when the project has no linked contract records;
- `Not recorded` when linked contracts exist but none has an original contract cost;
- a currency total when every linked contract has an original contract cost; and
- a currency total marked `Partial` when one or more linked contracts lack an original contract cost.

Do not show full project notes, contract scope descriptions, individual contract financial values, or FileMaker IDs in the result table. A match in a searchable project or contract field may be indicated with concise `Project` and/or `Contract` labels; the user can open the project-information page for the synchronized detail.

Do not offer user-controlled sorting in the HTML table. Its row order communicates the search ranking; users who need to sort, filter, or compare the complete result set should use the spreadsheet export.

### Result ordering and display limit

Use stable ordering:

1. exact normalized project-number match;
2. project-number prefix match;
3. exact normalized project-name match;
4. every term matched in project-local metadata;
5. every term matched in one directly linked contract;
6. terms distributed across project-local metadata and/or multiple directly linked contracts;
7. normalized project number; and
8. project ID as the final tie-breaker.

An exact normalized project-name match means the full normalized `query` value equals the full normalized project name.
Compute rank over the complete matching set before applying the HTML limit. Return the top 300 ranked rows in the HTML response. Query for one additional row so the UI can state clearly when more matching projects exist.

The on-demand spreadsheet export must use the same query semantics and project ordering, but include the complete matching result set rather than the HTML top-300 subset. It is the bulk review and sorting format, with the explicitly approved export-only disclosures for user-facing archive paths and contract detail. The workbook represents the data at export time; synchronized data may change between the HTML search and export requests.

The page should show:

- the escaped query and active filters;
- the number of displayed results;
- a notice when the top-300 HTML limit excludes additional matches;
- a link to download the complete matching result set as a spreadsheet when results exist; and
- a clear no-results message that does not imply the project or its files do not exist.

An exact project-number query that matches multiple records must display all matching project rows. It must not select an arbitrary row or redirect to `/project_info?project_number=...`.

## Search semantics

### Fields searched

Project-local fields:

- `projects.number`;
- `projects.name`;
- `projects.campus_client`;
- `projects.project_manager_name`;
- `projects.inspector_name`.

Related contract fields:

- `contracts.contract_number`;
- `contracts.contractor_org_name`;
- `contracts.executive_design_org_name`;
- `contracts.funding_number`; and
- `contracts.scope_description`.

Financial values and FileMaker primary IDs are not searched in the normal query.

Project notes and raw `file_server_location` values are not searched in the first release. Notes are often noisy and may contain sensitive operational context; raw archive-root paths are implementation data rather than useful discovery text. `has_archive_location` remains the way to filter on whether a root has been recorded.

### Query parsing

The first release uses simple whitespace-term parsing. Each term is a case-insensitive substring match within an allowed field: conceptually, `ILIKE '%' || escaped_term || '%' ESCAPE '\\'`. Terms use AND semantics across the query and OR semantics across the permitted project-local and linked-contract fields. Escape SQL `LIKE` wildcard characters in user input so `%` and `_` retain literal meaning. Normalize only harmless outer whitespace and case; preserve meaningful project-number punctuation such as hyphens.

The implementation must separately retain exact and prefix project-number matching because whitespace-term matching is not a sufficient identifier search strategy. Web-style query syntax, quoted phrases, `OR`, and negative terms are deferred. If phrases are added later, a phrase must match within one project-local record or one contract row; it must not bridge fields or records.

CAAN Search retains its current whitespace-term behavior in the first project-search release. Aligning CAAN and project query syntax is deferred until there is a reason to change the existing CAAN workflow.

### Relationship matching

A project matches when every required query term can be satisfied by at least one project-local field or one field on a directly linked contract. Terms may be satisfied by different project fields and by different linked contract rows.

Use contract relationship predicates that return project IDs, rather than joining every contract row into the result set. This prevents duplicate project rows and keeps the top-N display and ordering correct. Contracts without a `project_id` cannot match.

The result must expose the ordered set of broad sources that contributed one or more query-term matches: `Project`, `Contract`, or both. These labels are explanatory only; they must not expose note or contract-scope text. A one-contract match ranks above a distributed contract match, and either ranks below a project-local match of comparable identifier quality.

### Filters

`status` maps to `projects.closed`:

- `open`: `closed = false`;
- `closed`: `closed = true`;
- `unknown`: `closed IS NULL`;
- `any`: no predicate.

`drawings` maps similarly to `projects.drawings`.

`has_archive_location=yes` means a non-null, non-blank `file_server_location`.

`has_archive_location=no` means null or blank `file_server_location`.

The first release should not offer a `has_indexed_files` filter. That would require an aggregate over the much larger `file_locations` table and could be confused with a live filesystem check. The existing project-information page remains the authoritative place for the one-project indexed-location count.

## Application implementation shape

### Search service

Add a small read-only service module, for example:

`archives_application/project_tools/project_search.py`

It should own:

1. request-parameter validation;
2. query normalization;
3. project search construction;
4. relationship-match predicates;
5. score and match-source calculation;
6. HTML display-limit detection;
7. result-row and spreadsheet-row preparation; and
8. user-facing search-state metadata.

The route should remain thin:

1. parse and validate the request;
2. call the search service;
3. translate defined validation outcomes to HTTP responses; and
4. render the template.

Do not generate raw HTML tables from database content. Let Jinja escape all database-backed values.

### ORM and SQL strategy

Use SQLAlchemy for ordinary filters and contract relationship existence checks. Keep all search values parameterized.

The query must:

- select project rows as the primary result identity;
- avoid duplicate rows from contract joins;
- use `EXISTS` or grouped subqueries for contract matches and counts;
- load only the project and aggregate data needed for the returned page; and
- avoid one query per result row.

The HTML query must retrieve at most 301 ranked rows. The spreadsheet export may iterate the complete ordered result set, but should use a write-only workbook or similarly bounded-memory approach rather than construct a complete DataFrame.

The project-information resolver should share normalization rules with project search. The existing `/api/project_location` legacy endpoint is not a suitable resolver because it uses a first-match, number-only lookup and should not be copied into the new feature.

### Template

Add:

`archives_application/templates/project_search.html`

The template should use `layout.html` and existing Bootstrap conventions. It should be responsive, HTML-escaped, and consistent with the CAAN and archive-search forms.

No filesystem calls, `ServerEdit`, RQ tasks, or database writes are permitted in this route.

### Spreadsheet export

Add an on-demand, public `GET /project_search/export` route that accepts the same validated search criteria as the HTML route. The **Download all results** control appears only on a non-empty HTML result page and invokes this route with that page's query and filters. It must reject unknown or repeated parameters, require an active query or filter, and create no database or filesystem records beyond the temporary downloadable workbook. The export is a direct download rather than a background job because the current project table contains approximately 10,000 rows and the expected matching sets are substantially smaller. It requires no login and intentionally supports bulk export of the public project and contract data described below. Do not impose a feature-specific result-row cap; apply the deployment's ordinary public-route or proxy rate limits if present.

Sanitize spreadsheet cell values that could be interpreted as formulas before writing database-backed text to XLSX. The workbook must contain a `Projects and contracts` sheet and a `Search information` sheet. The latter records the query/filter state, export timestamp, total matched-project count, and total exported-row count.

`Projects and contracts` is a flattened table, ordered first by the complete project ranking and then by the existing stable natural contract-number order. Emit one row for every direct project-contract relationship, repeating the project columns for each linked contract. A matching project with no linked contracts must still produce one row with blank contract columns. Include every directly linked contract for each matching project, not only a contract that contributed to the search match.

Project columns are:

- result rank and ranking band;
- canonical project ID and Project Information URL;
- project number and name;
- status, drawings, campus client, project manager, and inspector;
- user-facing archive location, produced with `FileServerUtils.user_path_from_db_data(...)` and `USER_ARCHIVES_LOCATION`, plus archive-root status;
- initial contract value, with the same `No contract data`, `Not recorded`, and `Partial` semantics as the HTML table, plus the number of linked contracts with a recorded original contract cost and the total linked-contract count; and
- matched-in labels.

The archive-location column must never reveal the raw database value. When no root is recorded it is blank; when a recorded root cannot be converted because the configured user archive location is unavailable, it is `Unavailable` rather than a raw path.

Contract columns mirror the established Project Information contract table, except FileMaker IDs: contract number; contractor; executive design organization; scope description; cost estimate; original contract cost; change-order total; revised total including change orders; funding number; bid, contract, notice-to-proceed, occupancy, completion, termination, and current-expected-end dates; and original, change-order, and current durations. Contract cells are blank for a project-only row.

The HTML result table remains compact and does not display the user-facing archive path, contract scope, or individual contract financial values. Project notes, FileMaker IDs, CAAN data, and raw database archive-root paths remain excluded from the workbook. The user-facing archive path and the listed contract-detail fields are intentional public export disclosures.

## Database and indexing requirements

### Deployed baseline indexes

`business_services_db` migration `30966ca0a87a`, deployed on 2026-09-16, provides a non-unique normalized project-number B-tree index equivalent to `lower(btrim(projects.number))`. The application model metadata declares the same index, but the Alembic migration remains the production schema source of truth.

The same migration also provides the reverse `project_caans (caan_id, project_id)` index for the separate CAAN workflows. Project Search does not use CAAN data in its first release.

### Phase 0 measured preflight (2026-09-21)

The following evidence was collected from the live `business_services_db` with read-only transactions and `EXPLAIN (ANALYZE, BUFFERS)`. The server was PostgreSQL 17.11. PostgreSQL statistics for `projects` and `contracts` had been auto-analyzed on 2026-09-20.

#### Data and filter distributions

- `projects`: 10,059 rows; `contracts`: 5,713 rows. `contracts(project_id)` has 5,710 linked rows covering 5,396 projects; the largest project has 34 contracts.
- There are 10,030 distinct normalized project numbers. Twenty-five normalized numbers are duplicated, covering 54 project rows; the largest duplicate group has four rows. Project-ID result identity remains required.
- Status is strongly skewed: 9,691 closed (96.34%) and 368 open (3.66%); no `closed` value is null.
- Drawings distribution is 4,628 yes (46.01%), 3,157 no (31.38%), and 2,274 unknown (22.61%). Archive-root state is 8,257 recorded (82.08%), 1,800 null (17.90%), and 2 blank (0.02%).
- Search-field completeness matters more than raw table size: `campus_client` is blank/null for 9,840 projects (97.82%) and `inspector_name` for 8,187 (81.39%); `project_manager_name` is blank/null for 2,312 (22.98%). On contracts, `executive_design_org_name` is blank/null for all 5,713 rows, while `scope_description` is blank/null for 1,768 (30.95%) and `funding_number` for 570 (9.98%). Do not add an index for an all-blank field.

#### Observed index and plan results

The live catalog confirms `ix_projects_number_normalized` on `lower(btrim(number))` and `idx_contracts_project_id` on `(project_id)`; neither table has a project/contract text-search index, and `pg_trgm` is not installed. The normalized-number index had 18 cumulative scans when observed; `idx_contracts_project_id` had 2,542. These counters are contextual only; the query plans below establish use for the tested shapes.

All cases selected IDs, used the stable normalized-number/project-ID order, and used the 301-row HTML bound where applicable:

| Query shape and representative live term | Plan / index use | Execution time | Buffer result |
|---|---|---:|---|
| Normalized exact number (`1200`) | `Index Scan` on `ix_projects_number_normalized` | 1.446 ms | 4 hit, 2 read |
| Duplicate normalized number (`3301-041`, four rows) | `Bitmap Index Scan` then `Bitmap Heap Scan` on `ix_projects_number_normalized` | 0.793 ms | 3 hit, 1 read |
| Normalized-number prefix (`1200%`, 136 rows) | `Seq Scan` on `projects`; the existing B-tree was not used for `LIKE` | 10.021 ms | 491 hit |
| Project-local literal substring (`hahn`, 186 rows) | `Seq Scan` on `projects` | 39.858 ms | 488 hit |
| Contract literal substring through `EXISTS` (`construction`, 1,715 contracts / 1,669 projects) | `Nested Loop Semi Join`; `Index Scan` on both `ix_projects_number_normalized` for ordered project traversal and `idx_contracts_project_id` for the relationship predicate | 21.604 ms | 7,305 hit, 17 read |
| Contract-match result with the planned contract-count/cost aggregate | `Seq Scan`/`HashAggregate` of `contracts`, `Hash Join`, and top-N sort | 53.769 ms | 1,497 hit |
| Two-term AND semantics (`hahn` and `construction`), permitting project/contract distribution | Two contract `Seq Scan` hashed subplans; normalized-number index supplied final order only | 118.754 ms | 6,570 hit, 54 read |

#### Query-plan decision before implementation

Do **not** add a new project-search index before the first implementation. Exact normalized-number lookup is already well supported, and the measured substring/relationship cases remain synchronous and bounded at the current live data size, including the 301-row aggregate result shape and a two-term distributed-match case.

Do not add the proposed `simple`-configuration full-text GIN indexes as a substitute for this release's literal `ILIKE '%term%'` contract: full-text token matching would change the specified substring behavior. Do not introduce `pg_trgm` or broad trigram indexes yet: the extension is absent, the measured worst representative case was 118.754 ms, and broad indexes across every project and contract text field lack plan evidence at this size. Reassess after the implemented query has production latency telemetry or materially larger tables; first rerun the same plans, including result aggregates and multi-term cases. If a future measured need is specifically number/name substring or typo matching, evaluate narrowly scoped trigram indexes through a controlled `business_services_db` migration.

### Full-text index option

For the first implementation, use the current row counts and `EXPLAIN (ANALYZE, BUFFERS)` results to determine whether expression GIN indexes are needed immediately.

If text search requires them, add expression indexes using the same expressions used in the query:

- Projects: a `simple` tsvector over number, name, campus client, manager, and inspector.
- Contracts: a `simple` tsvector over contract number, contractor, design organization, funding number, and scope.

Contract fields cannot be included in a generated `projects` search column without denormalizing synchronized data. Keep contract matching as indexed relationship predicates unless a future materialized search document is justified.

### Trigram option

`pg_trgm` is not listed among the installed extensions in the supplied schema reference. Do not require it for the first release unless representative queries demonstrate that full-text search and exact/prefix matching cannot support expected usage.

If users need substring or typo-tolerant matching, add `pg_trgm` through a controlled migration and index only the normalized project number and name first. Do not add broad trigram indexes to every metadata column without query-plan evidence.

### Migration operations

Production index creation should use the database repository's controlled Alembic process. Large indexes should be created concurrently where supported, using the migration's autocommit requirements. Run `EXPLAIN (ANALYZE, BUFFERS)` against a production-like dataset before and after the migration.

No new `file_locations` index is required solely for project search.

## Archive Search integration

Project Search and Archive Search remain separate features, but they should have explicit handoff points.

### Project-to-file handoff

A future Project Information page may offer **Search files in this project**. The Project Search result table does not include this action.

Because project numbers are not unique, a canonical project-ID handoff is required. The archive-search scope layer must eventually accept a project ID, for example through an internal `project_id` scope parameter or equivalent validated state.

When a project ID is supplied:

- resolve exactly that project row;
- use only its recorded non-blank `file_server_location`;
- display the project number and name in the search scope summary; and
- explain that no file scope is available when the root is missing.

Manual project-number scope may remain supported. If a number matches multiple project rows, the archive search must either search all recorded roots with an explicit message or require the user to choose a project. It must not silently select the first row.

### CAAN-to-file handoff

The existing CAAN result and CAAN detail pages may continue to use CAAN-based archive scope. CAAN codes are unique, so the ambiguity problem is different from project numbers.

### Shared path semantics

Both features must use the same directory-boundary rules:

- exact root match; or
- descendant path match using the root followed by `/`.

Neither feature may infer a root from a project-number prefix when `file_server_location` is null.

## API boundary

An authenticated `/api/project_search` endpoint is deferred from the first HTML release.

If added later, it should:

- use the same search service as the HTML route;
- require the same active-user authentication policy as `/api/project_info`;
- return stable project IDs and canonical detail URLs;
- support bounded result limits;
- reject unknown or repeated parameters; and
- return structured match-source values rather than HTML.

The API should not accept credentials in query parameters.

## Performance and operational constraints

- HTML result limit: 300 ranked project rows.
- Query length maximum: 200 characters.
- Search must remain synchronous for the first release; project metadata volume is small enough to validate this assumption.
- Do not build a full result DataFrame for the HTML response or spreadsheet export.
- Do not count `file_locations` per result.
- The HTML query must not load all contracts for all matching projects. The flattened spreadsheet export intentionally loads all direct contracts for its matching projects, using bounded-memory iteration.
- Use a stable final tie-breaker to preserve the same order in HTML and spreadsheet results.
- Log unexpected search failures, but do not log contract scope text or raw archive-root paths.

Before release, measure:

- exact normalized project-number lookup;
- common project-name searches;
- contract-field searches;
- status and archive-root filters; and
- duplicate-number searches.

## Security and data handling

- `GET /project_search` and `GET /project_search/export` are public and do not require an active user. The deferred JSON API remains separately authenticated.
- Escape project, contract, notes, and archive-root values in templates.
- Do not render raw search snippets from notes or contract scope as trusted HTML.
- Do not expose FileMaker primary IDs in normal result rows.
- Do not expose full notes or financial fields in the search result list.
- The public workbook may include the user-facing archive location and the specified contract-detail fields, but never raw database archive-root paths, project notes, or FileMaker IDs.
- Do not perform SMB access or filesystem existence checks in the search route.

## Validation plan

### Unit-level checks

Test:

- initial empty form;
- query length and parameter validation;
- exact project-number matching;
- case and outer-whitespace normalization;
- duplicate project-number results;
- project-name and multi-term searches;
- contract relationship matches;
- a term found only in project notes or a raw archive-root path does not produce a Project Search match;
- CAAN-only metadata does not produce a Project Search match;
- a `caan` query parameter is rejected as unknown;
- status, drawings, and archive-root filters;
- null and blank archive-root handling;
- blank `query` with default filters renders the empty form, while blank `query` with an active filter searches;
- top-300 limit detection and consistent project ordering between HTML and spreadsheet output;
- public export authorization, active-criteria enforcement, and absence of an export control on blank or no-results pages;
- project-contract row expansion, including one blank-contract row for a project with no contracts and inclusion of non-matching linked contracts;
- user-facing archive-location conversion and prevention of raw database-path disclosure;
- match-source labeling;
- spreadsheet formula-injection neutralization; and
- HTML escaping of database values.

### Database checks

Run `EXPLAIN (ANALYZE, BUFFERS)` for:

- normalized exact project-number lookup;
- prefix project-number lookup;
- common project-name search;
- contract-field relationship search; and
- a duplicate project-number query.

Verify that the normalized-number index is used for exact lookup. Decide separately whether full-text or trigram indexes are justified for project and contract text.

### Manual acceptance checks

1. `GET /project_search` renders the empty form.
2. A project-number query returns matching projects and links each row to its project ID URL.
3. A deliberately duplicated project number returns every matching project row.
4. A project-name query returns relevant projects with stable ordering.
5. Contract relationship matches do not duplicate project rows, and one-contract matches rank above distributed contract matches.
6. Status, drawings, and archive-root filters work independently and together.
7. A CAAN name or description that is absent from the project and its contracts does not produce a project result.
8. A project with no recorded root is labelled as missing a recorded root; no path is inferred.
9. Long names, notes, scopes, and path values remain escaped and wrapped.
10. The HTML page shows no more than 300 rows, accurately signals truncation, and its non-empty result state offers a public Download all results control.
11. The workbook expands every matching project into its linked-contract rows in project-ranking order, preserves a row for a project with no contracts, and never emits a raw database archive-root path.
12. Spreadsheet cell text cannot be interpreted as a formula.
13. Project search does not query SMB, enqueue work, or write database rows.
14. A project-to-file handoff, when implemented, uses a project ID and does not silently resolve a duplicate number.
15. Python compilation, Jinja parsing, focused tests, and `git diff --check` pass.

## Recommended implementation phases

### Phase 0: Query and data preflight

- Confirm duplicate project-number frequency.
- Profile project and contract search fields.
- Run baseline query plans.
- Confirm the desired result columns with project managers and archivists.

### Phase 1: Dedicated project search

- Add the request parser and project search service.
- Add the `/project_search` route and template.
- Add canonical project-information links.
- Add top-300 ranked HTML display and on-demand complete spreadsheet export.
- Add core filters; defer the campus-client filter until a controlled vocabulary is profiled and approved.
- Preserve `/caan_search` unchanged.

### Phase 2: Baseline-index verification

- Verify the deployed normalized project-number index is used for exact lookup.
- Validate query plans for the project-and-contract search implementation.

### Phase 3: Search quality and integration

- Add expression GIN or trigram indexes only when justified by measured queries.
- Add a Project Information-page project-ID-based Archive Search handoff.
- Align project scope resolution with the canonical project resolver.

### Phase 4: Optional API

- Add authenticated `/api/project_search` only after the HTML semantics and result-limit contract are stable.

## Deferred decisions

- Whether a controlled campus-client vocabulary justifies adding a campus-client filter.
- Whether `pg_trgm` is necessary for partial and typo-tolerant matching.
- Whether the project search page should offer autocomplete.
- Whether a future global launcher should link to project, CAAN, and archive searches without combining their results.
