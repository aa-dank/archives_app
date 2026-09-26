# # archives_application/project_tools/routes.py

import html
import flask
import json
import re
import pandas as pd
from flask_login import current_user
from archives_application import db, bcrypt
from archives_application import utils
from archives_application.models import UserModel, ProjectModel, CAANModel, WorkerTaskModel
from archives_application.project_tools.forms import CAANSearchForm
from archives_application.project_tools import project_info as project_info_service
from archives_application.project_tools import project_search as project_search_service
from sqlalchemy import or_, and_


DEFAULT_TASK_TIMEOUT_SECONDS = 18000 # 5 hours

project_tools = flask.Blueprint('project_tools', __name__)


def project_directory_summary_link(location):
    """Build a safely escaped directory-summary link for a user-visible path."""
    summary_url = flask.url_for('archiver.dir_contents_summary', path=location)
    location_display = html.escape(location).replace(' ', '&nbsp;')
    return (
        f'<a href="{html.escape(summary_url, quote=True)}">'
        f'{location_display}</a>'
    )

def admin_request_user():
    """Return the authenticated admin and supplied password, if applicable.

    Explicit ``user`` and ``password`` request parameters take precedence over
    the current session.  Returns ``(user, password)`` for a verified admin,
    otherwise ``(None, password)`` for an attempted parameter login or
    ``(None, None)`` when no authenticated admin is available.
    """
    user_param = utils.FlaskAppUtils.retrieve_request_param("user", None)
    if user_param:
        password_param = utils.FlaskAppUtils.retrieve_request_param("password")
        user = UserModel.query.filter_by(email=user_param).first()
        if user and bcrypt.check_password_hash(user.password, password_param or "") and utils.FlaskAppUtils.has_admin_role(user):
            return user, password_param
        return None, password_param

    if current_user and current_user.is_authenticated and utils.FlaskAppUtils.has_admin_role(current_user):
        return current_user, None

    return None, None


def requested_projects_list():
    """Return requested project identifiers from singular and plural parameters.

    Combines ``project`` with comma-separated ``projects`` values, strips
    surrounding whitespace, omits blank entries, and returns ``None`` when
    neither parameter provides an identifier.
    """
    project_param = utils.FlaskAppUtils.retrieve_request_param("project", None)
    projects_param = utils.FlaskAppUtils.retrieve_request_param("projects", None)
    project_values = []
    if project_param:
        project_values.append(project_param)
    if projects_param:
        project_values.extend(projects_param.split(","))

    return [project.strip() for project in project_values if project.strip()] or None


@project_tools.route("/confirm_project_locations", methods=['GET', 'POST'])
def confirm_project_locations():
    """
    Enqueues a task that refreshes ``projects.file_server_location`` from the file server.

    Request parameters can be sent in either the URL or request headers:
        user: email of the user making the request when not already logged in
        password: password for the request user
        project: optional single project number to check
        projects: optional comma-separated project numbers to check
        timeout: maximum task runtime in seconds
    """
    from archives_application.project_tools.project_tools_tasks import confirm_project_locations_task

    try:
        user, _ = admin_request_user()
    except Exception as e:
        return utils.FlaskAppUtils.api_exception_subroutine("Error authenticating user permissions.", e)

    if not user:
        return flask.Response("Unauthorized", status=401)

    timeout_seconds = int(utils.FlaskAppUtils.retrieve_request_param("timeout") or DEFAULT_TASK_TIMEOUT_SECONDS)
    projects_list = requested_projects_list()
    task_info = {
        "projects": projects_list or "all",
        "user": user.email
    }
    task_kwargs = {"projects_list": projects_list}
    nq_kwargs = {"timeout": timeout_seconds}
    nq_results = utils.RQTaskUtils.enqueue_new_task(
        db=db,
        enqueued_function=confirm_project_locations_task,
        task_info=task_info,
        task_kwargs=task_kwargs,
        enqueue_call_kwargs=nq_kwargs
    )
    return flask.Response(json.dumps(utils.serializable_dict(nq_results)), status=200)


