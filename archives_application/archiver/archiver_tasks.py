from archives_application import utilities, create_app
from archives_application.models import ArchivedFileModel, FileLocationModel, FileModel, WorkerTaskModel

import flask
import os
import time
from datetime import timedelta, datetime
from typing import Callable


# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()


def add_file_to_db_task(filepath: str,  queue_id: str, archiving: bool = False):
    """
    This function adds a file to the database.
    """
    with app.app_context():
        task_results = {'queue_id': queue_id, 'filepath': filepath}
        try:
            db = flask.current_app.extensions['sqlalchemy'].db
            utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            
            file_hash = utilities.get_hash(filepath)
            file_id = None
            filename = utilities.split_path(filepath)[-1]
            
            # check if the file is already in the database and add it if it is not
            while not file_id:
                db_file_entry = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
                if not db_file_entry:
                    file_ext = filename.split('.')[-1]
                    file_size = os.path.getsize(filepath)
                    new_file = FileModel(hash=file_hash, size=file_size, extension=file_ext)
                    db.session.add(new_file)
                    db.session.commit()
                    file_id = new_file.id #TODO does this value exist after the commit?
                else:
                    file_id = db_file_entry.id
            
            # extract the path from the root of the windows share
            file_server_root_index = len(utilities.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            server_directories = filepath[:-(len(filename)-1)]
            task_results['server_directories'] = server_directories # TODO remove this line after debugging
            task_results['root_index'] = file_server_root_index # TODO remove this line after debugging
            server_dirs_list = utilities.split_path(server_directories)[file_server_root_index:]
            server_directories = os.path.join(*server_dirs_list)

            # check if the file location is already in the database and add it if it is not
            db_file_location_entry = db.session.query(FileLocationModel).filter(FileLocationModel.file_server_directories == server_directories,
                                                                                FileLocationModel.filename == filename).first()
            
            # if there is already already a file location that is the same as this loction,
            # but the files are different, we remove the old file location and add the new one.
            if db_file_location_entry and (db_file_location_entry.file_id != file_id):
                db.session.delete(db_file_location_entry)
                db.session.commit()
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
                #TODO need to test if this query is working correctly
                search_path = os.path.join(server_directories, filename)
                archived_file = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.destination_path.endswith(search_path),
                                                                        ArchivedFileModel.filename == filename)\
                                                                            .order_by(db.asc(ArchivedFileModel.date_archived)).first()
                if archived_file:
                    archived_file.file_id = file_id
                else:
                    task_results['error'] = f'Could not find archived file with path {search_path} in database.'
                db.session.commit()
            task_results["file_id"] = file_id 
            task_results["filepath"] = filepath
            utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=task_results)
            return file_id
        
        except Exception as e:
            task_results['error'] = str(e)
            utilities.failed_task_subroutine(q_id=queue_id, sql_db=db, task_result=task_results)



