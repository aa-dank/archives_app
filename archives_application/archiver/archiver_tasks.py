# archives_application/archiver/archiver_tasks.py

from archives_application import create_app, utils
from archives_application.models import ArchivedFileModel, FileLocationModel, FileModel, WorkerTaskModel, ServerChangeModel
from archives_application.archiver.routes import exclude_extensions, exclude_filenames
import flask
import os
import time
import traceback
from datetime import timedelta, datetime
from typing import Callable
from rq.timeouts import JobTimeoutException


# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()


def add_file_to_db_task(filepath: str,  queue_id: str, archiving: bool = False):
    """
    This function adds a file to the database.
    :param filepath: The path of the file to add to the database.
    :param queue_id: The id of task in the worker queue.
    :param archiving: A flag to indicate if the file is being added to the database as part of an archiving event.
    """
    with app.app_context():
        task_results = {'queue_id': queue_id, 'filepath': filepath}
        try:
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id,
                                                       sql_db=db)
            
            file_hash = utils.FilesUtils.get_hash(filepath)
            file_id = None
            filename = utils.FileServerUtils.split_path(filepath)[-1]
            
            # check if the file is already in the database and add it if it is not
            while not file_id:
                db_file_entry = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
                if not db_file_entry:
                    file_ext = filename.split('.')[-1]
                    file_size = os.path.getsize(filepath)
                    new_file = FileModel(hash=file_hash, size=file_size, extension=file_ext)
                    db.session.add(new_file)
                    # Keep the file row in the same transaction as its first
                    # location. A failed location write must not leave a
                    # canonical file row without a physical location.
                    db.session.flush()
                    file_id = new_file.id
                
                else:
                    file_id = db_file_entry.id
            
            # extract the path from the root of the windows share
            file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            server_directories = filepath[:-(len(filename)+1)]
            server_dirs_list = utils.FileServerUtils.split_path(server_directories)[file_server_root_index:]
            server_directories = os.path.join(*server_dirs_list)

            # check if the file location is already in the database and add it if it is not
            db_file_location_entry = db.session.query(FileLocationModel).filter(FileLocationModel.file_server_directories == server_directories,
                                                                                FileLocationModel.filename == filename).first()
            
            # if there is already already a file location that is the same as this loction,
            # but the files are different, we remove the old file location and add the new one.
            if db_file_location_entry and (db_file_location_entry.file_id != file_id):
                displaced_file_id = db_file_location_entry.file_id
                db.session.delete(db_file_location_entry)
                db.session.flush()

                # A replaced path may have been the displaced file's only
                # known location. Remove that obsolete FileModel only when it
                # no longer has any locations, and keep the replacement work
                # in one transaction.
                remaining_location = db.session.query(FileLocationModel.id)\
                    .filter(FileLocationModel.file_id == displaced_file_id)\
                    .first()
                if not remaining_location:
                    archival_events = db.session.query(ArchivedFileModel)\
                        .filter(ArchivedFileModel.file_id == displaced_file_id)\
                        .all()
                    for archival_event in archival_events:
                        archival_event.file_id = None

                    displaced_file = db.session.query(FileModel)\
                        .filter(FileModel.id == displaced_file_id)\
                        .first()
                    if displaced_file:
                        db.session.delete(displaced_file)
                db_file_location_entry = None
            
            if not db_file_location_entry:
                new_file_location = FileLocationModel(file_server_directories=server_directories,
                                                    filename=filename,
                                                    file_id=file_id,
                                                    existence_confirmed = datetime.now(),
                                                    hash_confirmed = datetime.now())
                db.session.add(new_file_location)
                db.session.commit()

            # if the file is already in the database, update the existence_confirmed and hash_confirmed fields
            else:
                db_file_location_entry.existence_confirmed = datetime.now()
                db_file_location_entry.hash_confirmed = datetime.now()
                db.session.commit()

            # if adding the file to database is connected to archiving event, update associated archived_files entry   
            if archiving:
                search_path = os.path.join(server_directories, filename)
                archived_file = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.destination_path.endswith(search_path),
                                                                           ArchivedFileModel.filename == filename)\
                                                                            .order_by(db.asc(ArchivedFileModel.date_archived))\
                                                                                .first()
                if archived_file:
                    archived_file.file_id = file_id
                else:
                    task_results['error'] = f'Could not find archived file with path {search_path} in database.'
                db.session.commit()
            task_results["file_id"] = file_id 
            task_results["filepath"] = filepath
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id,
                                                       sql_db=db,
                                                       task_result=task_results)
            return file_id
        
        except Exception as e:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            error_msg = f"Error adding file {filepath} to database:\n{str(e)}\nTraceback:\n{traceback.format_exc()}"
            task_results['error'] = error_msg
            utils.RQTaskUtils.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=task_results)


