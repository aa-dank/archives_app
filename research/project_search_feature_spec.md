# Project Search Feature Specification

Last updated: 2026-09-15

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

The current database reference documents approximately:

- 10,000 projects;
- 1,400 CAANs;
- 12,800 project-CAAN relationships;
- 5,700 contracts;
- 760,000 unique files; and
- 1,000,000 file-location rows.

The `projects` table currently has primary-key and FileMaker-key indexes but no dedicated normalized project-number or project-text search index. The `project_caans` primary key is ordered `(project_id, caan_id)` and does not provide the preferred leading order for CAAN-to-project lookup.

The authoritative schema and migration source are in `business_services_db`. New indexes must be added through that repository's Alembic migrations and applied with the database administration role. The Archives App must not create production DDL at request time.

Reference material:

- `C:\Users\adankert\projects\business_services_db\reference\ARCHIVES_DB_AND_FILE_SERVER_REFERENCE.md`
- `C:\Users\adankert\projects\business_services_db\reference\business_services_db_schema_20260902.md`
- `research/project_info_feature_spec.md`
- `research/user_search_feature_spec.md`

## Goals

- Provide a dedicated, bookmarkable project-search page.
- Make project number and project name easy to find.
- Support useful metadata and relationship matches without exposing a large detail record in the result list.
- Preserve duplicate project-number safety.
- Link every result to the canonical project-information page.
- Make a future project-to-file-search handoff possible without introducing ambiguous project-number behavior.
- Provide bounded, paginated results rather than loading all matching projects into memory.
- Use database-backed search and indexes appropriate for the current data size and future growth.
- Keep project search understandable as a metadata lookup tool distinct from archive document retrieval.

## Non-goals

The first release does not:

- combine project and CAAN rows into a single mixed result page;
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

- `q`: optional free-text query, trimmed and limited to a configured maximum length such as 200 characters;
- `status`: optional `any`, `open`, `closed`, or `unknown`;
- `drawings`: optional `any`, `yes`, `no`, or `unknown`;
- `has_archive_location`: optional `any`, `yes`, or `no`;
- `caan`: optional exact CAAN code used as a project relationship filter;
- `campus_client`: optional campus-client filter when a controlled value is supplied;
- `page`: positive integer, default `1`; and
- `page_size`: positive integer, default `50`, with a hard maximum of `100`.

A request must contain either a non-empty `q` value or at least one supported filter. An unfiltered request must not render all projects by default.

`q` is a search query, not a project selector with redirect semantics. Even when it exactly matches one project number, the response remains a result page. This avoids special behavior for duplicate numbers and lets users see the selected record before opening its detail page.

### Validation outcomes

- Empty initial request: `200 OK`, empty search form.
- Valid query or filter: `200 OK`, paginated results.
- Unknown/repeated/malformed parameters: `400 Bad Request`.
- Valid query with no matches: `200 OK`, no-results state with the active query and filters preserved.
- Page beyond the result set: `200 OK` with an empty page and a clear navigation state, or `404` if that convention is preferred consistently with the rest of the application. The implementation should choose one behavior and test it.

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
2. A short explanation that the query searches project metadata and related CAAN/contract metadata.
3. An advanced filter section containing status, drawings, archive-location state, CAAN, and campus-client filters.
4. A bounded page-size choice only if the existing UI conventions support it; otherwise retain the configured default.
5. A submit button that disables itself while the request is being submitted, following the existing CAAN and archive-search behavior.

The form should explain:

- exact project-number matches are ranked first;
- duplicate project numbers may produce multiple project rows;
- a missing archive location means no recorded project root, not proof that the project has no files; and
- file/document searching is available through Archive Search rather than this page.

### Result presentation

Render one row per `projects.id`. Do not render one row per CAAN, contract, or file location.

Recommended project result columns:

- Project number;
- Project name;
- Project status: Open, Closed, or Unknown;
- Drawings: Yes, No, or Unknown;
- Campus client, when present;
- Project manager, when present;
- Associated CAAN codes or a compact count;
- Contract count;
- Archive root status: Recorded or Not recorded;
- Match source, such as Project name, CAAN, Contract, or Notes; and
- A link to project information.

The visible project-number link must use:

`url_for('project_tools.project_info', project_id=row.id)`

It must never construct a project-information link from the project number alone.

Do not show full project notes, contract scope descriptions, financial values, or FileMaker IDs in the result table. A match in one of those fields may be indicated with a concise match-source label; the user can open the project-information page for the synchronized detail.

### Result ordering and pagination

Use stable ordering:

1. exact normalized project-number match;
2. project-number prefix match;
3. exact phrase/name match;
4. weighted text relevance;
5. normalized project number; and
6. project ID as the final tie-breaker.

Return at most `page_size` rows. Use a bounded query and avoid loading the complete matching set into Pandas or Python.

The page should show:

- the escaped query and active filters;
- the number of results on the current page;
- previous/next navigation when applicable; and
- a clear no-results message that does not imply the project or its files do not exist.