@project_tools.route("/test/confirm_project_locations", methods=['GET', 'POST'])
def test_confirm_project_locations():
    """Runs the project location confirmation task synchronously for admin testing."""
    from datetime import datetime
    from archives_application.project_tools.project_tools_tasks import confirm_project_locations_task

    user, _ = admin_request_user()
    if not user:
        return flask.Response("Unauthorized", status=401)

    projects_list = requested_projects_list()
    confirm_job_id = f"{confirm_project_locations_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
    confirm_task_record = WorkerTaskModel(task_id=confirm_job_id,
                                          time_enqueued=str(datetime.now()),
                                          origin="test",
                                          function_name=confirm_project_locations_task.__name__,
                                          status="queued")
    db.session.add(confirm_task_record)
    db.session.commit()
    results = confirm_project_locations_task(queue_id=confirm_job_id, projects_list=projects_list)
    return flask.Response(json.dumps(utils.serializable_dict(results)), status=200)

@project_tools.route("/caan_search", methods=['GET', 'POST'])
def caan_search():
    """CAAN search endpoint.

    Provides a web form for users to locate CAAN records (by number, name, or description) and optionally
    jump directly to the info view for a specific CAAN. The endpoint supports both initial form
    rendering (GET) and search submission (POST).

    Workflow:
            1. User visits the page (GET) and is shown a form with two inputs:
                    - ``enter_caan``: Exact CAAN value. If supplied on submit, user is redirected immediately to
                        the corresponding ``/caan_info/<caan>`` page without running a broader search.
                    - ``search_query``: Free‑text terms separated by whitespace. Each term is matched (case‑insensitive)
                        against CAAN number, name, OR description. All terms must match at least one of the three
                        fields (logical AND across terms; logical OR across fields per term).
                2. If only ``search_query`` is supplied, a filtered result set is produced and rendered in
                    ``caan_search_results.html``. Each CAAN in the results links to its info page.
            3. If no matches are found, the form is re-rendered with an informational flash message.

    Form Fields (``CAANSearchForm``):
            enter_caan (StringField): Optional direct navigation shortcut.
            search_query (StringField): Space‑delimited search terms; required unless ``enter_caan`` provided.
            submit (SubmitField): Triggers search or redirect.

    Returns:
            - GET: Renders ``caan_search.html`` with empty form.
            - POST (exact CAAN provided): Redirect to ``project_tools.caan_info``.
            - POST (search terms): Renders ``caan_search_results.html`` with ``table_list`` (list of dicts:
                ``caan``, ``name``, ``description``) and original ``query`` string.
            - POST (no results): Re-renders ``caan_search.html`` with flash message.
            - On exception: Redirect via ``web_exception_subroutine`` with an error flash.

    Notes:
            - A safety result limit could be added in future if the CAAN table grows large.
            - For more advanced relevance ranking, consider migrating to full‑text search (e.g., PostgreSQL
                ``to_tsvector``) if performance becomes an issue.
    """
    form = CAANSearchForm()
    if form.validate_on_submit():
        try:
            # direct navigation if an exact CAAN provided
            if form.enter_caan.data:
                return flask.redirect(flask.url_for('project_tools.caan_info', caan=form.enter_caan.data.strip()))

            if not form.search_query.data:
                raise ValueError("Missing search query")

            raw_query = form.search_query.data.strip()
            terms = [t for t in raw_query.split() if t]

            base_query = CAANModel.query
            if terms:
                term_filters = []
                for term in terms:
                    pattern = f"%{term}%"
                    term_filters.append(
                        or_(
                            CAANModel.caan.ilike(pattern),
                            CAANModel.name.ilike(pattern),
                            CAANModel.description.ilike(pattern)
                        )
                    )
                base_query = base_query.filter(and_(*term_filters))

            results = (base_query
                       .order_by(CAANModel.caan.asc())
                       .all())

            if not results:
                flask.flash("No results found", "info")
                return flask.render_template('caan_search.html', form=form)

            table_list = [
                {
                    'caan': r.caan,
                    'name': r.name or '',
                    'description': r.description or ''
                } for r in results
            ]
            return flask.render_template('caan_search_results.html', form=form, table_list=table_list, query=raw_query)
        except Exception as e:
            return utils.FlaskAppUtils.web_exception_subroutine(
                flash_message="CAAN Search Failed",
                thrown_exception=e,
                app_obj=flask.current_app
            )

    return flask.render_template('caan_search.html', form=form)