class ScrapeTimeLimitReached(Exception):
    """End a scrape cooperatively without accepting an incomplete file hash."""


class ScrapeCheckpointError(Exception):
    """Stop the scrape when its durable progress cannot be saved."""


def _scrape_directories(archives_location, start_location, check_deadline):
    """Walk one circular pass, yielding the start directory inclusively.

    Skip earlier subtrees while seeking, then revisit that prefix after reaching
    the end. Deadline checks during seeking never advance the saved checkpoint.
    Enumeration errors must not silently skip a subtree.
    """
    def walk_error(error):
        raise error

    def walk():
        for root, directories, files in os.walk(archives_location, onerror=walk_error):
            check_deadline()
            directories.sort()
            yield root, directories, files

    found = False
    for root, directories, files in walk():
        if root == start_location:
            found = True
        if found:
            yield root, files
        else:
            start_branch = os.path.relpath(start_location, root).split(os.sep, 1)[0]
            if start_branch in directories:
                # Keep the starting branch and later siblings. Earlier
                # branches belong to the wraparound pass, not the seek.
                del directories[:directories.index(start_branch)]
    if not found:
        raise FileNotFoundError(f"Scrape start directory was not found: {start_location}")
    if start_location != archives_location:
        for root, _, files in walk():
            if root == start_location:
                break
            yield root, files


def _persist_scraped_file(db, file, archives_location, file_hash, file_size):
    """Commit observed content and its path together, returning counter changes.

    The caller rolls back failures. Replaying an existing path updates its content
    association instead of inserting another location. Historical content rows
    and pre-existing duplicate locations are retained.
    """
    filename = os.path.basename(file)
    db_file = db.session.query(FileModel).filter_by(hash=file_hash).first()
    file_is_new = db_file is None
    if file_is_new:
        db_file = FileModel(hash=file_hash, size=file_size, extension=filename.split(".")[-1].lower())
        db.session.add(db_file)
        db.session.flush()

    directories = os.path.relpath(os.path.dirname(file), archives_location)
    if directories == ".":
        directories = ""
    location = db.session.query(FileLocationModel).filter_by(
        file_server_directories=directories, filename=filename).first()
    location_is_new = location is None
    location_changed = location is not None and location.file_id != db_file.id
    if location_is_new:
        location = FileLocationModel(file_server_directories=directories, filename=filename)
        db.session.add(location)
    location.file_id = db_file.id
    location.existence_confirmed = datetime.now()
    location.hash_confirmed = location.existence_confirmed
    db.session.commit()
    return {"Files Added": int(file_is_new),
            "File Locations Added": int(location_is_new),
            "File Locations Updated": int(location_changed),
            "Files Confirmed": int(not location_is_new)}


