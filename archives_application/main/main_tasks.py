import flask
import os
from datetime import datetime, timedelta
from dateutil import parser
from archives_application import utilities, create_app
from archives_application.models import WorkerTaskModel

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

class AppCustodian:
    def __init__(self, temp_file_lifespan: int):
            self.temporary_files_location = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])
            self.temporary_file_lifespan = temp_file_lifespan


    def temp_file_clean_up_task(self, queue_id):

        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            now = datetime.datetime.now()
            log = {"task_id": queue_id, "files_removed": 0, "quantity_removed": 0, "errors": []}
            expiration_date = now - timedelta(days=self.temporary_file_lifespan)
            temporary_files = [os.path.join(self.temporary_files_location, f) for f in os.listdir(self.temporary_files_location) if os.path.isfile(os.path.join(self.temporary_files_location, f))]
            to_remove = [f for f in temporary_files if os.path.getctime(f) < expiration_date]
            for filepath in to_remove:
                try:
                    filesize = os.path.getsize(filepath)
                    os.remove(filepath)
                    log["files_removed"] += 1
                    log["quantity_removed"] += filesize
                except Exception as e:
                     error_dict = {"filepath": filepath, "error": e}
                     log["errors"].append(error_dict)
            utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, log=log)
            return log
        
            