@project_tools.route("/project_search", methods=["GET"])
def project_search():
    """Render the public, read-only project metadata search page."""
    try:
        state = project_search_service.parse_request(flask.request.args)
        results, has_more = project_search_service.search_html(state)
        return flask.render_template(
            "project_search.html",
            title="Project Search",
            state=state,
            results=results,
            has_more=has_more,
        )
    except project_search_service.ProjectSearchValidationError as error:
        return flask.Response(str(error), status=400)
    except Exception:
        flask.current_app.logger.error("Project search request failed", exc_info=True)
        return flask.Response("Unable to search projects.", status=500)


@project_tools.route("/project_search/export", methods=["GET"])
def project_search_export():
    """Download all public project-search matches as a flattened XLSX workbook."""
    try:
        state = project_search_service.parse_request(flask.request.args)
        if not state.is_active:
            raise project_search_service.ProjectSearchValidationError(
                "Supply a search query or at least one active filter."
            )
        workbook = project_search_service.build_export_workbook(
            state,
            flask.current_app.config.get("USER_ARCHIVES_LOCATION"),
        )
        return flask.send_file(
            workbook,
            as_attachment=True,
            download_name="project_search.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except project_search_service.ProjectSearchValidationError as error:
        return flask.Response(str(error), status=400)
    except Exception:
        flask.current_app.logger.error("Project search export failed", exc_info=True)
        return flask.Response("Unable to export project search results.", status=500)


@project_tools.route("/caan_info/<caan>", methods=['GET'])
def caan_info(caan):
    """
    Endpoint for displaying details and associated projects for a given CAAN.

    This endpoint retrieves and displays CAAN details plus all projects associated with the CAAN.
    It includes a "Drawings?" status column from the project record and links each root
    project folder path to its directory-contents summary.

    Path Parameters:
        caan (str): The CAAN identifier for which to retrieve project and metadata details.

    Returns:
        Response: Renders the 'caan_info.html' template with CAAN metadata and a table of associated projects.
                  Returns a 404 response if the CAAN is not found or if no projects are associated with the CAAN.
    """

    def project_number_sort_key(project_number):
        # Split alpha and numeric chunks so mixed values sort more naturally (e.g. 6300-7A before 6300-11).
        number_str = str(project_number) if project_number is not None else ""
        return tuple(
            (0, int(chunk)) if chunk.isdigit() else (1, chunk.lower())
            for chunk in re.split(r'(\d+)', number_str)
            if chunk
        )

    def drawings_label(drawings_value):
        if pd.isnull(drawings_value):
            return "UNKNOWN"
        return "Yes" if bool(drawings_value) else "No"

    def project_root_location(project_location, network_location):
        # SQL NULL values are represented as numpy.nan after the project query is
        # converted to a DataFrame.  ``bool(numpy.nan)`` is True, so a normal
        # truthiness check would otherwise render the misleading path ending in
        # ``\\nan``.
        if pd.isna(project_location) or not isinstance(project_location, str) or not project_location.strip():
            return "Not recorded in database"

        if not network_location:
            return None

        return utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=project_location,
            user_archives_location=network_location
        )

    def linked_project_root_location(project_location, network_location):
        """Return a directory-summary link for a recorded project location."""
        location = project_root_location(project_location, network_location)
        if location is None:
            # Preserve the existing table's fallback when the user archive mount
            # is unavailable in configuration.
            return "UNKNOWN"
        if location == "Not recorded in database":
            return location

        return project_directory_summary_link(location)

    def project_info_link(project_id, project_number):
        """Build a canonical project-detail link without trusting table content."""
        project_url = flask.url_for('project_tools.project_info', project_id=int(project_id))
        return (
            f'<a href="{html.escape(project_url, quote=True)}">'
            f'{html.escape(str(project_number))}</a>'
        )
    
    try:
        # check if the caan value exists in the database
        caan_query = CAANModel.query.filter(CAANModel.caan == caan)
        if caan_query.count() == 0:
            return flask.Response(f"CAAN {caan} not found in database.", status=404)

        # get all projects for a caan
        caan_projects_query = ProjectModel.query.filter(ProjectModel.caans.any(CAANModel.caan == caan))
        caan_projects_df = utils.FlaskAppUtils.db_query_to_df(query=caan_projects_query)

        # if there are no projects for the caan, return a 404
        if caan_projects_df.empty:
            return flask.Response(f"No projects found for CAAN {caan}.", status=404)

        row_root_location = lambda row: linked_project_root_location(
            project_location=row["file_server_location"],
            network_location=flask.current_app.config.get('USER_ARCHIVES_LOCATION')
        )
        html_col_widths = {"Number": "10%", "Name": "33%", "Drawings?": "12%", "Location": "45%"}

        caan_projects_df["Drawings?"] = caan_projects_df["drawings"].apply(drawings_label)
        caan_projects_df["_drawings_rank"] = caan_projects_df["Drawings?"].map({"Yes": 0, "UNKNOWN": 1, "No": 2})
        caan_projects_df["_number_sort_key"] = caan_projects_df["number"].apply(project_number_sort_key)
        caan_projects_df["Number"] = caan_projects_df.apply(
            lambda row: project_info_link(row["id"], row["number"]), axis=1
        )
        caan_projects_df["Location"] = caan_projects_df.apply(row_root_location, axis=1)

        caan_projects_df.sort_values(by=["_drawings_rank", "_number_sort_key"], inplace=True)
        projects_table_df = caan_projects_df[["Number", "name", "Drawings?", "Location"]]
        projects_table_df.columns = ["Number", "Name", "Drawings?", "Location"]
        # ``html_columns`` disables pandas' table-wide escaping so the location
        # links render. Escape the remaining database-backed columns explicitly.
        for column in ["Name", "Drawings?"]:
            projects_table_df[column] = projects_table_df[column].apply(
                lambda value: html.escape(str(value))
            )
        projects_html = utils.html_table_from_df(
            df=projects_table_df,
            html_columns=["Number", "Location"],
            column_widths=html_col_widths
        )
        
        # retrieve caan data
        caan = CAANModel.query.filter(CAANModel.caan == caan).first()

        return flask.render_template(
            'caan_info.html',
            caan=caan.caan,
            caan_name=caan.name,
            caan_description=caan.description,
            caan_address_street=caan.address_street,
            caan_address_city=caan.address_city,
            caan_address_zip=caan.address_zip,
            caan_area=caan.area,
            projects_table=projects_html
        )
    except Exception as e:
        return utils.FlaskAppUtils.api_exception_subroutine(
            response_message="Error retrieving CAAN information:",
            thrown_exception=e
        )