def scrape_file_data_task(archives_location: str, start_location: str, file_server_root_index: int,
                          exclusion_functions: list[Callable[[str], bool]], scrape_time: timedelta,
                          queue_id: str):
    """
    This function scrapes file data from the archives file server and adds it to the database.
    
    :param archives_location: The location of the archives file server.
    :param start_location: The location from which to start scraping file data.
    :param file_server_root_index: The index of the file server root in the file server path.
    :param exclusion_functions: A list of functions that take a file path as input and return True if the file
    should be excluded from the scraping process.
    :param scrape_time: The amount of time to spend scraping file data.
    :param queue_id: The id of task in the worker queue.
    """
    
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy'].db
        utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)

        # create a log of the scraping process
        scrape_log = {"Scrape Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
                    "This Start  Location": start_location,
                    "Files Added":0,
                    "File Locations Added":0,
                    "Files Confirmed":0,
                    "Errors":[],
                    "Time Elapsed":0,
                    "Next Start Location": start_location}
        start_time = time.time()
        start_location_found = False
        scrape_time_expired = False
        start_location_root_found = False
        root_dirs_paths = [os.path.join(archives_location, d) for d in os.listdir(archives_location)]
        

        # iterate through the root of the file share to find the root directory to start scrape from
        for root_dir in root_dirs_paths:
            if scrape_time_expired:
                break

            if not start_location_root_found:
                if start_location.startswith(root_dir):
                    start_location_root_found = True
                else:
                    continue
            
            for root, _, files in os.walk(root_dir):

                # if the time limit for scraping has passed, we end the scraping loop
                if timedelta(seconds=(time.time() - start_time)) >= scrape_time:
                    # process root to be agnostic to where the archives location is mounted
                    scrape_time_expired = True
                    next_start = utilities.split_path(root)[file_server_root_index:]
                    scrape_log["Next Start Location"] = os.path.join(*next_start)
                    break

                # We iterate through the archives folder structure until we find the location from which we want to start
                # scraping file data.
                if root == start_location:
                    start_location_found = True

                if not start_location_found:
                    continue

                filepaths = [os.path.join(root, f) for f in files]
                for file in filepaths:
                    try:
                        # if the file is excluded by one of the exclusion functions, move to next file
                        if any([fun(file) for fun in exclusion_functions]):
                            continue
                        
                        # if the file is empty, move to next file
                        file_size = os.path.getsize(file)
                        if file_size == 0:
                            continue

                        # if there is not an equivalent entry in database, we add it.
                        file_is_new = False # flag to indicate if the file is new to the database
                        file_hash = utilities.get_hash(filepath=file)
                        db_file_entry = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
                        if not db_file_entry:
                            file_is_new = True
                            path_list = utilities.split_path(file)
                            extension = path_list[-1].split(".")[-1].lower()
                            model = FileModel(hash=file_hash,
                                            size=file_size,
                                            extension=extension)
                            db.session.add(model)
                            db.session.commit()
                            db_file_entry = db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
                            scrape_log["Files Added"] += 1

                        path_list = utilities.split_path(file)
                        # This is for if there is a file in the root directory of the share (eg R:\some_file.pdf) )
                        file_server_dirs = ""
                        if path_list[file_server_root_index:-1] != []:
                            file_server_dirs = os.path.join(*path_list[file_server_root_index:-1])
                        filename = path_list[-1]
                        confirmed_exists_dt = datetime.now()
                        confirmed_hash_dt = datetime.now()
                        
                        # If the file is not new, we check if the path is already represented in the database
                        # and update the file database entryto reflect that the file has been checked.
                        if not file_is_new:

                            # query to see if the current path is already represented in the database
                            db_path_entry = db.session.query(FileLocationModel).filter(
                                FileLocationModel.file_server_directories == file_server_dirs,
                                FileLocationModel.filename == filename).first()

                            # If there is an entry for this path in the database update the dates now we have confirmed location and
                            # that the file has not changed (hash is same.)
                            if db_path_entry:
                                entry_updates = {"existence_confirmed": confirmed_exists_dt,
                                                "hash_confirmed": confirmed_hash_dt}
                                db.session.query(FileLocationModel).filter(
                                    FileLocationModel.file_server_directories == file_server_dirs,
                                    FileLocationModel.filename == filename).update(entry_updates)
                                db.session.commit()
                                scrape_log["Files Confirmed"] += 1
                                continue

                        new_location = FileLocationModel(file_id=db_file_entry.id,
                                                        file_server_directories=file_server_dirs,
                                                        filename=filename, existence_confirmed=confirmed_exists_dt,
                                                        hash_confirmed=confirmed_hash_dt)
                        db.session.add(new_location)
                        db.session.commit()
                        scrape_log["File Locations Added"] += 1

                    except Exception as e:
                        print(str(e) + "\n" + file)
                        db.session.rollback()
                        e_dict = {"Filepath": file, "Exception": str(e)}
                        scrape_log["Errors"].append(e_dict)

        # update the task entry in the database
        scrape_log["Time Elapsed"] = str(time.time() - start_time) + "s"
        utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=scrape_log)
        return scrape_log


def confirm_file_locations_task(archive_location: str, confirming_time: timedelta, queue_id: str):
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy'].db
        utilities.initiate_task_subroutine(q_id=queue_id, sql_db=db)

        start_time = time.time()
        confirm_locations_log = {"Confirm Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
                                 "Errors": [],
                                 "Locations Missing": 0,
                                 "Files Removed": 0,
                                 "Files Confirmed": 0}
        
        # We iterate through the file locations in the database, 1000 at a time, and check if the file still exists.
        while timedelta(seconds=(time.time() - start_time)) < confirming_time:
            file_location_entries = db.session.query(FileLocationModel).order_by(db.asc(FileLocationModel.existence_confirmed)).limit(1000)
            for file_location in file_location_entries:
                try:
                    if timedelta(seconds=(time.time() - start_time)) >= confirming_time:
                        break
                    
                    file_location_path = os.path.join(archive_location, file_location.file_server_directories, file_location.filename)
                    
                    # if the file no longer exists, we delete the entry in the database
                    if not os.path.exists(file_location_path):
                        confirm_locations_log["Locations Missing"] += 1
                        file_id = file_location.file_id
                        db.session.delete(file_location)
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
                            confirm_locations_log["Files Removed"] += 1
                    
                    else:
                        # if the file exists, we update the existence_confirmed date of this file_locations entry
                        db.session.query(FileLocationModel).filter(FileLocationModel.id == file_location.id).update({"existence_confirmed": datetime.now()})
                        db.session.commit()
                        confirm_locations_log["Files Confirmed"] += 1
                
                except Exception as e:
                    db.session.rollback()
                    e_dict = {"Location": file_location.file_server_directories,
                            "filename": file_location.filename,
                            "Exception": str(e)}
                    confirm_locations_log["Errors"].append(e_dict)
                
        # update the task entry in the database
        confirm_locations_log["Time Elapsed"] = str(time.time() - start_time) + "s"
        utilities.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=confirm_locations_log)
        return confirm_locations_log
