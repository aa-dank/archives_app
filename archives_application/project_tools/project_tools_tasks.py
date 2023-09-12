import flask
import fmrest
from archives_application import create_app, utils
from archives_application.models import *
from archives_application.project_tools.routes import FILEMAKER_API_VERSION, FILEMAKER_CAAN_LAYOUT, FILEMAKER_PROJECTS_LAYOUT

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

def fmp_reconciliation_task(queue_id: str):
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        utils.initiate_task_subroutine(q_id=queue_id, sql_db=db)

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
        
        
        recon_log = {"CAAN": {"added": [], "removed": []},
                     "project": {"added": [], "removed": []},
                     "errors": []}
        try:
            filemaker_caan_df = fmp_caan_df()
            db_caans_df = db_caan_df()

            missing_from_db = filemaker_caan_df[~filemaker_caan_df['CAAN'].isin(db_caans_df['caan'])]
            for _, row in missing_from_db.iterrows():
                caan = CAANModel(caan=row['CAAN'],
                                    name=row['Name'],
                                    description=row['Description'])
                db.session.add(caan)
                recon_log['CAAN']['added'].append(row['CAAN'])
            
            missing_from_fmp = db_caans_df[~db_caans_df['caan'].isin(filemaker_caan_df['CAAN'])]
            for _, row in missing_from_fmp.iterrows():
                caan = CAANModel.query.filter_by(caan=row['caan']).first()
                
                # Remove the caan from any projects it is associated with
                for project in caan.projects:
                    project.caans.remove(caan)
                
                db.session.delete(caan)
                recon_log['CAAN']['removed'].append(row['caan'])
            
            db.session.commit()
            
        except Exception as e:
            if db.session.transaction and db.session.transaction.nested:
                            db.session.rollback()
            recon_log['errors'].append({"message": "Error reconciling CAAN data:", "exception": str(e)})
        
        try:
            
            filemaker_projects_df = fmp_projects_df()
            db_project_df = db_projects_df()

            missing_from_db = filemaker_projects_df[~filemaker_projects_df['Project Number'].isin(db_project_df['project_number'])]
            for _, row in missing_from_db.iterrows():
                project = ProjectModel(project_number=row['Project Number'],
                                        project_name=row['Project Name'],
                                        project_description=row['Project Description'],
                                        project_manager=row['Project Manager'])
                db.session.add(project)
                recon_log['project']['added'].append(row['Project Number'])

            missing_from_fmp = db_project_df[~db_project_df['project_number'].isin(filemaker_projects_df['Project Number'])]
            for _, row in missing_from_fmp.iterrows():
                project = ProjectModel.query.filter_by(project_number=row['project_number']).first()
                
                # Remove the project from any caans it is associated with
                for caan in project.caans:
                    caan.projects.remove(project)
                
                db.session.delete(project)
                recon_log['project']['removed'].append(row['project_number'])
            db.session.commit()

        except Exception as e:
            if db.session.transaction and db.session.transaction.nested:
                            db.session.rollback()
            recon_log['errors'].append({"message": "Error reconciling project data:", "exception": str(e)})
            