@project_tools.route("/project_info", methods=['GET'])
def project_info():
    """Render one project's business, CAAN, contract, and archive-index details.

    The route accepts exactly one query selector: a positive ``project_id`` or
    an exact case-insensitive ``project_number``. Number lookups redirect to
    the canonical ID URL and refuse ambiguous numbers rather than choosing an
    arbitrary project. The page is read-only and makes no filesystem calls.
    """
    try:
        selector = project_info_service.parse_selector(flask.request.args)
        project = project_info_service.resolve_project(selector)
        if selector.kind == "project_number":
            return flask.redirect(
                flask.url_for('project_tools.project_info', project_id=project.id),
                code=302,
            )
        context = project_info_service.project_context(
            project=project,
            user_archives_location=flask.current_app.config.get('USER_ARCHIVES_LOCATION'),
        )
        return flask.render_template(
            'project_info.html',
            title=f"Project {project.number}: {project.name}",
            **context,
        )
    except project_info_service.ProjectInfoValidationError as error:
        return flask.Response(str(error), status=400)
    except project_info_service.ProjectInfoNotFoundError as error:
        return flask.Response(str(error), status=404)
    except project_info_service.ProjectInfoAmbiguousNumberError as error:
        return flask.Response(str(error), status=409)
    except Exception as error:
        return utils.FlaskAppUtils.api_exception_subroutine(
            response_message="Error retrieving project information:",
            thrown_exception=error,
        )


