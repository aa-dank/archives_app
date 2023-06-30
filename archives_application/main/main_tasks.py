import flask
import os
import re
import flask_sqlalchemy
from datetime import datetime, timedelta
from typing import Dict
from archives_application import utilities, create_app
from archives_application.models import WorkerTaskModel
from archives_application.main.routes import DB_BACKUP_FILE_PREFIX, DB_BACKUP_FILE_TIMESTAMP_FORMAT

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

class AppCustodian:
    def __init__(self, temp_file_lifespan: int, db_backup_file_lifespan: int, task_records_lifespan_map: Dict[str, int]):
            """
            temp_file_lifespan: the number of days that a file can remain in the temp_files directory before it is removed
            task_records_lifespan_map: a dictionary mapping task names to the number of days that the task records can remain in the database before they are removed.
            """
            self.temporary_files_location = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])
            self.temporary_file_lifespan = temp_file_lifespan
            self.task_records_lifespan_map = task_records_lifespan_map
            self.db_backup_file_lifespan = db_backup_file_lifespan


    def enqueue_maintenance_tasks(self, db: flask_sqlalchemy.SQLAlchemy):
        # interrogate self for all functions that end int '_task'
        # call each of those functions
        # return a dictionary of results

        # get all functions that end in '_task'
        task_functions = [f for f in dir(self) if f.endswith('_task')]
        results = {}
        for task_name in task_functions:
            task_function = getattr(self, task_name)
            enqueuement_results = utilities.enqueue_new_task(db=db,
                                                             enqueued_function=task_function)
            results[task_name] = enqueuement_results
        
        return results


    def temp_file_clean_up_task(self, queue_id: str):
        """
        This task will remove all files in the temp_files directory that are older than the specified lifespan.
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            now = datetime.now()
            log = {"task_id": queue_id, "files_removed": 0, "quantity_removed": 0, "errors": []}
            expiration_date = now - timedelta(days=self.temporary_file_lifespan)
            temporary_files = [os.path.join(self.temporary_files_location, f) for f in os.listdir(self.temporary_files_location) 
                               if os.path.isfile(os.path.join(self.temporary_files_location, f))]
            creation_dt = lambda f_path: datetime.fromtimestamp(os.path.getctime(f_path))            
            to_remove = [f for f in temporary_files if creation_dt(f) < expiration_date]
            for filepath in to_remove:
                try:
                    filesize = os.path.getsize(filepath)
                    os.remove(filepath)
                    log["files_removed"] += 1
                    log["quantity_removed"] += filesize
                except Exception as e:
                     error_dict = {"filepath": filepath, "error": e}
                     log["errors"].append(error_dict)
            utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


    def task_records_clean_up_task(self, queue_id: str):
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            now = datetime.now()
            log = {"task_id": queue_id, "records_removed": 0, "errors": []}
            
            for task_type, lifespan in self.task_records_lifespan_map.items():
                expiration_date = now - timedelta(days=lifespan)
                records = WorkerTaskModel.query.filter(WorkerTaskModel.time_enqueued < expiration_date,
                                                       WorkerTaskModel.function_name == task_type)\
                                                .all()
                if records:
                    log["records_removed"] += len(records)
                    [db.session.delete(record) for record in records]
                    db.session.commit()
            
            utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


    def db_backup_clean_up_task(self, queue_id: str):
        """
        This task will remove all files in the temp_files directory that are older than the specified lifespan.
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            now = datetime.now()
            log = {"task_id": queue_id, "files_removed": 0, "errors": []}
            expiration_date = now - timedelta(days=self.db_backup_file_lifespan)
            db_backup_location = flask.current_app.config.get("DATABASE_BACKUP_LOCATION")
            db_backup_files = [f for f in os.listdir(db_backup_location) if f.startswith(DB_BACKUP_FILE_PREFIX)]
            for db_file in db_backup_files:
                db_file_timestamp = re.search(r'\d+', db_file).group() # Retrieves all digits from db_file as a string
                db_file_dt = datetime.strptime(db_file_timestamp, DB_BACKUP_FILE_TIMESTAMP_FORMAT)
                if db_file_dt < expiration_date:
                    path = os.path.join(db_backup_location, db_file)
                    try:
                        os.remove(path)
                        log["files_removed"] += 1
                    except Exception as e:
                        error_dict = {"filepath": path, "error": e}
                        log["errors"].append(error_dict)
            
            utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


