import bz2
import flask
import flask_sqlalchemy
import os
import re
import subprocess
import time
from datetime import datetime, timedelta
from typing import Dict
from archives_application import create_app, utils
from archives_application.models import WorkerTaskModel

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

DB_BACKUP_FILE_PREFIX = "db_backup_"
DB_BACKUP_FILE_TIMESTAMP_FORMAT = r"%Y%m%d%H%M%S"

class AppCustodian:
    def __init__(self, temp_file_lifespan: int, db_backup_file_lifespan: int, task_records_lifespan_map: Dict[str, int]):
            """
            :param temp_file_lifespan: the number of days that a file can remain in the temp_files directory before it is removed
            :param task_records_lifespan_map: a dictionary mapping task names to the number of days that the task records can remain in the database before they are removed.
            :param db_backup_file_lifespan: the number of days that a database backup file can remain in the DATABASE_BACKUP_LOCATION directory before it is removed
            """
            self.temporary_files_location = os.path.join(os.getcwd(), *["archives_application", "static", "temp_files"])
            self.temporary_file_lifespan = temp_file_lifespan
            self.task_records_lifespan_map = task_records_lifespan_map
            self.db_backup_file_lifespan = db_backup_file_lifespan


    def enqueue_maintenance_tasks(self, db: flask_sqlalchemy.SQLAlchemy):
        """
        This function will enqueue all of the maintenance tasks that are defined in this class.
        :param db: the SQLAlchemy database object
        """
        # interrogate self for all functions that end int '_task'
        # call each of those functions
        # return a dictionary of results
        # get all functions that end in '_task'
        task_functions = [f for f in dir(self) if f.endswith('_task')]
        results = {}
        for task_name in task_functions:
            task_function = getattr(self, task_name)
            enqueuement_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                                     enqueued_function=task_function)
            results[task_name] = enqueuement_results
        
        return results


    def _temp_file_clean_up_task(self, queue_id: str):
        """
        This task will remove all files in the temp_files directory that are older than the specified lifespan.
        :param queue_id: the id of the task in the RQ queue
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
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
                     error_dict = {"filepath": filepath, "error": e, "stack_trace": str(e.__traceback__)}
                     log["errors"].append(error_dict)

            serializable_log = {k: str(v) if not isinstance(str(v), Exception) else v for k, v in log.items()}
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=serializable_log)
            return log


    def _task_records_clean_up_task(self, queue_id: str):
        """
        This task will remove all records in the WorkerTaskModel table that are older than the specified lifespan.
        :param queue_id: the id of the task in the RQ queue
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
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
            
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


    def _db_backup_clean_up_task(self, queue_id: str):
        """
        This task will remove all files in the DATABASE_BACKUP_LOCATION directory that are older than the specified lifespan.
        :param queue_id: the id of the task in the RQ queue
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
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
                        error_dict = {"filepath": path, "error": e, "stack_trace": str(e.__traceback__)}
                        log["errors"].append(error_dict)
            
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


def restart_app_task(queue_id: str, delay: int = 0):
    """
    This task will restart the app using the supervisorctl command.
    :param queue_id: the id of the task in the RQ queue
    :param delay: the number of seconds to wait before restarting the app
    """
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        log = {"task_id": queue_id, "errors": []}
        cmd = "sudo supervisorctl restart archives_app"
        time.sleep(delay)
        try:
            cmd_result = subprocess.run(cmd,
                                        shell=True,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, # This is necessary to capture the output of the command
                                        stderr=subprocess.PIPE,
                                        text=True)
            log["cmd_result"] = cmd_result
        except Exception as e:
            log["errors"].append({"error": e, "stack_trace": str(e.__traceback__)})
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log


def restart_app_workers_task(queue_id: str, delay: int = 0):
    #TODO: Implement this function and incorporate it into the app config change endpoint
    pass


def db_backup_task(queue_id: str):
    """
    Worker task for sending pg_dump command to shell and saving a compressed backup to the server.
    Resources:
    https://stackoverflow.com/questions/63299534/backup-postgres-from-python-on-win10
    https://stackoverflow.com/questions/43380273/pg-dump-pg-restore-password-using-python-module-subprocess
    https://medium.com/poka-techblog/5-different-ways-to-backup-your-postgresql-database-using-python-3f06cea4f51
    :param queue_id: the id of the task in the RQ queue

    """
    
    def bz2_compress_file(input_filepath: str, output_filepath: str = None):
        """
        Compresses a file using bz2 compression.
        :param input_filepath: the path to the file to be compressed
        :param output_filepath: the path to the compressed file. If none is provided, the compressed file will be saved in the same directory as the input file.
        """

        if not output_filepath:
            input_filepath_list = utils.FileServerUtils.split_path(input_filepath)
            filename = input_filepath_list[-1]
            output_filepath = os.path.join(input_filepath_list[:-1], filename + '.bz2')

        with open(input_filepath, 'rb') as f_in:
            with bz2.open(output_filepath, 'wb') as f_out:
                f_out.writelines(f_in)
                return os.path.exists(output_filepath)
            
    with app.app_context():
        try:
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            log = {"task_id": queue_id, "errors": []}
            db_url = flask.current_app.config.get("SQLALCHEMY_DATABASE_URI")
            timestamp = datetime.now().strftime(DB_BACKUP_FILE_TIMESTAMP_FORMAT)
            temp_backup_filename = f"{DB_BACKUP_FILE_PREFIX}{timestamp}.sql"
            temp_backup_path =  utils.FlaskAppUtils.create_temp_filepath(temp_backup_filename)
            
            # An example of desired shell pg_dump command:
            # pg_dump postgresql://archives:password@localhost:5432/archives > /opt/app/data/Archive_Data/backup101.sql
            postgres_executable_location = flask.current_app.config.get("POSTGRESQL_EXECUTABLES_LOCATION")
            db_backup_cmd = fr"""{postgres_executable_location}pg_dump {db_url} > {temp_backup_path}"""
            log["backup_command"] = db_backup_cmd
            cmd_result = subprocess.run(db_backup_cmd,
                                        shell=True,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE,
                                        text=True)
            if cmd_result.stderr:
                raise Exception(
                    f"Backup command failed. Stderr from attempt to call pg_dump back-up command:\n{cmd_result.stderr}")
            log["stdout"] = str(cmd_result.stdout)
            db_backup_destination = flask.current_app.config.get("DATABASE_BACKUP_LOCATION")
            destination_path = os.path.join(db_backup_destination, f"{DB_BACKUP_FILE_PREFIX}{timestamp}.sql.bz2")
            log["backup_location"] = destination_path
            log["uncompressed_size"] = os.path.getsize(temp_backup_path)
            
            # Compress the backup file using bz2 compression
            bz2_compress_file(temp_backup_path, destination_path)
            os.remove(temp_backup_path)
            log["compressed_size"] = os.path.getsize(destination_path)

        except Exception as e:
            # log stack trace and error message
            log["errors"].append({"error": e, "stack_trace": str(e.__traceback__)})


        log = {k: str(val) for k, val in log.items() if hasattr(val, '__str__')} # Convert all values to strings to avoid JSON serialization errors
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log

        
    