import flask
import fmrest
import os
import re
import pandas as pd
from archives_application import create_app, utils
from archives_application.models import *
from archives_application.project_tools.routes import FILEMAKER_API_VERSION, FILEMAKER_CAAN_LAYOUT, FILEMAKER_PROJECTS_LAYOUT, FILEMAKER_PROJECT_CAANS_LAYOUT, FILEMAKER_TABLE_INDEX_COLUMN_NAME

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

# Regex pattern for matching a project number.
#  - ^\d{4,5} matches 4 to 5 digits at the start of the string.
#  - (?:[A-Za-z](?:-\d{3})?[A-Za-z]?)? is a non-capturing group that allows for an optional letter, followed by an optional group that matches a dash and three digits, followed by an optional letter.
#  - $ ensures that the pattern matches the entire string.
PROJECT_NUMBER_RE_PATTERN = r'^\d{4,5}(?:[A-Za-z](?:-\d{3})?[A-Za-z]?)?$'



def fmp_caan_project_reconciliation_task(queue_id: str):
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        utils.initiate_task_subroutine(q_id=queue_id, sql_db=db)

        def fmrest_server(layout):
            s = fmrest.Server(
                flask.current_app.config.get("FILEMAKER_HOST_LOCATION"),
                user=flask.current_app.config.get('FILEMAKER_USER'),
                password=flask.current_app.config.get('FILEMAKER_PASSWORD'),
                database=flask.current_app.config.get('FILEMAKER_DATABASE_NAME'),
                layout=layout,
                api_version=FILEMAKER_API_VERSION,
                verify_ssl=False
            )
            return s

        def all_records_using_id_primary(layout):
            
            try:
                fm_server = fmrest_server(layout)
                fm_server.login()
                foundset = fm_server.find(query=[{FILEMAKER_TABLE_INDEX_COLUMN_NAME : "*"}],
                                        limit=1000000)
                df = foundset.to_df()
                return df, None

            except Exception as e:
                return pd.DataFrame(), e

        recon_log = {"CAAN": {"added": [], "removed": []},
                     "project": {"added": [], "removed": []},
                     "project-caans": {"added": [], "removed": []},
                     "errors": []}
        
        fmrest.utils.TIMEOUT = 300
        # Reconcile CAANs
        try:
            fm_caan_df, fm_caan_error = all_records_using_id_primary(FILEMAKER_CAAN_LAYOUT)
            if fm_caan_error:
                recon_log['errors'].append({"message": "Error retrieving FileMaker CAAN data:", "exception": str(fm_caan_error)})
            fm_projects_df, fm_projects_error = all_records_using_id_primary(FILEMAKER_PROJECTS_LAYOUT)
            if fm_projects_error:
                recon_log['errors'].append({"message": "Error retrieving FileMaker project data:", "exception": str(fm_projects_error)})
            fm_project_caan_df, fm_project_caan_error = all_records_using_id_primary(FILEMAKER_PROJECT_CAANS_LAYOUT)
            if fm_project_caan_error:
                recon_log['errors'].append({"message": "Error retrieving FileMaker project-caan join data:", "exception": str(fm_project_caan_error)})

            if not fm_caan_df.empty:
                caan_query = db.session.query(CAANModel)
                db_caans_df = utils.db_query_to_df(caan_query)

                missing_from_db = fm_caan_df
                if not db_caans_df.empty:
                    missing_from_db = fm_caan_df[~fm_caan_df['CAAN'].isin(db_caans_df['caan'])]
                for _, row in missing_from_db.iterrows():
                    caan = CAANModel(caan=row['CAAN'],
                                    name=row['Name'],
                                    description=row['Description'])
                    db.session.add(caan)
                    recon_log['CAAN']['added'].append(row['CAAN'])
                
                if not db_caans_df.empty:
                    missing_from_fm = db_caans_df[~db_caans_df['caan'].isin(fm_caan_df['CAAN'])]
                    for _, row in missing_from_fm.iterrows():
                        caan = CAANModel.query.filter_by(caan=row['caan']).first()
                        
                        # Remove the caan from any projects it is associated with
                        for project in caan.projects:
                            project.caans.remove(caan)
                        
                        db.session.delete(caan)
                        recon_log['CAAN']['removed'].append(row['caan'])
                
                db.session.commit()
            
        except Exception as e:
            utils.attempt_rollback(db)
            recon_log['errors'].append({"message": "Error reconciling CAAN data:", "exception": str(e)})
        
        # Reconcile projects
        try:
            if not fm_projects_df.empty:
                project_query = db.session.query(ProjectModel)
                db_project_df = utils.db_query_to_df(project_query)
                is_proj_number = lambda input_string: bool(re.match(PROJECT_NUMBER_RE_PATTERN, input_string))
                fm_projects_df = fm_projects_df[fm_projects_df['ProjectNumber'].apply(is_proj_number)]
                missing_from_db = fm_projects_df.copy()
                if not db_project_df.empty:
                    missing_from_db = fm_projects_df[~fm_projects_df['ProjectNumber'].isin(db_project_df['number'])]
                
                for _, row in missing_from_db.iterrows():
                    archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
                    project_location = None
                    
                    # attempt to get a project directory for the project
                    try:
                        project_location, _ = utils.path_to_project_dir(project_number=row['ProjectNumber'],
                                                                        archives_location=archives_location)
                        if project_location:
                            project_location = project_location[len(flask.current_app.config.get("ARCHIVES_LOCATION")):]

                    except Exception as e:
                        recon_log['errors'].append({"message": f"Error getting a project directory for {row['ProjectNumber']}:",
                                                    "exception": str(e)})
                    
                    # Map the FileMaker 'Drawings' field to a boolean value. Note thaat it has other values esides yes and no.
                    drawing_value_map = {"Yes": True, "yes": True, "YES": True, "NO": False, "No": False, "no": False}
                    has_drawings = drawing_value_map.get(row['Drawings'], None)
                    
                    project = ProjectModel(number=row['ProjectNumber'],
                                           name=row['ProjectName'],
                                           drawings=has_drawings,
                                           file_server_location=project_location)
                    db.session.add(project)
                    recon_log['project']['added'].append(row['ProjectNumber'])

                if not db_project_df.empty:
                    missing_from_fm = db_project_df[~db_project_df['project_number'].isin(fm_projects_df['Project Number'])]
                    for _, row in missing_from_fm.iterrows():
                        project = ProjectModel.query.filter_by(project_number=row['project_number']).first()
                        
                        # Remove the project from any caans it is associated with
                        for caan in project.caans:
                            caan.projects.remove(project)
                        
                        db.session.delete(project)
                        recon_log['project']['removed'].append(row['project_number'])
                
                db.session.commit()

        except Exception as e:
            utils.attempt_rollback(db)
            recon_log['errors'].append({"message": "Error reconciling project data:", "exception": str(e)})
        
        # Reconcile project-caan join table
        try:
            if not fm_project_caan_df.empty:
                project_groups = fm_project_caan_df.groupby('Projects::ProjectNumber')
                for project_number, project_df in project_groups:
                    project = ProjectModel.query.filter_by(number=project_number).first()
                    caan_numbers = project_df['CAAN'].tolist()
                    
                    # how many caans in the df are not in the db?
                    missing_from_db = [caan for caan in caan_numbers if caan not in [caan.caan for caan in project.caans]]
                    caans = CAANModel.query.filter(CAANModel.caan.in_(caan_numbers)).all()
                    project.caans = caans
                    recon_log['project-caans']['added'].append({"project": project_number, "caans": missing_from_db})
                db.session.commit()

        except Exception as e:
            utils.attempt_rollback(db)
            recon_log['errors'].append({"message": "Error reconciling project-caan join data:", "exception": str(e)})

        utils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=recon_log)
        return recon_log