def _project_info_api_error(status_code: int, message: str):
    """Return the consistent JSON error representation for project-information API calls."""
    return flask.jsonify({"error": message}), status_code


@project_tools.route("/api/project_info", methods=["GET"])
def project_info_api():
    """Return authenticated JSON project, CAAN, contract, and archive-index data.

    ``GET /api/project_info`` is the programmatic counterpart to the HTML
    ``/project_info`` page. It resolves exactly one project, returns its direct
    CAAN and contract relationships, and reports the recorded archive root plus
    one database-indexed file-location count. It is read-only: it performs no
    SMB/file-server I/O, creates no records, and enqueues no background work.

    Authentication:
        An active application user is required. A caller may authenticate with
        an existing application session or HTTP Basic credentials in the
        ``Authorization`` header. Basic credentials are the user's application
        email and password; deployments must protect such requests with HTTPS.
        Credentials are never accepted as query parameters. Missing or invalid
        credentials return ``401 Unauthorized``. This first version permits
        every authenticated active user to retrieve project data, including
        contract financial values and synchronized project notes.

    Query parameters:
        Supply exactly one selector:

        - ``project_id`` (positive integer): the canonical ``projects.id``.
        - ``project_number`` (string): an exact case-insensitive
          ``projects.number`` match after harmless outer whitespace is trimmed.

        The selectors are mutually exclusive. ``project_number`` requests do
        not redirect: successful API responses always contain the canonical ID
        at ``project.id``, which clients should retain for future requests.
        Project numbers are not schema-unique, so an ambiguous number returns
        ``409 Conflict`` rather than selecting an arbitrary row.

        Optional boolean values accept case-insensitive ``true`` or ``false``:

        - ``include_user_path`` (default ``false``): When ``true``, add
          ``archives.user_path`` using the configured
          ``USER_ARCHIVES_LOCATION``. It is ``null`` when no recorded root or
          user archive mount is available. When ``false``, that key is omitted;
          the database-relative ``archives.database_root`` remains available.

        Unknown, blank, repeated, or malformed parameters return ``400 Bad
        Request``. Query parameter names are case-sensitive. Do not use a URL
        path segment for project numbers because their punctuation and future
        formats are not constrained to a simple identifier.

    Response schema:
        A successful request returns ``200 OK`` JSON with top-level
        ``project``, ``archives``, ``caan_count``, ``caans``,
        ``contract_count``, and ``contracts`` keys. ``project`` contains the
        canonical ID, synchronized project fields including ``notes``,
        ``inspector_name``, and ``project_manager_name``, plus ISO-8601
        ``last_synced_at``. FileMaker primary IDs are implementation details
        and are intentionally not exposed.

        ``archives.database_root`` is the normalized Records-relative stored
        directory or ``null``; ``root_recorded`` distinguishes that state.
        ``indexed_file_location_count`` is a single count of indexed
        ``file_locations`` at that root or beneath its directory boundary. It
        is not a live filesystem inventory, and it is ``null`` when no root is
        recorded. A count of zero means no indexed paths are currently
        recorded, not that the directory is known to be empty.

        ``caans`` is naturally sorted by CAAN code and contains each direct
        relationship's ID, code, name, and description. ``contracts`` is
        sorted by contract number then ID and always contains every direct
        ``contracts.project_id`` relationship, including when there is only
        one or there are many. Long scope descriptions are returned in full;
        the HTML page's disclosure presentation does not apply to JSON.

        Each contract includes identity/party fields, a ``financials`` object,
        a ``schedule`` object, and ``last_synced_at``. Monetary values are
        decimal strings such as ``"13587.39"`` rather than JSON floats, so
        clients do not lose precision. Dates use ``YYYY-MM-DD`` and timestamps
        use ISO-8601; database nulls are JSON ``null``. The schedule contains
        raw synchronized dates and durations plus three supplemental checks:
        ``duration_reconciles`` tests original duration plus change-order time
        against revised duration, ``expected_end_reconciles`` tests NTP plus
        revised duration against the recorded expected end, and
        ``actual_vs_expected_days`` compares actual NOC completion with that
        recorded expected end. These fields never substitute derived values for
        the synchronized source values. Deprecated contract account numbers
        and FileMaker primary IDs are not returned.

    Errors:
        - ``400``: Invalid selector or query parameter.
        - ``401``: Missing or invalid authentication.
        - ``404``: No project matches a valid selector.
        - ``409``: More than one project has the supplied project number.
        - ``500``: Unexpected lookup or serialization failure.

        Every successful response sends ``Cache-Control: private, no-store``
        because project, archive-path, and contract information can be
        sensitive.
    """
    if utils.FlaskAppUtils.authenticate_active_api_user() is None:
        return _project_info_api_error(401, "Unauthorized.")

    try:
        selector, include_user_path = project_info_service.parse_api_request(
            flask.request.args
        )
        project = project_info_service.resolve_project(selector)
        api_data = project_info_service.project_api_data(
            project=project,
            user_archives_location=flask.current_app.config.get(
                "USER_ARCHIVES_LOCATION"
            ),
            include_user_path=include_user_path,
        )
        response = flask.jsonify(api_data)
        response.headers["Cache-Control"] = "private, no-store"
        return response
    except project_info_service.ProjectInfoValidationError as error:
        return _project_info_api_error(400, str(error))
    except project_info_service.ProjectInfoNotFoundError as error:
        return _project_info_api_error(404, str(error))
    except project_info_service.ProjectInfoAmbiguousNumberError as error:
        return _project_info_api_error(409, str(error))
    except Exception:
        flask.current_app.logger.error(
            "Project information API request failed", exc_info=True
        )
        return _project_info_api_error(500, "Unable to retrieve project information.")


