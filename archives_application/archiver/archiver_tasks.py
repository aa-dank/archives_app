from archives_application import utilities, create_app
from archives_application.models import FileLocationModel, FileModel, WorkerTask


import os
import time
from datetime import timedelta, datetime
from typing import Callable


app = create_app()


def scrape_file_data(archives_location: str, start_location: str, file_server_root_index: int,
                     exclusion_functions: list[Callable[[str], bool]], scrape_time: timedelta,
                     queue_id: str):

    with app.app_context():
        scrape_log = {"Scrape Date": datetime.now().strftime(r"%m/%d/%Y, %H:%M:%S"),
                    "This Start  Location": start_location,
                    "Files Added":0,
                    "File Locations Added":0,
                    "Errors":[],
                    "Time Elapsed":0,
                    "Next Start Location": start_location}
        start_time = time.time()
        start_location_found = False

        for root, _, files in os.walk(archives_location):

            # if the time limit for scraping has passed, we end the scraping loop
            if timedelta(seconds=(time.time() - start_time)) >= scrape_time:
                scrape_log["Next Start Location"] = root
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
                    file_is_new = False
                    file_hash = utilities.get_hash(filepath=file)
                    db_file_entry = app.db.session.query(FileModel).filter(FileModel.hash == file_hash).first()

                    # if there is not an equivalent entry in database, we add it.
                    if not db_file_entry:
                        file_is_new = True
                        file_size = os.path.getsize(file)
                        path_list = utilities.split_path(file)
                        extension = path_list[-1].split(".")[-1].lower()
                        model = FileModel(hash=file_hash,
                                        size=file_size,
                                        extension=extension)
                        app.db.session.add(model)
                        app.db.session.commit()
                        db_file_entry = app.db.session.query(FileModel).filter(FileModel.hash == file_hash).first()
                        scrape_log["Files Added"] += 1

                    path_list = utilities.split_path(file)
                    file_server_dirs = os.path.join(*path_list[file_server_root_index:-1])
                    filename = path_list[-1]
                    confirmed_exists_dt = datetime.now()
                    confirmed_hash_dt = datetime.now()
                    if not file_is_new:

                        # query to see if the current path is already represented in the database
                        db_path_entry = app.db.session.query(FileLocationModel).filter(
                            FileLocationModel.file_server_directories == file_server_dirs,
                            FileLocationModel.filename == filename).first()

                        # if there is an entry for this path in the database update the dates now we have confirmed location and
                        # that the file has not changed (hash is same.)
                        if db_path_entry:
                            entry_updates = {"existence_confirmed": confirmed_exists_dt,
                                            "hash_confirmed": confirmed_hash_dt}
                            app.db.session.query(FileLocationModel).filter(
                                FileLocationModel.file_server_directories == file_server_dirs,
                                FileLocationModel.filename == filename).update(entry_updates)

                            app.db.session.commit()
                            continue

                    new_location = FileLocationModel(file_id=db_file_entry.id,
                                                    file_server_directories=file_server_dirs,
                                                    filename=filename, existence_confirmed=confirmed_exists_dt,
                                                    hash_confirmed=confirmed_hash_dt)
                    app.db.session.add(new_location)
                    app.db.session.commit()
                    scrape_log["File Locations Added"] += 1

                except Exception as e:
                    e_dict = {"Filepath": file, "Exception": str(e)}
                    scrape_log["Errors"].append(e_dict)

        # update the task entry in the database
        scrape_log["Time Elapsed"] = str(time.time() - start_time) + "s"
        task_db_updates = {"status": 'finished', "result": scrape_log, "time_completed":datetime.now()}
        app.db.session.query(WorkerTask).filter(WorkerTask.id == queue_id).update(task_db_updates)
        app.db.session.commit()
        return scrape_log