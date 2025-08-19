# # archives_application/project_tools/routes.py

import flask
import json
import os
import numpy as np
import pandas as pd
import traceback
from flask_login import login_required, current_user
from archives_application import db, bcrypt
from archives_application import utils
from archives_application.models import *


FILEMAKER_API_VERSION = 'v1'
FILEMAKER_CAAN_LAYOUT = 'caan_table'
FILEMAKER_PROJECTS_LAYOUT = 'projects_table'
FILEMAKER_PROJECT_CAANS_LAYOUT = 'caan_project_join'
FILEMAKER_TABLE_INDEX_COLUMN_NAME = 'ID_Primary'
VERIFY_FILEMAKER_SSL = False
DEFAULT_TASK_TIMEOUT = 18000 # 5 hours

project_tools = flask.Blueprint('project_tools', __name__)



@project_tools.route("/fmp_reconciliation", methods=['GET', 'POST'])
def filemaker_reconciliation():
    """
    The purpose of this endpoint is to ensure that any changes made to the FileMaker database are reflected in the
    application database. This is done by comparing the FileMaker database to the application database and making
    changes to the application database as needed.
    Request parameters can be sent in either the url or request headers.
    Request parameters:
        user: email of the user making the request (Required)
        password: password of the user making the request (Required)
        confirm_locations: whether to confirm the locations of projects in the application database
        update_projects: whether to update the projects in the application database to match the FileMaker database
        timeout: the maximum time in seconds that the task is allowed to run

    :return: JSON response with the results of the reconciliation
    """
    
    from archives_application.project_tools.project_tools_tasks import fmp_caan_project_reconciliation_task

    # Check if the request includes user credentials or is from a logged in user. 
    # User needs to have ADMIN role.
    request_is_authenticated = False
    try:
        user_param = utils.FlaskAppUtils.retrieve_request_param("user")
        password_param = None
        if user_param:
            password_param = utils.FlaskAppUtils.retrieve_request_param("password")
            user = UserModel.query.filter_by(email=user_param).first()

            # If there is a matching user to the request parameter, the password matches and that account has admin role...
            if user \
                and bcrypt.check_password_hash(user.password, password_param) \
                and utils.FlaskAppUtils.has_admin_role(user):
                request_is_authenticated = True

        elif current_user:
            if current_user.is_authenticated \
                and utils.FlaskAppUtils.has_admin_role(current_user):
                request_is_authenticated = True
    
    except Exception as e:
        m ="Error authenticating user permissions."
        return utils.FlaskAppUtils.api_exception_subroutine(m, e)    

    if request_is_authenticated:
        # extract fmp_caan_project_reconciliation_task params from request
        to_confirm = utils.FlaskAppUtils.retrieve_request_param('confirm_locations')
        to_confirm = True if (to_confirm and to_confirm.lower() == "true") else False
        to_update = utils.FlaskAppUtils.retrieve_request_param('update_projects')
        to_update = True if (to_update and to_update.lower() == "true") else False
        timeout = utils.FlaskAppUtils.retrieve_request_param('timeout') if utils.FlaskAppUtils.retrieve_request_param('timeout') else DEFAULT_TASK_TIMEOUT

        task_info = {"confirm_locations": to_confirm,
                     "update_projects": to_update,
                     "user": user_param if user_param else current_user.email,
                     "password": password_param if password_param else "logged_in_user"}  
        task_kwargs = {"confirm_locations": to_confirm, "update_existing": to_update}
        nq_kwargs = {"timeout": timeout}
        nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                        enqueued_function=fmp_caan_project_reconciliation_task,
                                                        task_info=task_info,
                                                        task_kwargs=task_kwargs,
                                                        enqueue_call_kwargs=nq_kwargs)
        return flask.Response(json.dumps(utils.serializable_dict(nq_results)), status=200)
    else:
        return flask.Response("Unauthorized", status=401)
        

@project_tools.route("/test/fmp_reconciliation", methods=['GET', 'POST'])
@utils.FlaskAppUtils.roles_required(['ADMIN'])
def test_fmp_reconciliation():
    """
    Endpoint for testing the task that reconciles the application database with the FileMaker database.

    This endpoint enqueues a task to reconcile the application database with the FileMaker database.
    Optionally, it can also confirm the locations of projects in the application database.
    Request parameters can be sent in either th url or request headers.
    
    Query Parameters:
        confirm_locations (str): Whether to confirm the locations of projects in the application database. 
                                 Accepts 'true' or 'false'. Default is 'false'.
        update_projects (str): Whether to update the projects in the application database to match the FileMaker database.
                               Accepts 'true' or 'false'. Default is 'false'.

    Returns:
        Response: A JSON response with the results of the reconciliation and confirmation tasks.
    """
    from archives_application.project_tools.project_tools_tasks import fmp_caan_project_reconciliation_task, confirm_project_locations_task
    results = {"confirm_results":{},
               "reconciliation_results":{}}
    recon_job_id = f"{fmp_caan_project_reconciliation_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
    new_task_record = WorkerTaskModel(task_id=recon_job_id,
                                      time_enqueued=str(datetime.now()),
                                      origin="test",
                                      function_name=fmp_caan_project_reconciliation_task.__name__,
                                      status="queued")
    db.session.add(new_task_record)
    db.session.commit()
    
    to_confirm = utils.FlaskAppUtils.retrieve_request_param('confirm_locations')
    to_confirm = True if (to_confirm and to_confirm.lower() == "true") else False
    update_existing = utils.FlaskAppUtils.retrieve_request_param('update_projects')
    update_existing = True if (update_existing and update_existing.lower() == "true") else False
    reconciliation_results = fmp_caan_project_reconciliation_task(queue_id=recon_job_id,
                                                                  confirm_locations=False, # call this seperately
                                                                  update_existing=update_existing)
    results["reconciliation_results"] = reconciliation_results
    if to_confirm:
        # get list of projects with existing locations to confirm
        to_confirm_foundset = ProjectModel.query.filter(ProjectModel.file_server_location.isnot(None)).all()
        project_nums_list = [proj.number for proj in to_confirm_foundset]
        confirm_job_id = f"{confirm_project_locations_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
        confirm_task_record = WorkerTaskModel(task_id=confirm_job_id,
                                              time_enqueued=str(datetime.now()),
                                              origin="test",
                                              function_name=confirm_project_locations_task.__name__,
                                              status="queued")
        db.session.add(confirm_task_record)
        db.session.commit()
        confirm_results = confirm_project_locations_task(queue_id=confirm_job_id,
                                                         projects_list=project_nums_list)
        results["confirm_results"] = confirm_results
    
    results = utils.serializable_dict(results)
    return flask.Response(json.dumps(results), status=200)


