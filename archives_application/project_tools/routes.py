import flask
import fmrest
import json
from flask_login import login_required, current_user
from archives_application import db, bcrypt
from archives_application import utils
from archives_application.models import *


FILEMAKER_API_VERSION = 'v1'
FILEMAKER_CAAN_LAYOUT = 'caan_table'
FILEMAKER_PROJECTS_LAYOUT = 'projects_table'
FILEMAKER_PROJECT_CAANS_LAYOUT = 'caan_project_join'

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
        nk_results = utils.enqueue_new_task(db=db, enqueued_function=fmp_caan_project_reconciliation_task)
        
@project_tools.route("/test/fmp_reconciliation", methods=['GET', 'POST'])
@utils.roles_required(['ADMIN'])
def test_fmp_reconciliation():
    from archives_application.project_tools.project_tools_tasks import fmp_caan_project_reconciliation_task
    recon_job_id = f"{fmp_caan_project_reconciliation_task.__name__}_test_{datetime.now().strftime(r'%Y%m%d%H%M%S')}" 
    new_task_record = WorkerTaskModel(task_id=recon_job_id, time_enqueued=str(datetime.now()), origin="test",
                        function_name=fmp_caan_project_reconciliation_task.__name__, status="queued")
    db.session.add(new_task_record)
    db.session.commit()
    task_results = fmp_caan_project_reconciliation_task(queue_id=recon_job_id)
    

    # prepare scrape results for JSON serialization
    return flask.Response(json.dumps(task_results), status=200)


