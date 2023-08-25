import datetime
import flask
import os
import random
import shutil
from sqlalchemy import text, func
from archives_application import create_app, utils
from archives_application.archiver import archiver_tasks
from archives_application.models import  FileLocationModel, FileModel, ArchivedFileModel

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()


class ServerEdit:
    def __init__(self, server_location, change_type, user, new_path=None, old_path=None):
        """

        :param server_location:
        :param change_type:
        :param new_path: resulting path after the change will be made
        :param old_path: resultin
        :param user:
        :return:
        """
        self.change_type = change_type
        self.new_path = None
        if new_path:
            self.new_path = utils.user_path_to_app_path(path_from_user=new_path,
                                                            location_path_prefix=server_location)

        self.old_path = None
        if old_path:
            self.old_path = utils.user_path_to_app_path(path_from_user=old_path,
                                                            location_path_prefix=server_location)
            if not os.path.exists(self.old_path):
                e_message = f"Path to asset does not exists: {self.new_path}\nEntered path: {old_path}"
                raise Exception(e_message)

            if self.old_path == server_location:
                raise Exception("Server root directory chosen")
        self.user = user
        self.change_executed = False
        self.data_effected = 0
        self.files_effected = 0
        
        # if the serveredit is a move, change, or edit, determine if the change is to a file or directory
        self.is_file = False
        if self.old_path:
            self.is_file = os.path.isfile(self.old_path)

    def execute(self, files_limit = 500, effected_data_limit=500000000):
        """
        This function executes the server change that was specified during the creation of the ServerEdit object. The change can be of the following types:

        DELETE: deletes a file or directory at the specified old path.
        RENAME: renames a file or directory at the specified old path to the specified new path.
        MOVE: moves a file or directory at the specified old path to the specified new path.
        CREATE: creates a new file or directory at the specified new path.
        The function takes two optional parameters, files_limit and effected_data_limit, which sets limits for the number of files and the amount of data affected by the change, respectively. If either of these limits is breached during the execution of the change, an exception is raised.

        The function returns True if the change is successfully executed, False otherwise.

        :param files_limit: Maximum number of files that can be affected by the change (default is 500).
        :type files_limit: int
        :param effected_data_limit: Maximum amount of data that can be affected by the change (default is 50,000,000).
        :type effected_data_limit: int
        :return: True if the change is successfully executed, False otherwise.
        :rtype: bool
        """

        def check_against_limits():
            if effected_data_limit and self.data_effected and self.data_effected > effected_data_limit:
                raise Exception(
                    f"ServerEdit data limit breached. Too much data effected by change type '{self.change_type}'.\nOld path: {self.old_path}\nNew path: {self.new_path}")

            if files_limit and self.files_effected and self.files_effected > files_limit:
                raise Exception(
                    f"ServerEdit file limit breached. Too many files effected by change type '{self.change_type}.'\nOld path: {self.old_path}\nNew path: {self.new_path}")


        def get_quantity_effected(dir_path):
            #TODO this should be a database query, not a loop
            
            for root, _, files in os.walk(dir_path):
                for file in files:
                    self.files_effected += 1
                    self.data_effected += os.path.getsize(os.path.join(root, file))


        # If the change type is 'DELETE'
        if self.change_type.upper() == 'DELETE':
            enqueueing_results = {}
            if self.is_file:
                self.data_effected = os.path.getsize(self.old_path)
                os.remove(self.old_path)
                self.change_executed = True
                self.files_effected = 1
                enqueueing_results = utils.enqueue_new_task(db= flask.current_app.extensions['sqlalchemy'].db,
                                                                enqueued_function=self.add_deletion_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
                return enqueueing_results

            # if the deleted asset is a dir we need to add up all the files and their sizes before removing
            get_quantity_effected(self.old_path)

            # make sure change is not in excess of limits set
            check_against_limits()

            # remove directory and contents
            shutil.rmtree(self.old_path)
            self.change_executed = True
            enqueueing_results = utils.enqueue_new_task(db= flask.current_app.extensions['sqlalchemy'].db,
                                                            enqueued_function=self.add_deletion_to_db_task)
            # for testing:
            #self.add_deletion_to_db_task(task_id=f"{self.add_deletion_to_db_task.__name__}_test01")
            enqueueing_results['change_executed'] = self.change_executed
            return enqueueing_results

        # If the change type is 'RENAME'
        if self.change_type.upper() == 'RENAME':
            enqueueing_results = {}
            old_path = self.old_path
            old_path_list = utils.split_path(old_path)
            new_path_list = utils.split_path(self.new_path)
            if not len(old_path_list) == len(new_path_list):
                raise Exception(
                    f"Attempt at renaming paths failed. Parent directories are not the same: \n {self.new_path}\n{self.old_path}")

            # if this a change of a filepath, we need to cleanse the filename
            if self.is_file:
                self.files_effected = 1
                self.data_effected = os.path.getsize(old_path) #bytes
                new_path_list[-1] = utils.cleanse_filename(new_path_list[-1])

            else:
                get_quantity_effected(self.old_path)

            # make sure change is not in excess of limits set
            check_against_limits()
            if old_path == self.new_path:
                return self.change_executed
                
            try:
                os.rename(self.old_path, self.new_path)
                self.change_executed = True
                enqueueing_results = utils.enqueue_new_task(db= flask.current_app.extensions['sqlalchemy'].db,
                                                                enqueued_function=self.add_renaming_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
            except Exception as e:
                raise Exception(f"There was an issue trying to change the name. If it is permissions issue, consider that it might be someone using a directory that would be changed \n{e}")

            return enqueueing_results

        # if the change_type is 'MOVE'
        if self.change_type.upper() == 'MOVE':
            try:
                if os.path.isfile(self.old_path):
                    filename = utils.split_path(self.old_path)[-1]
                    destination_path = os.path.join(self.new_path, filename)
                    self.files_effected = 1
                    self.data_effected = os.path.getsize(self.old_path)
                    shutil.copyfile(src=self.old_path, dst=destination_path)
                    os.remove(self.old_path)
                    self.change_executed = True
                
                else:
                    # make sure change is not in excess of limits set
                    get_quantity_effected(self.old_path)
                    check_against_limits()

                    # cannot move a directory within itself
                    if self.new_path.startswith(self.old_path):
                        raise Exception(
                            f"Cannot move a directory within itself. \nOld path: {self.old_path}\nNew path: {self.new_path}")

                    # move directory and contents
                    shutil.move(self.old_path, self.new_path, copy_function=shutil.copytree)
                    self.change_executed = True
                
                # for testing:
                #db_edit = self.add_move_to_db_task(queue_id=f"{self.add_deletion_to_db_task.__name__}_test{random.randint(1, 1000)}")
                #return db_edit
                
                enqueueing_results = utils.enqueue_new_task(db= flask.current_app.extensions['sqlalchemy'].db,
                                                                enqueued_function=self.add_move_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
                return enqueueing_results
            
            except Exception as e:
                if type(e) == shutil.Error:
                    e_str = f"Exception trying to move the directory. Is there a collision with an existing file/directory? If it is permissions issue, consider that it might be someone using a directory that would be changed \n{e}"
                    raise Exception(e_str)

        # if the change_type is 'MAKE'
        if self.change_type.upper() == 'MAKE':
            if os.path.exists(self.new_path):
                raise Warning(f"Trying to make a directory that already exists: {self.new_path}")
            os.makedirs(self.new_path)
            self.change_executed = True
            self.files_effected = 0
            self.data_effected = 0
            results = {'change_executed': self.change_executed}
            return results

        results = {'change_executed': self.change_executed}
        return results

    
    @staticmethod
    def remove_file_from_db(db, root_index, file_path):
        """
        Removes a file location from database and if it is the last entry for that file_id, removes the file db entry too.
        :param db: SQLAlchemy database object
        :param root_index: index of the server share directory in the file path
        :param file_path: path to the file
        """

        filename = utils.split_path(file_path)[-1]
        db_path = os.path.join(*utils.split_path(file_path)[root_index:-1])   
        location_entry_removed = False
        file_entry_removed = False
        location_entry = db.session.query(FileLocationModel)\
            .filter(FileLocationModel.file_server_directories == db_path,
                    FileLocationModel.filename == filename)\
            .first()
        
        if location_entry:
            file_id = location_entry.file_id
            db.session.delete(location_entry)
            location_entry_removed = True

            other_entries = db.session.query(FileModel)\
                .filter(FileModel.id == file_id)\
                .all()
            
            # If this was the last entry for this file_id, delete the file_id entry
            if not other_entries:
                associated_archival_events = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.file_id == file_id).all()
                for archival_event in associated_archival_events:
                    archival_event.file_id = None
                
                db.session.query(FileModel)\
                    .filter(FileModel.id == file_id)\
                    .delete()
            db.session.commit()
            file_entry_removed = True
        return location_entry_removed, file_entry_removed
            

    def add_deletion_to_db_task(self, queue_id):
        
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            deletion_log = {}
            deletion_log['task_id'] = queue_id
            deletion_log['location_entries_effected'] = 0
            deletion_log['files_entries_effected'] = 0
            deletion_log['old_path'] = self.old_path

            if self.is_file:
                server_dirs = utils.split_path(self.old_path)[file_server_root_index:-1]
                filename = utils.split_path(self.old_path)[-1]
                server_path = os.path.join(*server_dirs)
                file_location_entry = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories == server_path,
                            FileLocationModel.filename == filename).first()

                if file_location_entry:
                    file_id = file_location_entry.file_id
                    db.session.delete(file_location_entry)
                    db.session.commit()
                    deletion_log['location_entries_effected'] = 1
                    
                    # If this was the last entry for this file_id, delete the file_id entry
                    other_locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == file_id).all()
                    if not other_locations:
                        # update any associated archival events so file_id is NULL
                        associated_archival_events = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.file_id == file_id).all()
                        for archival_event in associated_archival_events:
                            archival_event.file_id = None

                        file_entry = db.session.query(FileModel).filter(FileModel.id == file_id).first()
                        db.session.delete(file_entry)
                        db.session.commit()
                        deletion_log['files_entries_effected'] = 1

            else:
                server_dirs = utils.split_path(self.old_path)[file_server_root_index:]
                server_path = os.path.join(*server_dirs)
                file_location_entries = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories.like(func.concat(server_path, '%'))).all()
                for file_location_entry in file_location_entries:
                    file_id = file_location_entry.file_id
                    db.session.delete(file_location_entry)
                    db.session.commit()
                    deletion_log['location_entries_effected'] += 1
                    other_locations = db.session.query(FileLocationModel).filter(FileLocationModel.file_id == file_id).all()
                    if not other_locations:
                        # update any associated archival events so file_id is NULL
                        associated_archival_events = db.session.query(ArchivedFileModel).filter(ArchivedFileModel.file_id == file_id).all()
                        for archival_event in associated_archival_events:
                            archival_event.file_id = None

                        file_entry = db.session.query(FileModel).filter(FileModel.id == file_id).first()
                        db.session.delete(file_entry)
                        db.session.commit()
                        deletion_log['files_entries_effected'] += 1
            
            utils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=deletion_log)
            return deletion_log
    

    def add_renaming_to_db_task(self, queue_id):

        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            rename_log = {}
            rename_log['task_id'] = queue_id
            rename_log['location_entries_effected'] = 0
            rename_log['files_entries_effected'] = 0
            rename_log['old_path'] = self.old_path
            rename_log['new_path'] = self.new_path

            if self.is_file:
                rename_log['is_file'] = True
                server_dirs = utils.split_path(self.old_path)[file_server_root_index:-1]
                old_filename = utils.split_path(self.old_path)[-1]
                new_filename = utils.split_path(self.new_path)[-1]
                server_path = os.path.join(*server_dirs)
                
                # first make sure that if there is already a file with the new name, it is deleted,
                # because it will have been replaced by the renamed file
                new_location_entry_removed, some_file_entry_removed = self.remove_file_from_db(db, file_server_root_index, self.new_path)
                if new_location_entry_removed:
                    rename_log['location_entries_effected'] += 1
                if some_file_entry_removed:
                    rename_log['files_entries_effected'] += 1
                
                old_file_location_entry = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories == server_path,
                            FileLocationModel.filename == old_filename).first()

                if old_file_location_entry:
                    old_file_location_entry.filename = new_filename
                    db.session.commit()
                    rename_log['location_entries_effected'] += 1

            # If the asset being renamed is a directory, we need to reflect the changed path 
            # for all files within the directory
            else:
                rename_log['is_file'] = False
                old_server_dirs = utils.split_path(self.old_path)[file_server_root_index:]
                old_server_path = os.path.join(*old_server_dirs)
                new_server_dirs = utils.split_path(self.new_path)[file_server_root_index:]
                new_server_path = os.path.join(*new_server_dirs)
                file_location_entries = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories.like(func.concat(old_server_path, '%'))).all()
                

                for file_location_entry in file_location_entries:
                    old_file_path = file_location_entry.file_server_directories
                    old_path_list = utils.split_path(old_file_path)
                    new_server_path = os.path.join(*new_server_dirs, *old_path_list[len(new_server_dirs):])
                    
                    #TODO what if a file with the same name already exists in the new location?
                    existing_file_location_entry = db.session.query(FileLocationModel)\
                        .filter(FileLocationModel.file_server_directories == new_server_path,
                                FileLocationModel.filename == file_location_entry.filename).first()
                    
                    if existing_file_location_entry:
                        existing_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'), new_server_path)
                        remove_file_entry, remove_location_entry = self.remove_file_from_db(db, file_server_root_index, file_path=existing_path)   
                        if remove_file_entry:
                            rename_log['files_entries_effected'] += 1
                        if remove_location_entry:
                            rename_log['location_entries_effected'] += 1
                    
                    file_location_entry.file_server_directories = new_server_path
                    file_location_entry.existence_confirmed = datetime.datetime.now()
                    db.session.commit()
                    rename_log['location_entries_effected'] += 1
            
            utils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=rename_log)
            return rename_log
                
    
    def add_move_to_db_task(self, queue_id):
        """
        Reconciles the database with a file move operation. Basically, it changes the file_server_directories
        for each file_location entry that is effected by the move. If the move is a file move, it also changes.

        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy'].db
            utils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            move_log = {}
            move_log['task_id'] = queue_id
            move_log['location_entries_effected'] = 0
            move_log['files_entries_effected'] = 0
            move_log['old_path'] = self.old_path
            move_log['new_path'] = self.new_path

            old_path_list = utils.split_path(self.old_path)
            new_path_list = utils.split_path(self.new_path)
            if self.is_file:
                old_server_dirs_list = old_path_list[file_server_root_index:-1]
                filename = old_path_list[-1]
                old_server_path = os.path.join(*old_server_dirs_list)
                new_server_path = os.path.join(*new_path_list[file_server_root_index:])

                # first make sure that if there is already a file with the new name, it is deleted,
                # because it will have been replaced by the moved file
                new_location_entry_removed, some_file_entry_removed = self.remove_file_from_db(db, file_server_root_index, os.path.join(self.new_path, filename))
                if new_location_entry_removed:
                    move_log['location_entries_effected'] += 1
                if some_file_entry_removed:
                    move_log['files_entries_effected'] += 1

                location_entry = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories == old_server_path,
                            FileLocationModel.filename == filename)\
                    .first()
                
                if location_entry:
                    location_entry.file_server_directories = new_server_path
                    if os.path.exists(os.path.join(self.new_path, filename)):
                        location_entry.existence_confirmed = datetime.datetime.now()
                    db.session.commit()
                    move_log['files_entries_effected'] += 1

                # if there is not an entry for the file in db, add it.
                else:
                    file_hash = utils.get_hash(os.path.join(self.new_path, filename))
                    file_entry = None
                    while not file_entry:
                        
                        file_entry = db.session.query(FileModel)\
                            .filter(FileModel.hash == file_hash).first()
                        if not file_entry:
                            file_entry = FileModel(hash=file_hash,
                                                   size=os.path.getsize(os.path.join(self.new_path, filename)),
                                                   extension=filename.split('.')[-1])
                            db.session.add(file_entry)
                            db.session.commit()
                            move_log['files_entries_effected'] += 1
                    
                    location_entry = FileLocationModel(file_server_directories=new_server_path,
                                                       filename=filename,
                                                       file_id=file_entry.id,
                                                       existence_confirmed=datetime.datetime.now(),
                                                       hash_confirmed=datetime.datetime.now())
                    db.session.add(location_entry)
                    db.session.commit()
                    move_log['location_entries_effected'] += 1
                    return move_log
            
            # if we are moving a directory, we need to move all files within the directory
            else:
                old_server_dirs_list = old_path_list[file_server_root_index:]
                old_server_path = os.path.join(*old_server_dirs_list)
                effected_location_entries = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories.like(func.concat(old_server_path, '%'))).all()
                
                for location_entry in effected_location_entries:
                    entry_dir_list = utils.split_path(location_entry.file_server_directories)
                    new_location_list = new_path_list[file_server_root_index:] + entry_dir_list[len(old_server_dirs_list)-1:]
                    new_location_server_dirs = os.path.join(*new_location_list)

                    # remove any entries that are already in the new location
                    existing_loc_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'), new_location_server_dirs, location_entry.filename)
                    remove_file_entry, remove_location_entry = self.remove_file_from_db(db, file_server_root_index, file_path=existing_loc_path)
                    if remove_file_entry:
                        move_log['files_entries_effected'] += 1
                    if remove_location_entry:
                        move_log['location_entries_effected'] += 1

                    file_exists = os.path.exists(os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'), new_location_server_dirs, location_entry.filename))
                    if file_exists:
                        location_entry.file_server_directories = new_location_server_dirs
                        location_entry.existence_confirmed = datetime.datetime.now()
                        move_log['location_entries_effected'] += 1
                    else:
                        db.session.delete(location_entry)
                        move_log['location_entries_effected'] += 1
                    db.session.commit()

                # check that all of the moved files are in the database by checking if they are in the effected_location_entries
                # query results. If they are not, add them to the database.
                move_result_path = os.path.join(*(new_path_list + [old_path_list[-1]]))
                for root, _, files in os.walk(move_result_path):
                    for relocated_file in files:
                        located_in_db = False
                        root_server_dirs = os.path.join(*utils.split_path(root)[file_server_root_index:])
                        for location_entry in effected_location_entries:
                            if location_entry.filename == relocated_file and location_entry.file_server_directories == root_server_dirs:
                                located_in_db = True
                                break
                        
                        if not located_in_db:
                            task_kwargs = {'filepath': os.path.join(root, relocated_file)}
                            utils.enqueue_new_task(db= db,
                                                       enqueued_function=archiver_tasks.add_file_to_db_task,
                                                       function_kwargs=task_kwargs)


            utils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=move_log)
            return move_log

