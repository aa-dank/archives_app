# Project Information Page Specification

Last updated: 2026-08-28

## Purpose and first-release boundary

Add a read-only HTML page that gives a user one authoritative view of a project's business data, CAAN relationships, single-contract data, and recorded archive root. It is the detail destination for a project selected from a CAAN page or supplied by an external link.

This first release intentionally does not provide a project search page, a JSON API, an indexed-file count, a file listing, server-change history, or archive-summary aggregation. In particular, the page must not scan file_locations to count or enumerate a project's files: that table has about one million rows and its current indexes are not designed for cheap directory-prefix aggregations.

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

The first release follows the access behavior of the existing CAAN detail page. If contract costs, funding numbers, or account numbers later require a role restriction, add a named authorization policy before changing this endpoint; do not make only a subset of template fields disappear through ad-hoc template checks.

## Data retrieval and relationship rules

Retrieve the selected ProjectModel and eager-load its CAANs and contracts so the page does not issue one query per related item. The relationship semantics shown to users are:

| Section | Source | Meaning |
| --- | --- | --- |
| Project data | projects | Canonical project record synchronized from FileMaker. |
| CAANs | projects → project_caans → caans | Direct many-to-many building/funding relationships. |
| Contract data | contracts.project_id → projects.id | Direct FileMaker-synchronized contract relationship. |
| Archive root | projects.file_server_location | Records-relative directory, maintained by project-location confirmation. It is not a file record. |

file_server_location uses forward slashes and is relative to the Records root. Display it only after converting it with FileServerUtils.user_path_from_db_data(...) and the configured USER_ARCHIVES_LOCATION. Never concatenate a user-supplied path or perform filesystem I/O in this route.

The page must not infer a folder from the project number when file_server_location is null. This is particularly important for sub-projects: a missing recorded root is a data state, not proof that files do not exist.

## Contract rule: zero, one, or multiple

Contract presentation deliberately optimizes for the normal one-contract case. It must use the number of direct contracts.project_id relationships, not a heuristic match on contract number, project name, or file path.

| Direct contract count | Page behavior |
| --- | --- |
| 0 | Show “No linked contract record.” |
| 1 | Show the single contract section and its fields. |
| 2+ | Do not display any contract field or contract row. Show: “Multiple linked contract records are present. Contract details are not displayed on this page.” |

The multiple-contract message may include the count, but must not expose a partial contract, pick an arbitrary first row, or introduce tabs, accordions, or a contract-comparison UI. A future contract-specific view can address that exceptional workflow without complicating the project page.

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

Do not report a file count, folder size, indexing status, or an “empty” conclusion in this first release.

### 3. Associated CAANs

Render an **Associated CAANs** section using a compact responsive table:

| CAAN | Name | Description | Area |
| --- | --- | --- | --- |

The CAAN value links to project_tools.caan_info. Sort by CAAN code using the existing natural-sort convention where practical. Show “No CAAN records are linked to this project.” when the relationship is empty.

### 4. Contract

Render an **Associated contract** section after CAANs. Apply the zero/one/multiple rule above before rendering the template context.

For exactly one contract, use a two-column definition-style layout, omitting null fields. Keep the fields grouped in this order:

1. **Identity and parties:** contract number, contractor, executive design organization, scope description.
2. **Financials:** cost estimate, original contract cost, change-order total, change-order revised cost, account number, funding number.
3. **Dates:** bid, contract, notice-to-proceed, beneficial occupancy, substantial completion, certificate of occupancy, notice-of-completion, notice-of-completion recorded, termination, and revised expected end.
4. **Duration:** original project duration, change-order time total, revised duration.

Format currency as dollars with grouping and two decimal places. Format dates as readable calendar dates while preserving null as omitted. The page is a display of synchronized data, not a calculation tool: it must not derive completion dates, revised cost, or durations.

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
5. providing template-safe, presentation-neutral data to the route.

The route should be thin: validate the request, call the helper, convert its defined lookup outcomes to responses, and render project_info.html. Template rendering must continue to HTML-escape project, CAAN, and contract values. Do not construct a raw HTML table from unescaped database content.

## Acceptance checks

1. GET /project_info?project_id=<existing-id> renders the selected project, direct CAANs, root-state card, and correct contract state.
2. GET /project_info?project_number=<unique-number> redirects to its canonical ID URL.
3. A deliberately duplicated project number returns 409 and renders no project data.
4. Missing, both, repeated, blank, malformed, and unknown selectors return 400; unknown IDs/numbers return 404.
5. A project with zero contracts shows the zero state; one contract renders its fields; two contracts exposes no contract detail and shows the multiple state.
6. A recorded root is converted through the configured user archive mapping; a null root never triggers inferred-path lookup or filesystem access.
7. CAAN-page project links use a database ID and work when another project has the same number.
8. Focused tests cover selector validation, duplicate handling, contract display-state calculation, path conversion, and HTML escaping. Run the focused tests, Python compilation, Jinja parsing, and git diff --check.

## Deferred decisions

- A JSON GET /api/project_info counterpart.
- Project search and direct navigation by project number from the UI.
- Indexed file counts, size/text coverage, file results, and directory-prefix indexes or cached summaries needed to make those measurements efficient.
- Contract-specific pages or a multiple-contract comparison interface.
- Archive activity based on archived_files or server_changes.
- A separate authorization policy for financial/accounting contract fields.