def scrape_file_data_task(archives_location: str, start_location: str, file_server_root_index: int,
                          exclusion_functions: list[Callable[[str], bool]], scrape_time: timedelta,
                          queue_id: str):
    """Scrape with durable directory checkpoints and a cooperative time budget.

    Resume replays the saved directory, including its already committed files.
    File contents and their location are reconciled in one transaction. A hard
    termination can leave status ``started``, but cannot erase prior checkpoints.
    ``file_server_root_index`` remains accepted for jobs already stored in RQ;
    relative paths are now derived directly from ``archives_location``.
    """
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        start_time = time.monotonic()
        deadline = start_time + scrape_time.total_seconds()
        last_checkpoint = start_time
        scrape_log = {"Scrape Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
                      "This Start Location": start_location,
                      "Files Added": 0, "File Locations Added": 0,
                      "Files Confirmed": 0, "File Locations Updated": 0,
                      "Errors": [], "Time Elapsed": "0s",
                      "Current File": None, "Current File Started": None,
                      "Last Completed File": None}
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db, task_result=scrape_log)

        def check_deadline():
            if time.monotonic() >= deadline:
                raise ScrapeTimeLimitReached()

        def checkpoint(force=False):
            """Commit before slow file I/O and every 30 seconds during hashing."""
            nonlocal last_checkpoint
            now = time.monotonic()
            if force or now - last_checkpoint >= 30:
                scrape_log["Time Elapsed"] = f"{now - start_time:.3f}s"
                try:
                    utils.RQTaskUtils.update_task_subroutine(
                        sql_db=db, q_id=queue_id, status_details=scrape_log)
                except JobTimeoutException:
                    raise
                except Exception as error:
                    raise ScrapeCheckpointError("Could not save scrape progress") from error
                last_checkpoint = now

        def hash_progress():
            check_deadline()
            checkpoint()

        try:
            if scrape_time.total_seconds() <= 0:
                raise ValueError("scrape_time must be positive")
            archives_location = os.path.abspath(archives_location)
            start_location = os.path.abspath(start_location or archives_location)
            if os.path.commonpath([archives_location, start_location]) != archives_location:
                raise ValueError("Scrape start directory must be inside the archive root")
            if not os.path.isdir(archives_location):
                raise FileNotFoundError(f"Archive root is unavailable: {archives_location}")
            # A saved directory can be deleted between enqueue and execution.
            # Revisit its nearest existing ancestor instead of skipping ahead.
            requested_start = start_location
            while start_location != archives_location and not os.path.isdir(start_location):
                start_location = os.path.dirname(start_location)
            if not os.path.isdir(start_location):
                raise FileNotFoundError(f"Scrape start directory is unavailable: {start_location}")
            if start_location != requested_start:
                scrape_log["Resume Fallback"] = start_location
            scrape_log["Next Start Location"] = os.path.relpath(start_location, archives_location)
            checkpoint(force=True)

            for root, files in _scrape_directories(archives_location, start_location, check_deadline):
                scrape_log["Next Start Location"] = os.path.relpath(root, archives_location)
                scrape_log["Current File"] = None
                scrape_log["Current File Started"] = None
                checkpoint(force=True)
                for filename in sorted(files):
                    check_deadline()
                    file = os.path.join(root, filename)
                    if any(fun(file) for fun in exclusion_functions):
                        continue
                    scrape_log["Current File"] = os.path.relpath(file, archives_location)
                    scrape_log["Current File Started"] = datetime.now().isoformat()
                    checkpoint(force=True)
                    try:
                        file_size = os.path.getsize(file)
                        if file_size == 0:
                            continue
                        file_hash = utils.FilesUtils.get_hash(filepath=file, check_progress=hash_progress)
                        check_deadline()
                        changes = _persist_scraped_file(db, file, archives_location, file_hash, file_size)
                        for counter, increment in changes.items():
                            scrape_log[counter] += increment
                        scrape_log["Last Completed File"] = scrape_log["Current File"]
                        checkpoint()
                    except (ScrapeTimeLimitReached, ScrapeCheckpointError, JobTimeoutException):
                        raise
                    except Exception as error:
                        utils.FlaskAppUtils.attempt_db_rollback(db)
                        scrape_log["Errors"].append({"Filepath": file, "Exception": str(error),
                                                     "Traceback": traceback.format_exc()})
                check_deadline()
            scrape_log["Stop Reason"] = "scan_complete"
            scrape_log["Next Start Location"] = "."
            scrape_log["Current File"] = None
            scrape_log["Current File Started"] = None
        except ScrapeTimeLimitReached:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            scrape_log["Stop Reason"] = "time_limit"
        except Exception as error:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            scrape_log["Stop Reason"] = "failed"
            scrape_log["Errors"].append({"Exception": str(error), "Traceback": traceback.format_exc()})
            scrape_log["Time Elapsed"] = f"{time.monotonic() - start_time:.3f}s"
            utils.RQTaskUtils.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=scrape_log)
            raise

        scrape_log["Time Elapsed"] = f"{time.monotonic() - start_time:.3f}s"
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=scrape_log)
        return scrape_log


