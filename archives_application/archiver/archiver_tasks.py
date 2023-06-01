from archives_application import utilities, create_app
from archives_application.models import FileLocationModel, FileModel, WorkerTask

import flask
import os
import time
from datetime import timedelta, datetime
from typing import Callable


# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()

def add_file_to_db(filepath: str):
    """
    This function adds a file to the database.
    """
    with app.app_context():
        pass

def scrape_file_data(archives_location: str, start_location: str, file_server_root_index: int,
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
        
        # update the database to indicate that the task has started
        start_task_db_updates = {"status": 'started'}
        db.session.query(WorkerTask).filter(WorkerTask.task_id == queue_id).update(start_task_db_updates)
        db.session.commit()

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

        for root, _, files in os.walk(archives_location):

            # if the time limit for scraping has passed, we end the scraping loop
            if timedelta(seconds=(time.time() - start_time)) >= scrape_time:
                # process root to be agnostic to where the archives location is mounted
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
                    e_dict = {"Filepath": file, "Exception": str(e)}
                    scrape_log["Errors"].append(e_dict)

        # update the task entry in the database
        scrape_log["Time Elapsed"] = str(time.time() - start_time) + "s"
        task_db_updates = {"status": 'finished', "task_results": scrape_log, "time_completed":datetime.now()}
        db.session.query(WorkerTask).filter(WorkerTask.task_id == queue_id).update(task_db_updates)
        db.session.commit()
        return scrape_log


def confirm_file_locations(archive_location: str, confirming_time: timedelta, queue_id: str):
    with app.app_context():
        db = flask.current_app.extensions['sqlalchemy'].db
        
        # update the database to indicate that the task has started
        start_task_db_updates = {"status": 'started'}
        db.session.query(WorkerTask).filter(WorkerTask.task_id == queue_id).update(start_task_db_updates)
        db.session.commit()

        start_time = time.time()
        confirm_locations_log = {"Confirm Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
                                 "Errors": [],
                                 "Files Missing": 0,
                                 "Files Removed": 0,
                                 "Files Confirmed": 0}
        file_location_entries = db.session.query(FileLocationModel).order_by(db.desc(FileLocationModel.existence_confirmed)).yield_per(1000)
        for file_location in file_location_entries:
            try:
                if timedelta(seconds=(time.time() - start_time)) >= confirming_time:
                    break
                
                file_location_path = os.path.join(archive_location, file_location.file_server_directories, file_location.filename)
                
                # if the file no longer exists, we delete the entry in the database
                if not os.path.exists(file_location_path):
                    confirm_locations_log["Files Missing"] += 1
                    file_id = file_location.file_id
                    db.session.delete(file_location)
                    db.session.commit()
                    
                    # if there are no other locations for this file, we delete entry in the files table
                    other_locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == file_id).all()
                    if other_locations == []:
                        db.session.query(FileModel).filter(FileModel.id == file_id).delete()
                        db.session.commit()
                        confirm_locations_log["Files Removed"] += 1
                
                else:
                    # if the file exists, we update the existence_confirmed date of this file_locations entry
                    db.session.query(FileLocationModel).filter(FileLocationModel.id == file_location.id).update({"existence_confirmed": datetime.now()})
                    db.session.commit()
                    confirm_locations_log["Files Confirmed"] += 1
            
            except Exception as e:
                e_dict = {"Location": file_location.file_server_directories,
                        "filename": file_location.filename,
                        "Exception": str(e)}
                confirm_locations_log["Errors"].append(e_dict)
                
        
        # update the task entry in the database
        confirm_locations_log["Time Elapsed"] = str(time.time() - start_time) + "s"
        task_db_updates = {"status": 'finished', "task_results": confirm_locations_log, "time_completed":datetime.now()}
        db.session.query(WorkerTask).filter(WorkerTask.task_id == queue_id).update(task_db_updates)
        db.session.commit()
        return confirm_locations_log
