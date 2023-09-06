import flask
import fmrest
from flask_login import login_required, current_user
from archives_application import db, bcrypt
from archives_application import utils
from archives_application.models import *


FILEMAKER_API_VERSION = 'v1'
FILEMAKER_CAAN_LAYOUT = 'caan_table'
FILEMAKER_PROJECTS_LAYOUT = 'Projects'

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

    def fmrest_server(layout):
        s = fmrest.Server(
            flask.current_app.config.get("FILEMAKER_HOST_LOCATION"),
            user=flask.current_app.config.get('FILEMAKER_USER'),
            password=flask.current_app.config.get('FILEMAKER_PASSWORD'),
            database_name=flask.current_app.config.get('FILEMAKER_DATABASE'),
            layout=layout,
            api_version=FILEMAKER_API_VERSION,
            verify_ssl=False
        )
        return s
    
    def fmp_caan_df():
        s = fmrest_server(FILEMAKER_CAAN_LAYOUT)
        caan_foundset = s.get_records()
        return caan_foundset.to_df()
    
    def db_caan_df():
        caan_query = db.session.query(CAANModel)
        df = utils.query_to_df(caan_query)
        return df

    def fmp_projects_df():
        s = fmrest_server(FILEMAKER_PROJECTS_LAYOUT)
        projects_foundset = s.get_records()
        return projects_foundset.to_df()
    
    def db_projects_df():
        projects_query = db.session.query(ProjectModel)
        df = utils.query_to_df(projects_query)
        return df
    
    
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
        try:
            filemaker_caan_df = fmp_caan_df()
            db_caans_df = db_caan_df()

            missing_from_db = filemaker_caan_df[~filemaker_caan_df['CAAN'].isin(db_caans_df['caan'])]
            for row_idx, row in missing_from_db.iterrows():
                caan = CAANModel(caan=row['CAAN'], title=row['Title'])
                db.session.add(caan)

            

        except Exception as e:
            m = "Error getting CAAN data." 
            return api_exception_subroutine(m, e)