def _confirm_file_location(db, location, exists):
    """Commit one existence check and any last-location cleanup atomically.

    Return whether the canonical file record was removed. The caller rolls back
    failures before saving progress; no task update can commit a partial cleanup.
    """
    file_removed = False
    if exists:
        db.session.query(FileLocationModel).filter_by(id=location.id).update(
            {"existence_confirmed": datetime.now()})
    else:
        db.session.query(FileLocationModel).filter_by(id=location.id).delete()
        remaining = db.session.query(FileLocationModel.id).filter_by(file_id=location.file_id).first()
        if remaining is None:
            db.session.query(ArchivedFileModel).filter_by(file_id=location.file_id).update({"file_id": None})
            file_removed = bool(db.session.query(FileModel).filter_by(id=location.file_id).delete())
    db.session.commit()
    return file_removed


def confirm_file_locations_task(archive_location: str, confirming_time: timedelta, queue_id: str):
    """Confirm oldest locations first, persisting progress between transactions.

    The current location is saved before filesystem I/O, with elapsed time and
    committed counters refreshed at least every 30 seconds while checks return.
    Updated existence timestamps provide continuation on subsequent runs; no
    separate resume cursor is needed. A blocked filesystem check remains subject
    to RQ's timeout, and an interrupted check is left eligible for a later run.
    """
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        start_time = time.monotonic()
        deadline = start_time + confirming_time.total_seconds()
        last_update = start_time
        log = {"Confirm Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
               "Errors": [], "Locations Checked": 0, "Locations Missing": 0,
               "Files Removed": 0, "Files Confirmed": 0, "Time Elapsed": "0s",
               "Current Location ID": None, "Current File": None,
               "Current File Started": None, "Last Completed Location ID": None,
               "Last Completed File": None}
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)

        def save_progress(force=False):
            """Save only at transaction boundaries; persistence errors stop the run."""
            nonlocal last_update
            now = time.monotonic()
            if force or now - last_update >= 30:
                log["Time Elapsed"] = f"{now - start_time:.3f}s"
                utils.RQTaskUtils.update_task_subroutine(sql_db=db, q_id=queue_id, status_details=log)
                last_update = now

        try:
            if confirming_time.total_seconds() <= 0:
                raise ValueError("confirming_time must be positive")
            while time.monotonic() < deadline:
                # Scalar snapshots avoid ORM expiration/reloads after every
                # committed progress save while keeping batches bounded.
                locations = db.session.query(
                    FileLocationModel.id, FileLocationModel.file_id,
                    FileLocationModel.file_server_directories, FileLocationModel.filename,
                ).order_by(FileLocationModel.existence_confirmed.asc().nullsfirst(),
                           FileLocationModel.id.asc()).limit(1000).all()
                if not locations:
                    log["Stop Reason"] = "no_locations"
                    break
                for location in locations:
                    if time.monotonic() >= deadline:
                        break
                    relative_path = os.path.join(location.file_server_directories or "", location.filename)
                    log["Current Location ID"] = location.id
                    log["Current File"] = relative_path
                    log["Current File Started"] = datetime.now().isoformat()
                    save_progress(force=True)
                    try:
                        exists = os.path.exists(os.path.join(archive_location, relative_path))
                        # A slow check must not start destructive database work
                        # after the cooperative budget has already expired.
                        if time.monotonic() >= deadline:
                            break
                        file_removed = _confirm_file_location(db, location, exists)
                    except JobTimeoutException:
                        raise
                    except Exception as error:
                        utils.FlaskAppUtils.attempt_db_rollback(db)
                        log["Errors"].append({"Location": location.file_server_directories,
                                              "filename": location.filename, "Exception": str(error),
                                              "Traceback": traceback.format_exc()})
                    else:
                        log["Locations Checked"] += 1
                        log["Locations Missing"] += int(not exists)
                        log["Files Removed"] += int(file_removed)
                        log["Files Confirmed"] += int(exists)
                        log["Last Completed Location ID"] = location.id
                        log["Last Completed File"] = relative_path
                    save_progress()
            log.setdefault("Stop Reason", "time_limit")
            if log["Stop Reason"] == "no_locations":
                log["Current Location ID"] = None
                log["Current File"] = None
                log["Current File Started"] = None
        except Exception as error:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            log["Stop Reason"] = "failed"
            log["Errors"].append({"Exception": str(error), "Traceback": traceback.format_exc()})
            log["Time Elapsed"] = f"{time.monotonic() - start_time:.3f}s"
            utils.RQTaskUtils.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            raise

        log["Time Elapsed"] = f"{time.monotonic() - start_time:.3f}s"
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log


