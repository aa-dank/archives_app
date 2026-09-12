# Project Information Page Specification

Last updated: 2026-08-31

## Purpose and first-release boundary

Add a read-only HTML page that gives a user one authoritative view of a project's business data, CAAN relationships, contract data, and recorded archive root. It is the detail destination for a project selected from a CAAN page or supplied by an external link.

This first release intentionally does not provide a project search page, a JSON API, a file listing, server-change history, or archive-summary aggregation. It does provide one indexed-file-location count for a recorded project root. The page must not otherwise scan or enumerate a project's file_locations: that table has about one million rows.

The endpoint only reads PostgreSQL data. It must never access the SMB share, create a ServerEdit, write database rows, or enqueue an RQ job.

## Route and selector contract

~~~
GET /project_info?project_id={positive-integer}
GET /project_info?project_number={URL-encoded-project-number}
~~~

The route belongs to the existing project_tools blueprint. It accepts exactly one selector:

| Parameter | Meaning |
| --- | --- |
| project_id | The canonical projects.id primary key. |
| project_number | A human-entered projects.number value. |

project_id is the canonical identifier. A successful project_number lookup redirects with 302 Found to the corresponding /project_info?project_id={id} URL. This makes copied links stable even if a project number is later corrected, while still allowing a user or another page to start with the familiar number.

project_number is trimmed and matched exactly, case-insensitively, against the stored number. It is not a substring search and must not fall back to a file-path search. Normalize only harmless outer whitespace; do not silently remove meaningful punctuation such as the hyphen in 1200-032.

The route accepts no other query parameters. Missing selectors, both selectors, repeated selectors, empty values, an invalid/non-positive ID, and unknown query parameters return 400 Bad Request.

### Selector outcomes

| Lookup result | HTTP response | User-facing result |
| --- | --- | --- |
| One project by project_id | 200 OK | Render the detail page. |
| No project by project_id | 404 Not Found | “Project ID {id} was not found.” |
| One project by project_number | 302 Found | Redirect to the canonical ID URL. |
| No project by project_number | 404 Not Found | “Project number {number} was not found.” |
| More than one project by project_number | 409 Conflict | Explain that the number is ambiguous and that a project ID is required. Do not choose a row. |

Although duplicate numbers are expected to be rare, the schema does not make projects.number unique. The 409 result is therefore required rather than using the legacy ProjectModel.query.filter_by(number=...).first() pattern.

The first release follows the access behavior of the existing CAAN detail page. Contract costs, funding numbers, and account numbers are intentionally included in the normal single-contract display; they do not require a separate role restriction.

## Data retrieval and relationship rules

Retrieve the selected ProjectModel and eager-load its CAANs and contracts so the page does not issue one query per related item. The relationship semantics shown to users are:

| Section | Source | Meaning |
| --- | --- | --- |
| Project data | projects | Canonical project record synchronized from FileMaker. |
| CAANs | projects → project_caans → caans | Direct many-to-many building/funding relationships. |
| Contract data | contracts.project_id → projects.id | Direct FileMaker-synchronized contract relationship. |
| Archive root | projects.file_server_location | Records-relative directory, maintained by project-location confirmation. It is not a file record. |
| Indexed file count | file_locations under the recorded archive root | Count of indexed file paths, not a live filesystem inventory. |

file_server_location uses forward slashes and is relative to the Records root. Display it only after converting it with FileServerUtils.user_path_from_db_data(...) and the configured USER_ARCHIVES_LOCATION. Never concatenate a user-supplied path or perform filesystem I/O in this route.

The page must not infer a folder from the project number when file_server_location is null. This is particularly important for sub-projects: a missing recorded root is a data state, not proof that files do not exist.

### Indexed file-location count

When a non-empty archive root is recorded, retrieve exactly one aggregate: COUNT(file_locations.id) for locations at that root or in one of its descendants. A file_locations row represents one indexed path on the file server, so this is the count users expect as files in the project directory. The same canonical file hash in two separate paths counts twice, because two files exist at two locations.

Use a path-boundary predicate, not a broad contains match:

~~~sql
file_server_directories = :root
OR file_server_directories LIKE :escaped_root || '/%' ESCAPE '\'
~~~

Normalize the stored root to Records-relative forward-slash form, remove a harmless trailing slash, and escape literal percent, underscore, and backslash characters before constructing the LIKE pattern. The predicate must share the existing archive-search root/descendant semantics so, for example, a root of 1200 never also counts 12001.