An exact project-number query that matches multiple records must display all matching project rows. It must not select an arbitrary row or redirect to `/project_info?project_number=...`.

## Search semantics

### Fields searched

Project-local fields:

- `projects.number`;
- `projects.name`;
- `projects.campus_client`;
- `projects.project_manager_name`;
- `projects.inspector_name`;
- `projects.file_server_location`, at a lower weight; and
- `projects.notes`, at the lowest weight.

Related CAAN fields:

- `caans.caan`;
- `caans.name`; and
- `caans.description`.

Related contract fields:

- `contracts.contract_number`;
- `contracts.contractor_org_name`;
- `contracts.executive_design_org_name`;
- `contracts.funding_number`; and
- `contracts.scope_description`.

Financial values and FileMaker primary IDs are not searched in the normal query.

### Query parsing

Normalize only harmless outer whitespace and case. Preserve meaningful project-number punctuation such as hyphens.

The preferred text-search behavior is PostgreSQL web-style parsing with the `simple` text configuration:

- plain words match all required terms;
- quoted words support phrases;
- `OR` broadens a term group; and
- a leading `-` excludes a term.

The implementation must separately retain exact and prefix project-number matching because full-text parsing alone is not a sufficient identifier search strategy.

If the first release chooses simpler whitespace-term parsing instead, it must preserve the same core semantics: AND across terms, OR across searchable fields, case-insensitive comparison, and exact-number ranking.

CAAN Search retains its current whitespace-term behavior in the first project-search release. Aligning CAAN and project query syntax is deferred until there is a reason to change the existing CAAN workflow.

### Relationship matching

A project matches when every required query term can be satisfied by at least one project-local field or directly related CAAN/contract field.

Use relationship predicates that return project IDs, rather than joining every CAAN and contract row into the result set. This prevents duplicate project rows and keeps pagination correct.

The result should expose which broad source produced the match:

- Project;
- CAAN;
- Contract;
- Notes; or
- Archive root.

The match-source label is explanatory only; it must not expose full note or contract-scope text.

### Filters

`status` maps to `projects.closed`:

- `open`: `closed = false`;
- `closed`: `closed = true`;
- `unknown`: `closed IS NULL`;
- `any`: no predicate.

`drawings` maps similarly to `projects.drawings`.

`has_archive_location=yes` means a non-null, non-blank `file_server_location`.

`has_archive_location=no` means null or blank `file_server_location`.

The `caan` filter must use the unique CAAN business code and the direct `project_caans` relationship. It must not infer a CAAN from an archive path or project-number prefix.

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
6. pagination;
7. result-row preparation; and
8. user-facing search-state metadata.

The route should remain thin:

1. parse and validate the request;
2. call the search service;
3. translate defined validation outcomes to HTTP responses; and
4. render the template.

Do not generate raw HTML tables from database content. Let Jinja escape all database-backed values.

### ORM and SQL strategy

Use SQLAlchemy for ordinary filters and relationship existence checks. PostgreSQL-specific full-text ranking may use parameterized SQL expressions or a carefully bounded text query when ORM construction becomes unclear.

The query must:

- select project rows as the primary result identity;
- avoid duplicate rows from CAAN/contract joins;
- use `EXISTS` or grouped subqueries for relationship matches;
- eager-load CAANs only for the returned page; and
- avoid one query per result row.

The project-information resolver should share normalization rules with project search. The existing `/api/project_location` legacy endpoint is not a suitable resolver because it uses a first-match, number-only lookup and should not be copied into the new feature.

### Template

Add:

`archives_application/templates/project_search.html`

The template should use `layout.html` and existing Bootstrap conventions. It should be responsive, HTML-escaped, and consistent with the CAAN and archive-search forms.

No filesystem calls, `ServerEdit`, RQ tasks, or database writes are permitted in this route.

## Database and indexing requirements

### Required baseline indexes

Add a migration in `business_services_db/alembic/versions/` for:

1. A normalized project-number B-tree index equivalent to:

`lower(btrim(projects.number))`

This supports project search, the existing case-insensitive project-information resolver, and future canonical-number lookups.

2. A reverse project-CAAN relationship index equivalent to:

`project_caans (caan_id, project_id)`

This supports CAAN filtering and the existing CAAN-to-project direction more efficiently than the current `(project_id, caan_id)` primary key alone.

The migration must be the source of truth. If application model metadata declares the indexes, it must remain aligned with the database migration, but the app must not rely on `db.create_all()` for production schema changes.

### Full-text index option

For the first implementation, use the current row counts and `EXPLAIN (ANALYZE, BUFFERS)` results to determine whether expression GIN indexes are needed immediately.

If text search requires them, add expression indexes using the same expressions used in the query:

- Projects: a `simple` tsvector over number, name, campus client, manager, inspector, archive root, and optionally notes.
- CAANs: a `simple` tsvector over CAAN code, name, description, area, and address.
- Contracts: a `simple` tsvector over contract number, contractor, design organization, funding number, and scope.