def scrape_location_files_task(scrape_location: str, queue_id: str, recursively: bool = True, confirm_data: bool = True):
    """
    Reconcialiates the files in the database with the files in the scrape location. First, if confirm_data is True,
    it checks if the files in the database are still in the scrape location. If they are not, it removes the relevant 
    records. Then, it adds any files in the scrape location that are not in the database by enqueuing a task to add
    the file to the database.
    """
    
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy']
        file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
        location_scrape_log = {"queue_id": queue_id,
                               "Location": scrape_location,
                               "Recursive": recursively,
                               "Confirm Data": confirm_data,
                               "Locations Missing": 0,
                               "Files Records Removed": 0,
                               "Files Confirmed": 0,
                               "Files Enqueued to Add": 0,
                               "Errors": []}
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db, task_result=location_scrape_log)
        relevant_file_data_list = []
        if confirm_data:
            try:
                location_db_dirs_list = utils.FileServerUtils.split_path(scrape_location)[file_server_root_index:]
                query_dirs = os.path.join(*location_db_dirs_list)
                archive_location = flask.current_app.config.get('ARCHIVES_LOCATION')
                relevant_locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_server_directories.startswith(query_dirs)).all()
                for idx, location_record in enumerate(relevant_locations):
                    try:
                        filepath = os.path.join(archive_location, location_record.file_server_directories, location_record.filename)
                        
                        # if the file no longer exists, we delete the entry in the database
                        if not os.path.exists(filepath):
                            location_scrape_log["Locations Missing"] += 1
                            file_id = location_record.file_id
                            db.session.delete(location_record)
                            db.session.commit()
                            
                            # if there are no other locations for this file, we delete entry in the files table
                            other_locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == file_id).all()
                            if other_locations == []:
                                
                                # check for any archive events associated with this file. Update those events to remove their file_id.
                                archive_events = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.file_id == file_id).all()
                                if archive_events != []:
                                    for event in archive_events:
                                        event.file_id = None
                                    db.session.commit()
                                
                                # delete the file entry in the database
                                db.session.query(FileModel).filter(FileModel.id == file_id).delete()
                                db.session.commit()
                                location_scrape_log["Files Records Removed"] += 1
                        
                        else:
                            db.session.query(FileLocationModel).filter(FileLocationModel.id == location_record.id)\
                                .update({"existence_confirmed": datetime.now()})
                            db.session.commit()
                            location_scrape_log["Files Confirmed"] += 1
                    
                    except Exception as e:
                        utils.FlaskAppUtils.attempt_db_rollback(db)
                        e_dict = {"Location": location_record.file_server_directories,
                                "filename": location_record.filename,
                                "Exception": str(e)}
                        location_scrape_log["Errors"].append(e_dict)

                # populate list of file data that is already in the database        
                relevant_file_data_list = [(location_record.file_server_directories, location_record.filename) for location_record in relevant_locations]

            except Exception as e:
                utils.FlaskAppUtils.attempt_db_rollback(db)
                e_dict = {"Location": None,
                          "filename": None,
                          "Exception": str(e)}
                location_scrape_log["Errors"].append(e_dict)
     
        # Iterate through the files in the scrape location and add them to the database if they are not already there
        # by enqueuing a task to add the file to the database.
        for root, _, files in os.walk(scrape_location):
            
            server_dirs_list = []
            server_dirs = ""
            if files:
                server_dirs_list = utils.FileServerUtils.split_path(root)[file_server_root_index:]
                server_dirs = os.path.join(*server_dirs_list)
            
            for file in files:
                try:
                    filepath = os.path.join(root, file)
                    # If the file is excluded by one of the exclusion functions, move to next file.
                    if exclude_filenames(filepath) or exclude_extensions(filepath):
                        continue
                    
                    # If the file is already in our previous query results, we move to the next file.
                    if confirm_data:                        
                        if (server_dirs, file) in relevant_file_data_list:
                            continue
                    
                    location_scrape_log["Files Enqueued to Add"] += 1
                    add_file_params = {"filepath": filepath}
                    utils.RQTaskUtils.enqueue_new_task(db=db,
                                                       enqueued_function=add_file_to_db_task, 
                                                       task_kwargs=add_file_params)
                    
                except Exception as e:
                    e_dict = {"Location": root,
                              "filename": file,
                              "Exception": str(e),
                              "Traceback": traceback.format_exc()}
                    location_scrape_log["Errors"].append(e_dict)
            
            if scrape_location == root and not recursively:
                break
        
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=location_scrape_log)
        return location_scrape_log
    