The user-facing label is **Indexed files in this project directory**. Its description must make clear that it is a database-index count, not a live SMB check. A recorded root with a count of zero shows “No indexed files are currently recorded under this project location”; it must not say the directory is empty. A missing root does not execute the count query and shows “Indexed file count unavailable because no project root location is recorded.”

Use a single aggregate query from the project-info helper; never load file rows into Python or issue a separate query for each subdirectory. Before release, run EXPLAIN ANALYZE against a production-like data set. If the count needs a prefix index, add one through the database migration process before relying on this page at normal traffic. A case-insensitive count should use a matching lower-case expression index; do not add an unindexed ILIKE predicate without measuring it.

## Contract rule: zero, one, or multiple

Contract presentation deliberately optimizes for the normal one-contract case. It must use the number of direct contracts.project_id relationships, not a heuristic match on contract number, project name, or file path.

| Direct contract count | Page behavior |
| --- | --- |
| 0 | Show “No linked contract record.” |
| 1 | Show the single contract section and its fields. |
| 2+ | Show one horizontally scrollable, grouped table with one row per direct contract record. |

For multiple contracts, sort rows by contract number where practical and expose the synchronized identity/party, financial, schedule-date, and duration fields in the table. Use an em dash for a null value; never pick an arbitrary contract as the single detailed record. Do not render individual schedule bars, consistency warnings, or supplementary-date tables in this state, because those views are specific to one contract. The table may use a desktop horizontal scroll wrapper rather than squeezing its columns.

## Page layout

Use layout.html and the existing Bootstrap conventions. The page title is “Project {number}: {name}.” Long values must wrap rather than force a horizontal page scroll; only narrow data tables may use a responsive wrapper.

### 1. Header and project facts

At the top of the content section, render:

- project number and name;
- project status: Open, Closed, or Unknown from closed;
- drawings status: Yes, No, or Unknown from drawings;
- campus client when present; and
- last_synced_at when present, labelled “Business data last synchronized.”

Database IDs and FileMaker primary IDs are implementation identifiers and are not part of the normal display. They may remain available in the URL or a future authenticated API, but should not add visual noise to this page.

### 2. Archive root card

Render a clearly labelled **Archives location** card immediately after the project facts.

When a root is recorded and a user archive mount is configured, show the converted, copyable user path and a **Directory contents summary** link to the existing archiver.dir_contents_summary endpoint using that user path.

When no root is recorded, show:

> No project root location is recorded in the archive database. This does not establish that the project has no files.

When the root is recorded but a user archive mount is unavailable, show the root as unavailable for user-path display rather than exposing an incorrect or incomplete path.

### 3. Associated CAANs

Render an **Associated CAANs** section using a compact responsive table:

| CAAN | Name | Description |
| --- | --- | --- |

The CAAN value links to project_tools.caan_info. Sort by CAAN code using the existing natural-sort convention where practical. Show “No CAAN records are linked to this project.” when the relationship is empty. When more than 10 CAANs are linked, place the table in a collapsed native disclosure control labelled with the number of associated CAANs so the contract section remains quickly reachable; 10 or fewer remain visible without an extra interaction.

### 4. Contract

Render an **Associated contract** section after CAANs. Apply the zero/one/multiple rule above before rendering the template context. Use the plural heading when multiple direct contract records exist.

For exactly one contract, use a two-column definition-style layout, omitting null fields. Keep the fields grouped in this order:

1. **Identity and parties:** contract number, contractor, executive design organization, scope description.
2. **Financials:** cost estimate, original contract cost, change-order total, revised total (including change orders), and funding number.
3. **Schedule:** original project duration, change-order time total, and revised duration, as described below.

Format currency as dollars with grouping and two decimal places. Format dates as readable calendar dates while preserving null as omitted. The page is a display of synchronized data, not a calculation tool: it must not derive completion dates, revised totals, or durations.

### 5. Contract schedule overview

For exactly one linked contract, render a **Contract schedule** section beneath the contract facts. It describes the contractual schedule clock, not a generic timeline of every contract date.

Use `ntp_start_date` as the schedule start and the recorded `change_order_revised_expected_end` as the current expected end. When both are available, render a horizontal schedule bar with those dates as anchors. When the raw duration fields reconcile and contain non-negative values, split the bar proportionally into the original contractual duration and approved change-order time; otherwise render one current-duration segment when possible. The component values must always remain visible as text:

When the bar is split, place matching blue and amber swatches beside the original-duration and approved-change-order-time labels below it. These labels act as the visible legend; leave the current-duration total uncolored because it represents both portions.

| Display value | Source |
| --- | --- |
| Original contract duration | original_project_duration |
| Approved change-order time | change_order_time_total |
| Current contractual duration | change_order_revised_duration |

Show `noc_completion_date` as **Actual recorded completion** when present. When both it and the recorded expected end are available, show the calculated before/on/after variance in calendar days. This is an explanatory comparison, not a replacement completion-date calculation.

Perform transparent consistency checks only when all required source values exist: warn when original duration plus approved change-order time differs from current duration, or when Notice-to-Proceed plus current duration differs from the recorded expected end. Do not substitute a calculated value for a recorded source field. If either schedule anchor is absent, state that the schedule span is unavailable while still showing any recorded duration values.

Render `bid_date`, `contract_date`, beneficial occupancy, substantial completion, certificate of occupancy, notice-of-completion recorded, and termination dates in a compact chronological **Other recorded contract dates** table. This table is supplementary and must not present those dates as one inferred schedule. The multiple-contract state uses its grouped all-contract table instead of single-contract schedule data or other-date sections.

The future project-file date histogram is explicitly separate: it will need indexed file/date-mention aggregation and should be labelled as extracted document mentions, not contract schedule data. It may be added after its coverage, aggregation cost, and user value have been evaluated.

## Integration changes

After this route exists, change the CAAN detail page’s project-number column into a link to:

~~~
url_for('project_tools.project_info', project_id=row.id)
~~~

The CAAN query/table data must therefore retain each project's database id. Use the ID link even though the visible label remains the project number; this avoids duplicate-number ambiguity during ordinary navigation.

The current GET/POST /api/project_location endpoint remains unchanged and is out of scope. Its number-based .first() lookup is legacy behavior and must not be copied into the new route.

## Implementation shape

Add a small read-only helper, for example archives_application/project_tools/project_info.py, responsible for:

1. validating and resolving the mutually exclusive selectors;
2. loading the project plus CAAN and contract relationships;
3. computing the contract display state: none, single, or multiple;
4. turning the stored archive root into a user-facing path; and
5. retrieving the one path-boundary indexed-file-location count when a root exists;
6. building the single-contract schedule overview and supplementary date data when applicable; and
7. providing template-safe, presentation-neutral data to the route.

The route should be thin: validate the request, call the helper, convert its defined lookup outcomes to responses, and render project_info.html. Template rendering must continue to HTML-escape project, CAAN, and contract values. Do not construct a raw HTML table from unescaped database content.

## Acceptance checks

1. GET /project_info?project_id=<existing-id> renders the selected project, direct CAANs, root-state card, indexed-file count, and correct contract state.
2. GET /project_info?project_number=<unique-number> redirects to its canonical ID URL.
3. A deliberately duplicated project number returns 409 and renders no project data.
4. Missing, both, repeated, blank, malformed, and unknown selectors return 400; unknown IDs/numbers return 404.
5. A project with zero contracts shows the zero state; one contract renders its fields, schedule overview, and supplementary dates; two or more contracts render one stable-order grouped table with one row per direct contract record and no single-contract schedule detail.
6. A complete schedule shows its NTP and recorded expected-end anchors, duration components, and actual-completion variance when available. Missing or inconsistent source values remain visible without inventing replacement dates.
7. A recorded root is converted through the configured user archive mapping and produces the exact-or-descendant indexed-file count. A null root never triggers inferred-path lookup, count query, or filesystem access.
8. A sibling/prefix path does not inflate the count, a zero count is not described as an empty directory, and a duplicate hash in two indexed paths counts as two files.
9. CAAN-page project links use a database ID and work when another project has the same number.
10. Unit-test coverage is deferred. Run Python compilation, Jinja parsing, and `git diff --check`.

## Deferred decisions

- A JSON GET /api/project_info counterpart.
- Project search and direct navigation by project number from the UI.
- File size/text coverage, file results, and any cached summaries beyond the single indexed-file-location count.
- The project-file date-mention histogram, including coverage disclosure, aggregation design, and whether it supersedes the contract milestone timeline.
- Contract-specific pages or a multiple-contract comparison interface.
- Archive activity based on archived_files or server_changes.