@project_tools.route("/api/project_location", methods=['GET', 'POST'])
def project_location():
    """
    API endpoint to retrieve the file server location for a given project.
    
    Request Parameters:
        project (str): The project number to look up. GET requests accept the
            existing query/header parameters. POST requests also accept
            ``project`` in either form data or a JSON object.
        
    Returns:
        Response: JSON response containing the project location path.
                 Returns 404 if project is not found.
                 Returns 400 if project parameter is missing.
    """
    
    try:
        project_num = utils.FlaskAppUtils.retrieve_request_param("project")
        
        if not project_num:
            return flask.Response("Missing required parameter: project", status=400)
        
        # Query the database for the project
        project = ProjectModel.query.filter_by(number=project_num).first()
        
        if not project:
            return flask.Response(f"Project {project_num} not found in database.", status=404)
        
        if not project.file_server_location:
            return flask.Response(f"No file server location found for project {project_num}.", status=404)
        
        # Convert database path to user-friendly path
        user_archives_location = flask.current_app.config.get('USER_ARCHIVES_LOCATION')
        user_path = utils.FileServerUtils.user_path_from_db_data(
            file_server_directories=project.file_server_location,
            user_archives_location=user_archives_location
        )
        
        response_data = {
            "project_number": project.number,
            "project_name": project.name,
            "file_server_location": user_path
        }
        
        return flask.Response(json.dumps(response_data), status=200)
        
    except Exception as e:
        return utils.FlaskAppUtils.api_exception_subroutine(
            response_message="Error retrieving project location:",
            thrown_exception=e
        )