@project_tools.route("/caan_drawings/<caan>", methods=['GET', 'POST'])
def caan_drawings(caan):
    """
    Endpoint for displaying drawings for a given CAAN.

    This endpoint retrieves and displays the locations of project drawings associated with a given CAAN.
    It checks the database for projects linked to the CAAN and categorizes them based on whether they have drawings.
    The locations of the drawings are then displayed in an HTML table.

    Path Parameters:
        caan (str): The CAAN identifier for which to retrieve project drawings.

    Returns:
        Response: Renders the 'caan_drawings.html' template with tables of projects that have drawings and those that might have drawings.
                  Returns a 404 response if the CAAN is not found or if no projects are associated with the CAAN.
    """

    def project_drawing_location(project_location, archives_location, network_location, drawing_folder_prefix = "f5"):
        """
        Returns the location of the drawings folder for a project for access by .
        @param project_location: location of the project folder
        @param drawing_folder_prefix: prefix of the drawings folder
        @return: location of the drawings folder
        """
        
        if not project_location or not archives_location or not network_location:
            return None
        
        app_path_to_proj = os.path.join(archives_location, project_location)
        if os.path.exists(app_path_to_proj):
            drawing_folder_prefix = drawing_folder_prefix.lower()
            for entry in os.scandir(app_path_to_proj):
            
                if entry.is_dir() and entry.name.lower().startswith('f '):
                    project_location = os.path.join(project_location, entry.name)
                    app_path_to_proj = os.path.join(app_path_to_proj, entry.name)

                    for entry2 in os.scandir(app_path_to_proj):
                        if entry2.is_dir() and entry2.name.lower().startswith(drawing_folder_prefix):
                            project_location = os.path.join(project_location, entry2.name)
                            break
                    break
                
                # if the entry is a directory and starts with the drawing folder prefix, then we have found the drawings folder
                if entry.is_dir() and entry.name.lower().startswith(drawing_folder_prefix):
                    project_location = os.path.join(project_location, entry.name)
                    break
            
            user_project_path = utils.FileServerUtils.user_path_from_db_data(file_server_directories=project_location,
                                                                             user_archives_location=network_location)
            return user_project_path
        
        return None
    
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

    # split projects into those with drawings and those without
    has_drawings_groups = caan_projects_df.groupby('drawings')
    if True in has_drawings_groups.groups.keys():
        has_drawings_df = has_drawings_groups.get_group(True)

    else:
        has_drawings_df = pd.DataFrame()

    row_drawing_location = lambda row: project_drawing_location(project_location=row["file_server_location"],
                                                                archives_location=flask.current_app.config.get("ARCHIVES_LOCATION"),
                                                                network_location=flask.current_app.config.get('USER_ARCHIVES_LOCATION'))
    html_col_widths = {"Number": "10%", "Name": "35%", "Location": "55%"}
    
    # get all file locations for projects with drawings
    maybe_drawings_html, has_drawings_html = None, None
    if not has_drawings_df.empty:
        has_drawings_df.sort_values(by=["number"], inplace=True)
        has_drawings_df["Location"] = has_drawings_df.apply(row_drawing_location, axis=1)
        has_drawings_df = has_drawings_df[["number", "name", "Location"]]
        has_drawings_df.columns = has_drawings_df.columns.str.capitalize()
        has_drawings_html = utils.html_table_from_df(df=has_drawings_df,
                                                     path_columns=["Location"],
                                                     column_widths=html_col_widths)

    maybe_drawings_df = caan_projects_df[caan_projects_df["drawings"].isnull()]
    if not maybe_drawings_df.empty:
        maybe_drawings_df.sort_values(by=["number"], inplace=True)
        maybe_drawings_df["Location"] = maybe_drawings_df.apply(row_drawing_location, axis=1)
        maybe_drawings_df = maybe_drawings_df[["number", "name", "Location"]]
        maybe_drawings_df.columns = maybe_drawings_df.columns.str.capitalize()
        maybe_drawings_html = utils.html_table_from_df(df=maybe_drawings_df,
                                                       path_columns=["Location"],
                                                       column_widths=html_col_widths)
    
    # retrieve caan data
    caan = CAANModel.query.filter(CAANModel.caan == caan).first()

    return flask.render_template('caan_drawings.html', caan=caan.caan, caan_name=caan.name, drawings_confirmed_table=has_drawings_html, drawings_maybe_table=maybe_drawings_html)


@project_tools.route("/api/project_location", methods=['GET'])
def project_location():
    """
    API endpoint to retrieve the file server location for a given project.
    
    Query Parameters:
        project (str): The project number to look up.
        
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