def consolidate_dirs_edit_task(user_target_path, user_destination_path, user_id, queue_id, removal_timeout = 1200, remove_target = True):
    """
    Task function to be enqueued for enqueueing subsequent server move edits for moving contents of one directory to another directory.
    Also removes the target directory if remove_target is True.
    :param user_target_path: str: The path of the directory to be moved. Should be the path on the user's computer.
    :param user_destination_path: str: The path of the directory to which the contents are to be moved. Should be the path on the user's computer.
    :param user_id: int: The id of the user who initiated the move.
    :param queue_id: str: The id of the task in the worker queue.
    :param removal_timeout: int: While removing the target directory, the time to wait for the dependent tasks to complete.
    :param remove_target: bool: If True, the target directory is removed after the contents are moved.
    """

    from archives_application.archiver.server_edit import ServerEdit
    with app.app_context():
        log = {"task_id": queue_id, 'items_moved':[], 'errors':[], 'removal':{}}
        db = flask.current_app.extensions['sqlalchemy']
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        try:
            nqed_move_tasks = []
            archive_location = flask.current_app.config.get('ARCHIVES_LOCATION')
            target_app_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=user_target_path,
                                                                        app=flask.current_app)
            target_contents = os.listdir(target_app_path)
            for some_item in target_contents:
                try:

                    user_item_path = os.path.join(user_target_path, some_item)
                    item_edit = ServerEdit(server_location=archive_location,
                                           old_path=user_item_path,
                                           new_path=user_destination_path,
                                           change_type='MOVE',
                                           exclusion_functions=[exclude_filenames, exclude_extensions])
                    edit_nq_result = item_edit.execute()
                    nqed_move_tasks.append(edit_nq_result.get('task_id')) if edit_nq_result else None

                    # record the move in the database
                    item_edit_model = ServerChangeModel(old_path = item_edit.old_path,
                                                        new_path = item_edit.new_path,
                                                        change_type = item_edit.change_type,
                                                        files_effected = item_edit.files_effected,
                                                        data_effected = item_edit.data_effected,
                                                        date = datetime.now(),
                                                        user_id = user_id)
                    db.session.add(item_edit_model)
                    log['items_moved'] = target_contents
                
                except Exception as e:
                    e_dict = {"Item": os.path.join(user_target_path, some_item),
                            "Exception": str(e)}
                    log["errors"].append(e_dict)
            db.session.commit()

            # if the target directory is to be removed, wait until the contents are moved to remove the target directory.
            if remove_target:
                
                # enque a task to remove the target directory after the contents have been moved.
                removal_params = {"dependent_tasks": nqed_move_tasks,
                                  "target_path": target_app_path,
                                  "removal_timeout": removal_timeout}
                nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                                enqueued_function=consolidation_target_removal_task,
                                                                task_kwargs=removal_params)
                log['removal'] = utils.serializable_dict(nq_results)

        except Exception as e:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            e_dict = {"Exception": str(e),
                      "Traceback": traceback.format_exc()}
            log["errors"].append(e_dict)
        
        log = utils.serializable_dict(log)
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log


