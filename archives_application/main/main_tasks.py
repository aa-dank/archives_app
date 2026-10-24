# archives_application/main/main_tasks.py

import bz2
import flask
import flask_sqlalchemy
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta
from typing import Dict
from sqlalchemy.engine import make_url
from archives_application import create_app, utils
from archives_application.models import WorkerTaskModel
from archives_application.cache import positive_integer
from archives_application.archiver.archive_search import is_archive_search_workbook

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
        Remove eligible temporary files using their existing or workbook-specific policy.

        Published Archive Search XLSX files use completed-write age and configured
        seconds; other files keep the existing creation-time/day policy. Invalid
        workbook retention defers only those workbooks. Cleanup stays inside the
        queued task's app context and records initiation/completion status.
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

            # Determine the retention period for archive search workbooks
            try:
                workbook_retention = positive_integer(
                    flask.current_app.config.get("ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS", 518400),
                    "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS",
                )
            except ValueError as error:
                workbook_retention = None
                log["errors"].append(str(error))
                flask.current_app.logger.error("Archive Search workbook deletion deferred: %s", error)
            to_remove = []

            # Identify temporary files that should be removed based on retention policies
            for filepath in temporary_files:
                if is_archive_search_workbook(os.path.basename(filepath)):
                    # Completed publication/write time; downloads never renew age.
                    if (workbook_retention is not None
                            and time.time() - os.path.getmtime(filepath) >= workbook_retention):
                        to_remove.append(filepath)
                elif datetime.fromtimestamp(os.path.getctime(filepath)) < expiration_date:
                    to_remove.append(filepath)
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
        try:
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            log = {"task_id": queue_id, "errors": []}
            cmd = app.config.get("APP_RESTART_COMMAND")
            if not cmd:
                raise ValueError("APP_RESTART_COMMAND not found in app config.")
            
            time.sleep(delay)
            cmd_result = subprocess.run(cmd,
                                        shell=True,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, # This is necessary to capture the output of the command
                                        stderr=subprocess.PIPE,
                                        text=True)
            log["cmd_result"] = cmd_result
        except Exception as e:
            log["errors"].append({"error": e, "stack_trace": str(e.__traceback__)})
        
        log = utils.serializable_dict(log)
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log


def db_backup_task(queue_id: str):
    """Stream a compressed dump, persist phases, and publish only after pg_dump succeeds."""
    part_path = None
    proc = None
    stderr_file = None
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        backup_started_at = time.perf_counter()
        log = {"task_id": queue_id, "errors": [], "phase": "preparing",
               "started_at": datetime.now().isoformat()}
        cleanup_errors = []

        progress = utils.WorkerTaskProgress(
            db, queue_id, log, clock=time.perf_counter, elapsed_key=None)

        def read_stderr():
            stderr_file.seek(0)
            log["stderr"] = stderr_file.read().decode(errors="replace")

        def cleanup():
            # Attempt every cleanup step, retaining the original task exception.
            active_error = sys.exception()

            def attempt(operation):
                try:
                    operation()
                except Exception as error:
                    cleanup_errors.append(error)
                    log["errors"].append({"error": str(error), "stack_trace": traceback.format_exc(),
                                          "during": "cleanup"})

            if proc is not None:
                def stop_process():
                    if proc.poll() is None:
                        proc.kill()
                attempt(stop_process)
                attempt(proc.wait)
                attempt(proc.stdout.close)
            if stderr_file is not None:
                if "stderr" not in log:
                    attempt(read_stderr)
                attempt(stderr_file.close)
            if part_path is not None:
                def remove_partial():
                    try:
                        os.remove(part_path)
                    except FileNotFoundError:
                        pass
                attempt(remove_partial)
            if cleanup_errors and active_error is None:
                raise cleanup_errors[0]

        try:
            progress.start()
            try:
                raw_db_url = flask.current_app.config.get("SQLALCHEMY_DATABASE_URI")
                # Normalize the SQLAlchemy URL to remove the driver suffix so pg_dump accepts it
                db_url = make_url(raw_db_url).set(drivername="postgresql")
                db_url_for_pg_dump = db_url.render_as_string(hide_password=False)
                timestamp = datetime.now().strftime(DB_BACKUP_FILE_TIMESTAMP_FORMAT)

                postgres_executable_location = flask.current_app.config.get("POSTGRESQL_EXECUTABLES_LOCATION")
                pg_dump_bin = f"{postgres_executable_location or ''}pg_dump"
                db_backup_cmd = [pg_dump_bin, db_url_for_pg_dump]
                log["backup_command"] = [pg_dump_bin, db_url.render_as_string(hide_password=True)]

                db_backup_destination = flask.current_app.config.get("DATABASE_BACKUP_LOCATION")
                destination_path = os.path.join(db_backup_destination, f"{DB_BACKUP_FILE_PREFIX}{timestamp}.sql.bz2")
                part_path = f"{destination_path}.part"
                log["backup_location"] = destination_path

                env = os.environ.copy()
                env["PGPASSWORD"] = db_url.password or ""  # provide password non-interactively

                # A file drains diagnostics without a pipe reader thread or pipe-buffer limit.
                stderr_file = tempfile.TemporaryFile()
                progress.set_phase("dumping_and_compressing", key="phase")
                proc = subprocess.Popen(
                    db_backup_cmd, stdout=subprocess.PIPE, stderr=stderr_file, env=env)

                dump_and_compress_started_at = time.perf_counter()
                uncompressed_size = 0
                with bz2.open(part_path, "wb", compresslevel=9) as bz_out:
                    for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b""):
                        uncompressed_size += len(chunk)
                        bz_out.write(chunk)
                log["dump_and_compress_seconds"] = round(time.perf_counter() - dump_and_compress_started_at, 3)

                log["uncompressed_size"] = uncompressed_size
                log["compressed_size"] = os.path.getsize(part_path)
                progress.set_phase("waiting_for_pg_dump", key="phase")
                return_code = proc.wait()
                log["return_code"] = return_code
                read_stderr()
                if return_code != 0:
                    raise RuntimeError(
                        f"Backup command failed. pg_dump exited with code {return_code}:\n{log['stderr']}")

                progress.set_phase("publishing", key="phase")
                rename_started_at = time.perf_counter()
                os.replace(part_path, destination_path)
                log["rename_seconds"] = round(time.perf_counter() - rename_started_at, 3)
                part_path = None
            finally:
                cleanup()
            log["phase"] = "complete"
            log["finished_at"] = datetime.now().isoformat()
            log["duration_seconds"] = round(time.perf_counter() - backup_started_at, 3)
            progress.complete()
        except Exception as error:
            log["stop_reason"] = "failed"
            if not any(error is cleanup_error for cleanup_error in cleanup_errors):
                log["errors"].append({"error": str(error), "stack_trace": traceback.format_exc()})
            log["duration_seconds"] = round(time.perf_counter() - backup_started_at, 3)
            progress.fail()
            raise
        return log
