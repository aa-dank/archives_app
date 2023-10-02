import flask
import json
import os
import numpy as np
import pandas as pd
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

project_tools = flask.Blueprint('project_tools', __name__)



def has_admin_role(usr: UserModel):
    """
    Checks if a user has admin role
    """
    return any([admin_str in usr.roles.split(",") for admin_str in ['admin', 'ADMIN']])


def api_exception_subroutine(response_message, thrown_exception):
    """
    Subroutine for handling an exception and returning response code to api call.
    (In contrast to the web_exception_subroutine, which is for handling exceptions in the web app.)
    @param response_message: message sent with response code
    @param thrown_exception: exception that broke the 'try' conditional
    @return:
    """
    flask.current_app.logger.error(thrown_exception, exc_info=True)
    return flask.Response(response_message + "\n" + str(thrown_exception), status=500)


@project_tools.route("/fmp_reconciliation", methods=['GET', 'POST'])
def filemaker_reconciliation():
    """
    The purpose of this endpoint is to ensure that any changes made to the FileMaker database are reflected in the
    application database. This is done by comparing the FileMaker database to the application database and making
    changes to the application database as needed.
    """
    
    from archives_application.project_tools.project_tools_tasks import fmp_caan_project_reconciliation_task

    # Check if the request includes user credentials or is from a logged in user. 
    # User needs to have ADMIN role.
    request_is_authenticated = False
    try:
        if flask.request.args.get('user'):
            user_param = flask.request.args.get('user')
            password_param = flask.request.args.get('password')
            user = UserModel.query.filter_by(email=user_param).first()

            # If there is a matching user to the request parameter, the password matches and that account has admin role...
            if user and bcrypt.check_password_hash(user.password, password_param) and has_admin_role(user):
                request_is_authenticated = True

        elif current_user:
            if current_user.is_authenticated and has_admin_role(current_user):
                request_is_authenticated = True
    
    except Exception as e:
        m ="Error authenticating user permissions."
        return api_exception_subroutine(m, e)    

    if request_is_authenticated:
        to_confirm = flask.request.args.get('confirm_locations')
        to_confirm = True if to_confirm.lower() == "true" else False
        task_kwargs = {"confirm_locations": to_confirm}
        nk_results = utils.enqueue_new_task(db=db, enqueued_function=fmp_caan_project_reconciliation_task, task_kwargs=task_kwargs)
        return flask.Response(json.dumps(utils.serializablize_dict(nk_results)), status=200)
    else:
        return flask.Response("Unauthorized", status=401)
        

@project_tools.route("/test/fmp_reconciliation", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN'])
def test_fmp_reconciliation():
    from archives_application.project_tools.project_tools_tasks import fmp_caan_project_reconciliation_task
    recon_job_id = f"{fmp_caan_project_reconciliation_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
    new_task_record = WorkerTaskModel(task_id=recon_job_id,
                                      time_enqueued=str(datetime.now()),
                                      origin="test",
                                      function_name=fmp_caan_project_reconciliation_task.__name__,
                                      status="queued")
    db.session.add(new_task_record)
    db.session.commit()
    
    to_confirm = flask.request.args.get('confirm_locations')
    to_confirm = True if to_confirm.lower() == "true" else False
    task_results = fmp_caan_project_reconciliation_task(queue_id=recon_job_id,
                                                        confirm_locations=to_confirm)
    
    # prepare scrape results for JSON serialization
    return flask.Response(json.dumps(task_results), status=200)


@project_tools.route("/drawings_locations/<caan>", methods=['GET', 'POST'])
def caan_projects(caan):

    def project_drawing_location(project_location, archives_location, network_location, drawing_folder_prefix = "f5"):
        """
        Returns the location of the drawings folder for a project for access by .
        @param project_location: location of the project folder
        @param drawing_folder_prefix: prefix of the drawings folder
        @return: location of the drawings folder
        """
        if not project_location or not archives_location or not network_location:
            return None
        
        project_path = os.path.join(archives_location, project_location)
        if os.path.exists(project_path):
            drawing_folder_prefix = drawing_folder_prefix.lower()
            for entry in os.scandir(project_path):
            
                if entry.is_dir() and entry.name.lower().startswith('f '):
                    project_location = os.path.join(project_location, entry)
                    project_path = os.path.join(project_path, entry)

                    for entry2 in os.scandir(project_path):
                        if entry2.is_dir() and entry2.name.lower().startswith(drawing_folder_prefix):
                            project_location = os.path.join(project_location, entry2)
                            break
                    break
                
                # if the entry is a directory and starts with the drawing folder prefix, then we have found the drawings folder
                if entry.is_dir() and entry.name.lower().startswith(drawing_folder_prefix):
                    project_location = os.path.join(project_location, entry)
                    break
            
            user_project_path = utils.user_path_from_db_data(file_server_directories=project_location,
                                                             user_archives_location=network_location)
            return user_project_path
        
        return None
    
    # get all projects for a caan
    caan_projects_query = ProjectModel.query.filter(ProjectModel.caans.any(CAANModel.caan == caan))
    caan_projects_df = utils.db_query_to_df(query=caan_projects_query)
    has_drawings_groups = caan_projects_df.groupby('drawings')
    if True in has_drawings_groups.groups.keys():
        has_drawings_df = has_drawings_groups.get_group(True)
    else:
        has_drawings_df = pd.DataFrame()

    row_drawing_location = lambda row: project_drawing_location(project_location=row["file_server_location"],
                                                                archives_location=flask.current_app.config.get("ARCHIVES_LOCATION"),
                                                                network_location=flask.current_app.config.get('ARCHIVES_NETWORK_LOCATION'))
    # get all file locations for projects with drawings
    if not has_drawings_df.empty:
        
        has_drawings_df["Location"] = has_drawings_df.apply(row_drawing_location, axis=1)
        has_drawings_df = has_drawings_df[["number", "name", "Location"]]
        has_drawings_df.columns = has_drawings_df.columns.str.capitalize()
        has_drawings_html = utils.html_table_from_df(df=has_drawings_df, path_columns=["Location"])

    maybe_drawings_df = caan_projects_df[caan_projects_df["drawings"].isnull()]
    if not maybe_drawings_df.empty:
        maybe_drawings_df["Location"] = maybe_drawings_df.apply(row_drawing_location, axis=1)
        maybe_drawings_df = maybe_drawings_df[["number", "name", "Location"]]
        maybe_drawings_df.columns = maybe_drawings_df.columns.str.capitalize()
        maybe_drawings_html = utils.html_table_from_df(df=maybe_drawings_df, path_columns=["Location"])

    # retrieve caan data
    caan = CAANModel.query.filter(CAANModel.caan == caan).first()

    return flask.render_template('caan_projects.html', caan=caan, caan_name=caan.name, drawings_confirmed_table=has_drawings_html, drawings_maybe_table=maybe_drawings_html)