def consolidation_target_removal_task(dependent_tasks: list, target_path: str, queue_id, removal_timeout: int = 1200):
    """
    Sister task to batch_server_move_edits_task. Removes the target directory after the contents have been moved.
    :param dependent_tasks: list: The list of task ids of the tasks that need to be completed before the target is removed.
    :param target_path: str: The path of the directory to be removed.
    :param queue_id: str: The id of this task in the worker queue.
    :param removal_timeout: int: The time to wait for the dependent tasks to complete before removing the target directory.
    """
    def all_listed_tasks_completed(task_ids, db):
        """
        Checks if all the tasks in the list of task_ids are finished.
        """
        tasks_db_entries = db.session.query(WorkerTaskModel).filter(WorkerTaskModel.task_id.in_(task_ids)).all()
        return all([task.status == 'finished' for task in tasks_db_entries])


    with app.app_context():
        log = {"task_id": queue_id, 'errors': []}
        db = flask.current_app.extensions['sqlalchemy']
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        try:
            # Sit in a loop until all the tasks in the list of dependent_tasks are finished.
            start_time = time.time()
            while not all_listed_tasks_completed(dependent_tasks, db):
                if time.time() - start_time > 900:
                    e_mssg = f'Timeout of 900 seconds reached before all dependent tasks could be completed.\nThe target was not removed.'
                    raise Exception(e_mssg)
                # pause for 5 seconds before checking again
                time.sleep(5)

            # if the target is not empty, raise an error
            if os.listdir(target_path) != []:
                raise Exception(f'Target directory {target_path} is not empty after attempting to move its contents.')

            # if the target is empty, we remove it.
            os.rmdir(target_path)            

        except Exception as e:
            e_dict = {"Target Path": target_path,
                      "Exception": str(e),
                      "Traceback": traceback.format_exc()}
            log["errors"].append(e_dict)
        
        log = utils.serializable_dict(log)
        utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
        return log


def batch_move_edits_task(user_target_path, user_contents_to_move, user_destination_path, user_id, queue_id):
    """
    Task function to be enqueued for moving the contents of one directory to another directory.
    :param user_target_path: str: The path of the directory to be moved. Should be the path on the user's computer.
    :param user_contents_to_move: list: The list of the contents of the target directory to be moved.
    :param user_destination_path: str: The path of the directory to which the contents are to be moved. Should be the path on the user's computer.
    :param user_id: int: The id of the user who initiated the move.
    :param queue_id: str: The id of the task in the worker queue.
    """
    from archives_application.archiver.server_edit import ServerEdit
    with app.app_context():
        log = {"task_id": queue_id, 'items_moved':[], 'errors':[]}
        db = flask.current_app.extensions['sqlalchemy']
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        try:
            nqed_move_tasks = []
            archive_location = flask.current_app.config.get('ARCHIVES_LOCATION')
            
            # test existence of target and destination directories
            app_target_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=user_target_path,
                                                                        app=flask.current_app)
            app_destination_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=user_destination_path,
                                                                             app=flask.current_app)
            if not os.path.exists(app_target_path):
                raise Exception(f"Target directory does not exist: {user_target_path}")
            if not os.path.exists(app_destination_path):
                raise Exception(f"Destination directory does not exist: {user_destination_path}")
            
            
            for some_item in user_contents_to_move:
                try:
                    user_item_path = os.path.join(user_target_path, some_item)
                    item_edit = ServerEdit(server_location=archive_location,
                                           old_path=user_item_path,
                                           new_path=user_destination_path,
                                           change_type='MOVE',
                                           exclusion_functions=[exclude_filenames, exclude_extensions])
                    edit_nq_result = item_edit.execute()
                    nqed_move_tasks.append(edit_nq_result.get('task_id')) if edit_nq_result else None

                    # record the move in the database
                    item_edit_model = ServerChangeModel(old_path = item_edit.old_path,
                                                        new_path = item_edit.new_path,
                                                        change_type = item_edit.change_type,
                                                        files_effected = item_edit.files_effected,
                                                        data_effected = item_edit.data_effected,
                                                        date = datetime.now(),
                                                        user_id = user_id)
                    db.session.add(item_edit_model)
                    log['items_moved'] = user_contents_to_move
                
                except Exception as e:
                    e_dict = {"Item": os.path.join(user_target_path, some_item),
                              "Exception": str(e),
                              "Traceback": traceback.format_exc()}
                    log["errors"].append(e_dict)
            db.session.commit()
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log

        except Exception as e:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            log["errors"].append({
                "Exception": str(e),
                "Traceback": traceback.format_exc()
            })
            utils.RQTaskUtils.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log