Relationship fields cannot be included in a generated `projects` search column without denormalizing synchronized data. Keep CAAN and contract matching as indexed relationship predicates unless a future materialized search document is justified.

### Trigram option

`pg_trgm` is not listed among the installed extensions in the supplied schema reference. Do not require it for the first release unless representative queries demonstrate that full-text search and exact/prefix matching cannot support expected usage.

If users need substring or typo-tolerant matching, add `pg_trgm` through a controlled migration and index only the normalized project number and name first. Do not add broad trigram indexes to every metadata column without query-plan evidence.

### Migration operations

Production index creation should use the database repository's controlled Alembic process. Large indexes should be created concurrently where supported, using the migration's autocommit requirements. Run `EXPLAIN (ANALYZE, BUFFERS)` against a production-like dataset before and after the migration.

No new `file_locations` index is required solely for project search.

## Archive Search integration

Project Search and Archive Search remain separate features, but they should have explicit handoff points.

### Project-to-file handoff

A project result may offer **Search files in this project**.

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
- support bounded pagination;
- reject unknown or repeated parameters; and
- return structured match-source values rather than HTML.

The API should not accept credentials in query parameters.

## Performance and operational constraints

- Default HTML page size: 50.
- Hard maximum page size: 100.
- Query length should be bounded.
- Search must remain synchronous for the first release; project metadata volume is small enough to validate this assumption.
- Do not build a full result DataFrame.
- Do not count `file_locations` per result.
- Do not load all CAANs or contracts for all matching projects.
- Use a stable final tie-breaker to prevent rows moving between pages.
- Log unexpected search failures, but do not log full project notes or contract scope text.

Before release, measure:

- exact normalized project-number lookup;
- common project-name searches;
- CAAN relationship filters;
- contract-field searches;
- status and archive-root filters; and
- duplicate-number searches.

## Security and data handling

- Escape project, CAAN, contract, notes, and archive-root values in templates.
- Do not render raw search snippets from notes or contract scope as trusted HTML.
- Do not expose FileMaker primary IDs in normal result rows.
- Do not expose full notes or financial fields in the search result list.
- Preserve the existing project-information access policy unless a separate authorization decision is made.
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
- CAAN relationship matches;
- contract relationship matches;
- status, drawings, and archive-root filters;
- null and blank archive-root handling;
- stable ordering and pagination;
- match-source labeling; and
- HTML escaping of database values.

### Database checks

Run `EXPLAIN (ANALYZE, BUFFERS)` for:

- normalized exact project-number lookup;
- prefix project-number lookup;
- common project-name search;
- CAAN-to-project filter;
- contract-field relationship search; and
- a duplicate project-number query.

Verify that the normalized number and reverse relationship indexes are used where expected. Decide separately whether full-text or trigram indexes are justified.

### Manual acceptance checks

1. `GET /project_search` renders the empty form.
2. A project-number query returns matching projects and links each row to its project ID URL.
3. A deliberately duplicated project number returns every matching project row.
4. A project-name query returns relevant projects with stable ordering.
5. CAAN and contract relationship matches do not duplicate project rows.
6. Status, drawings, CAAN, and archive-root filters work independently and together.
7. A project with no recorded root is labelled as missing a recorded root; no path is inferred.
8. Long names, notes, scopes, and path values remain escaped and wrapped.
9. Pagination does not repeat or skip rows when scores tie.
10. Project search does not query SMB, enqueue work, or write database rows.
11. A project-to-file handoff, when implemented, uses a project ID and does not silently resolve a duplicate number.
12. Python compilation, Jinja parsing, focused tests, and `git diff --check` pass.

## Recommended implementation phases

### Phase 0: Query and data preflight

- Confirm duplicate project-number frequency.
- Profile project, CAAN, and contract search fields.
- Run baseline query plans.
- Confirm the desired result columns with project managers and archivists.

### Phase 1: Dedicated project search

- Add the request parser and project search service.
- Add the `/project_search` route and template.
- Add canonical project-information links.
- Add bounded pagination and core filters.
- Preserve `/caan_search` unchanged.

### Phase 2: Baseline database indexes

- Add the normalized project-number index.
- Add the reverse `project_caans(caan_id, project_id)` index.
- Validate query plans after migration.

### Phase 3: Search quality and integration

- Add expression GIN or trigram indexes only when justified by measured queries.
- Add project-ID-based Archive Search handoff.
- Align project scope resolution with the canonical project resolver.

### Phase 4: Optional API

- Add authenticated `/api/project_search` only after the HTML semantics and pagination contract are stable.

## Deferred decisions

- Whether CAAN Search should eventually reuse the project-search service implementation internally.
- Whether project notes should be included in the default searchable fields or placed behind an advanced option.
- Whether contract scope should be searched by default or only through an advanced mode.
- Whether `pg_trgm` is necessary for partial and typo-tolerant matching.
- Whether the project search page should offer autocomplete.
- Whether a future global launcher should link to project, CAAN, and archive searches without combining their results.