def batch_process_inbox_task(user_id: str, inbox_path: str, notes: str, items_to_archive: list[str], project_number: str, destination_dir: str, destination_path: str, queue_id: str):
    """
    Task function to be enqueued for archiving items in the inbox.
    :param user_id: str: The id of the user who initiated the archiving.
    :param inbox_path: str: The path of the inbox directory.
    :param items_to_archive: list: The list of items to be archived.
    :param project_number: str: The project number to which the items are to be archived.
    :param destination_dir: str: The directory in the project to which the items are to be archived.
    :param destination_path: str: The full path of the directory in the project to which the items are to be archived.
    :param queue_id: str: The id of the task in the worker queue.
    """
    from archives_application.archiver.archival_file import ArchivalFile
    from archives_application.models import ArchivedFileModel

    with app.app_context():

        db = flask.current_app.extensions['sqlalchemy']
        utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
        items_to_archive = {item: {'archived': False} for item in items_to_archive}
        log = {"task_id": queue_id, 'items_to_archived':items_to_archive, 'errors':[]}
        try:
            archives_location = flask.current_app.config.get('ARCHIVES_LOCATION')
            for some_item, _ in items_to_archive.items():
                try:
                    app_destination_path = None
                    if destination_path:
                        app_destination_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=destination_path,
                                                                                         app=flask.current_app)
                        app_destination_path = os.path.join(app_destination_path, some_item)
                    item_path = os.path.join(inbox_path, some_item)
                    if not os.path.exists(item_path):
                        raise Exception(f"Item does not exist: {item_path}")
                    
                    item_size = os.path.getsize(item_path)
                    item_to_archive = ArchivalFile(current_path=item_path,
                                                   archives_location=archives_location,
                                                   directory_choices=flask.current_app.config.get('DIRECTORY_CHOICES'),
                                                   project=project_number,
                                                   destination_dir=destination_dir,
                                                   destination_path=app_destination_path,
                                                   new_filename=some_item,
                                                   notes=notes)
                    archiving_success, archiving_error = item_to_archive.archive_in_destination()

                    if not archiving_success:
                        log['errors'].append(f"Error archiving {item_path}: {archiving_error}")
                        continue

                    log["items_to_archived"][some_item]['archived'] = True

                    # add the archiving event to the database
                    recorded_filing_code = destination_dir if not destination_path else None
                    recorded_destination_path = utils.FileServerUtils.archive_relative_path(
                        item_to_archive.get_destination_path(),
                        archives_location
                    )
                    archived_file = ArchivedFileModel(destination_path=recorded_destination_path,
                                                      archivist_id=user_id,
                                                      project_number=project_number,
                                                      date_archived=datetime.now(),
                                                      destination_directory=item_to_archive.destination_dir,
                                                      file_code = recorded_filing_code,
                                                      file_size = item_size,
                                                      filename=item_to_archive.new_filename)
                    db.session.add(archived_file)
                    db.session.commit()

                    # enqueue a task to add the file to the database
                    add_file_params = {"filepath": item_to_archive.get_destination_path(),
                                       "archiving": True}
                    nq_results = utils.RQTaskUtils.enqueue_new_task(db=db,
                                                                    enqueued_function=add_file_to_db_task,
                                                                    task_kwargs=add_file_params)
                except Exception as e:
                    log['errors'].append(f"Error archiving {item_path}:\nException: {str(e)}\nTraceback: {traceback.format_exc()}")
                    continue
            
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log
        
        except Exception as e:
            utils.FlaskAppUtils.attempt_db_rollback(db)
            e_dict = {"Exception": str(e),
                      "Traceback": traceback.format_exc()}
            log["errors"].append(e_dict)
            utils.RQTaskUtils.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=log)
            return log






