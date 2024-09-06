import datetime
import errno
import flask
import flask_sqlalchemy
import os
import random
import shutil
from sqlalchemy import func
from typing import List, Callable
from archives_application import create_app, utils
from archives_application.archiver import archiver_tasks
from archives_application.models import  FileLocationModel, FileModel, ArchivedFileModel

# Create the app context so that tasks can access app extensions even though
# they are not running in the main thread.
app = create_app()


def directory_contents_quantities(dir_path: str, server_location: str, db: flask_sqlalchemy.SQLAlchemy):
    """
    Sends a query to the database to get the number of files and the amount of data that will be effected in
    a given directory.
    :param dir_path: path to the directory
    :param server_location: root directory of the file server
    :param db: SQLAlchemy database object (flask.current_app.extensions['sqlalchemy'])
    """
    dir_query_str = dir_path.replace(server_location, '')[1:]
    file_location_entries = db.session.query(FileLocationModel) \
        .filter(FileLocationModel.file_server_directories.like(func.concat(dir_query_str, '%'))) \
        .join(FileModel, FileLocationModel.file_id == FileModel.id) \
        .with_entities(func.count(FileLocationModel.id).label('count'), func.sum(FileModel.size).label('total_size')) \
        .one()

    files_effected = file_location_entries.count
    data_effected = int(file_location_entries.total_size) if file_location_entries.total_size else 0
    return files_effected, data_effected


class ServerEdit:
    """
    This class is used to create a server edit object, which represents a change to the file server.
    The change can be of the following types: DELETE, RENAME, MOVE, CREATE.
    """
    
    def __init__(self, server_location, change_type, exclusion_functions: List[Callable[[str], bool]] = [], new_path: str=None, old_path: str=None):
        """
        Initializes a ServerEdit object with the specified server location, change type, exclusion functions, new path, and old path.
        :param server_location: The root directory of the file server.
        :param change_type: The type of change to be executed (DELETE, RENAME, MOVE, CREATE).
        :param exclusion_functions: A list of functions that take a file path as input and return True if the file should be excluded from the change, False otherwise.
        """
        self.server_location = server_location
        self.change_type = change_type
        self.exclusion_functions = exclusion_functions
        self.new_path = None
        if new_path:
            self.new_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=new_path,
                                                                      location_path_prefix=server_location)

        self.old_path = None
        if old_path:
            self.old_path = utils.FlaskAppUtils.user_path_to_app_path(path_from_user=old_path,
                                                                      location_path_prefix=server_location)
            if not os.path.exists(self.old_path):
                e_message = f"Path to asset does not exists: {self.new_path}\nEntered path: {old_path}"
                raise Exception(e_message)

            if self.old_path == server_location:
                raise Exception("Server root directory chosen")
        self.change_executed = False
        self.data_effected = 0
        
        # if the serveredit is a move, change, or edit, determine if the change is to a file or directory
        self.is_file = False
        if self.old_path:
            self.is_file = os.path.isfile(self.old_path)
        self.files_effected = 1 if self.is_file else 0

    def to_dict(self):
        """
        Returns a dictionary representation of the ServerEdit object.
        """
        return {'server_location': self.server_location,
                'change_type': self.change_type,
                'new_path': self.new_path,
                'old_path': self.old_path,
                'change_executed': self.change_executed,
                'is_file': self.is_file,
                'data_effected': self.data_effected,
                'files_effected': self.files_effected}

    def execute(self, files_limit = 500, effected_data_limit=500000000, timeout=15):
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
        :param timeout: Maximum time in seconds that the function can run before it is terminated (default is 600).
        :return: Dictionary containing the results of the enqueuing task.
        :rtype: dict
        """

        def check_against_limits():
            """
            Checks if the number of files and the amount of data affected by the change is within the limits set by the files_limit and effected_data_limit parameters.
            If either of these limits is breached, an exception is raised.
            """
            if effected_data_limit and self.data_effected and self.data_effected > effected_data_limit:
                raise Exception(
                    f"ServerEdit data limit breached. Too much data effected by change type '{self.change_type}'.\nOld path: {self.old_path}\nNew path: {self.new_path}")

            if files_limit and self.files_effected and self.files_effected > files_limit:
                raise Exception(
                    f"ServerEdit file limit breached. Too many files effected by change type '{self.change_type}.'\nOld path: {self.old_path}\nNew path: {self.new_path}")


        def on_rmtree_error(func, path, exc_info):
            """
            This function is called by the shutil.rmtree function if it encounters an error while removing a directory.
            Attempts to change the permissions of the file or directory and try again. If the error persists, an exception is raised, 
            but more informative error message is given.
            :param func: The function that raised the exception.
            :param path: The path to the file or directory that caused the exception.
            :param exc_info: The exception information.
            """
            # If the exception is a permission error, change the permissions of the file or directory and try again.
            if not os.access(path, os.W_OK):
                os.chmod(path, 0o700)
                func(path)
            
            else:
                raise Exception(f"Error removing path: {path} - {exc_info[1]}")

        def enqueue_change_task(task_func):
            """
            Enqueues a new task to execute the specified task function.
            :param task_func: The task function to be executed.
            :return: Dictionary containing the results of the enqueuing task.
            :rtype: dict
            """
            task_info = utils.serializable_dict(self.to_dict())
            return utils.RQTaskUtils.enqueue_new_task(db=flask.current_app.extensions['sqlalchemy'],
                                                      enqueued_function=task_func,
                                                      timeout=timeout,
                                                      task_info=task_info)
        
        def add_int_to_filename(filename: str, int_to_add: int):
            """
            Adds an integer to the filename before the file extension.
            :param filename: str: The filename to which the integer is to be added.
            :param int_to_add: int: The integer to add to the filename.
            """
            unique_suffix = f"_({int_to_add})"
            filename_parts = filename.split('.')
            if len(filename_parts) == 1:
                return filename + unique_suffix
            
            return '.'.join(filename_parts[:-1]) + unique_suffix + '.' + filename
        
        # If the change type is 'DELETE'
        if self.change_type.upper() == 'DELETE':
            enqueueing_results = {}
            
            # if the deleted asset is a file, we need to get the size of the file before removing
            if self.is_file:
                self.data_effected = os.path.getsize(self.old_path)
                os.remove(self.old_path)
                self.change_executed = True
                self.files_effected = 1
                enqueueing_results = enqueue_change_task(self.add_deletion_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
                return enqueueing_results

            # if the deleted asset is a dir we need to add up all the files and their sizes before removing
            self._get_quantity_effected(dir_path=self.old_path,
                                        db=flask.current_app.extensions['sqlalchemy'])

            # make sure change is not in excess of limits set
            check_against_limits()

            # remove directory and contents
            shutil.rmtree(self.old_path, onerror=on_rmtree_error)
            self.change_executed = True
            enqueueing_results = enqueue_change_task(self.add_deletion_to_db_task)
            enqueueing_results['change_executed'] = self.change_executed
            return enqueueing_results

        # If the change type is 'RENAME'
        if self.change_type.upper() == 'RENAME':
            enqueueing_results = {}
            old_path = self.old_path
            old_path_list = utils.FileServerUtils.split_path(old_path)
            new_path_list = utils.FileServerUtils.split_path(self.new_path)
            if not len(old_path_list) == len(new_path_list):
                raise Exception(
                    f"Attempt at renaming paths failed. Parent directories are not the same: \n {self.new_path}\n{self.old_path}")

            # if this a change of a filepath, we need to cleanse the filename
            if self.is_file:
                self.files_effected = 1
                self.data_effected = os.path.getsize(old_path) #bytes
                new_path_list[-1] = utils.FilesUtils.cleanse_filename(new_path_list[-1])

            else:
                self._get_quantity_effected(dir_path=self.old_path,
                                            db=flask.current_app.extensions['sqlalchemy'])

            # make sure change is not in excess of limits set
            check_against_limits()
            if old_path == self.new_path:            
                self.change_executed = True
                self.files_effected = 0
                self.data_effected = 0
                return {'change_executed': self.change_executed}
                
            try:
                os.rename(self.old_path, self.new_path)
                self.change_executed = True
                enqueueing_results = enqueue_change_task(self.add_renaming_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
            except Exception as e:
                raise Exception(f"There was an issue trying to change the name. If it is permissions issue, consider that it might be someone using a directory that would be changed \n{e}")

            return enqueueing_results

        # if the change_type is 'MOVE'
        if self.change_type.upper() == 'MOVE':
            try:
                if self.is_file:
                    filename = utils.FileServerUtils.split_path(self.old_path)[-1]
                    destination_path = os.path.join(self.new_path, filename)
                    
                    # if the file already exists in the destination directory, add a unique suffix to the filename
                    unique_filename_suffix_int = 0
                    while os.path.exists(destination_path):
                        unique_filename_suffix_int += 1
                        new_filename = add_int_to_filename(filename, unique_filename_suffix_int)
                        destination_path = os.path.join(self.new_path, new_filename)
                    
                    self.new_path = destination_path
                    self.data_effected = os.path.getsize(self.old_path)
                    check_against_limits()
                    shutil.copyfile(src=self.old_path, dst=destination_path)
                    os.remove(self.old_path)
                    self.change_executed = True
                
                else:
                    # make sure change is not in excess of limits set
                    self._get_quantity_effected(dir_path=self.old_path,
                                                db=flask.current_app.extensions['sqlalchemy'])
                    check_against_limits()

                    # cannot move a directory within itself
                    if self.new_path.startswith(self.old_path):
                        raise Exception(
                            f"Cannot move a directory within itself.\nOld path: {self.old_path}\nNew path: {self.new_path}")

                    # if the directory already exists in the destination directory, add a unique suffix to the directory name
                    base_dir_name = os.path.basename(self.old_path)
                    unique_new_path = os.path.join(self.new_path, base_dir_name)
                    unique_suffix_int = 0
                    while os.path.exists(unique_new_path):
                        unique_suffix_int += 1
                        unique_new_path = os.path.join(self.new_path, base_dir_name + f"_({unique_suffix_int})")
                        self.new_path = unique_new_path

                    # move directory and contents
                    shutil.move(self.old_path, unique_new_path, copy_function=shutil.copytree)
                    self.change_executed = True
              
                enqueueing_results = enqueue_change_task(self.add_move_to_db_task)
                enqueueing_results['change_executed'] = self.change_executed
                return enqueueing_results
            
            except Exception as e:
                if type(e) == shutil.Error:
                    e_str = f"Exception trying to move the directory. Is there a collision with an existing file/directory? If it is permissions issue, consider that it might be someone using a directory that would be changed \n{e}"
                    raise Exception(e_str)
                raise Exception(f"Exception trying to move the directory:\n{e}")

        # if the change_type is 'CREATE'
        if self.change_type.upper() == 'CREATE':
            if not os.path.exists(self.new_path):
                os.makedirs(self.new_path)
            self.change_executed = True
            self.files_effected = 0
            self.data_effected = 0
            return {'change_executed': self.change_executed}

        results = {'change_executed': self.change_executed}
        return results

    def _get_quantity_effected(self, dir_path, db):
        """
        Sends a query to the database to get the number of files and the amount of data that will be effected in
        a given directory.
        :param dir_path: path to the directory
        :param db: SQLAlchemy database object (flask.current_app.extensions['sqlalchemy'])
        """
        self.files_effected, self.data_effected = directory_contents_quantities(dir_path=dir_path,
                                                                               server_location=self.server_location,
                                                                               db=db)
        return self.files_effected, self.data_effected

    @staticmethod
    def remove_file_from_db(db, root_index, file_path):
        """
        Removes a file location from database and if it is the last entry for that file_id, removes the file db entry too.
        :param db: SQLAlchemy database object
        :param root_index: index of the server share directory in the file path
        :param file_path: path to the file
        """

        filename = utils.FileServerUtils.split_path(file_path)[-1]
        db_path = os.path.join(*utils.FileServerUtils.split_path(file_path)[root_index:-1])   
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
        """
        This function is used to reconcile the database with a file deletion operation.
        It is a task function that is enqueued for a seperate thread to execute.
        """
        
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            deletion_log = {}
            deletion_log['task_id'] = queue_id
            deletion_log['location_entries_effected'] = 0
            deletion_log['files_entries_effected'] = 0
            deletion_log['old_path'] = self.old_path

            if self.is_file:
                server_dirs = utils.FileServerUtils.split_path(self.old_path)[file_server_root_index:-1]
                filename = utils.FileServerUtils.split_path(self.old_path)[-1]
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
                server_dirs = utils.FileServerUtils.split_path(self.old_path)[file_server_root_index:]
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
            
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=deletion_log)
            return deletion_log
    

    def add_renaming_to_db_task(self, queue_id):
        """
        This function is used to reconcile the database with a file rename operation.
        It is a task function that is enqueued for a seperate thread to execute.
        """

        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            rename_log = {}
            rename_log['task_id'] = queue_id
            rename_log['location_entries_effected'] = 0
            rename_log['files_entries_effected'] = 0
            rename_log['old_path'] = self.old_path
            rename_log['new_path'] = self.new_path

            if self.is_file:
                rename_log['is_file'] = True
                server_dirs = utils.FileServerUtils.split_path(self.old_path)[file_server_root_index:-1]
                old_filename = utils.FileServerUtils.split_path(self.old_path)[-1]
                new_filename = utils.FileServerUtils.split_path(self.new_path)[-1]
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
                old_server_dirs = utils.FileServerUtils.split_path(self.old_path)[file_server_root_index:]
                old_server_path = os.path.join(*old_server_dirs)
                new_server_dirs = utils.FileServerUtils.split_path(self.new_path)[file_server_root_index:]
                new_server_path = os.path.join(*new_server_dirs)
                file_location_entries = db.session.query(FileLocationModel)\
                    .filter(FileLocationModel.file_server_directories.like(func.concat(old_server_path, '%'))).all()
                

                for file_location_entry in file_location_entries:
                    old_file_path = file_location_entry.file_server_directories
                    old_path_list = utils.FileServerUtils.split_path(old_file_path)
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
            
            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=rename_log)
            return rename_log
                
    
    def add_move_to_db_task(self, queue_id):
        """
        Reconciles the database with a file move operation. Basically, it changes the file_server_directories
        for each file_location entry that is effected by the move. If the move is a file move, it also changes.
        This is a task function that is enqueued for a seperate thread to execute.
        """
        with app.app_context():
            db = flask.current_app.extensions['sqlalchemy']
            utils.RQTaskUtils.initiate_task_subroutine(q_id=queue_id, sql_db=db)
            file_server_root_index = len(utils.FileServerUtils.split_path(flask.current_app.config.get('ARCHIVES_LOCATION')))
            move_log = {}
            move_log['task_id'] = queue_id
            move_log['location_entries_effected'] = 0
            move_log['files_entries_effected'] = 0
            move_log['old_path'] = self.old_path
            move_log['new_path'] = self.new_path

            old_path_list = utils.FileServerUtils.split_path(self.old_path)
            new_path_list = utils.FileServerUtils.split_path(self.new_path)
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
                    new_path = os.path.join(self.new_path, filename)
                    
                    # if the file is excluded by the exclusion functions, do not add it to the database
                    if any([exclusion_func(new_path) for exclusion_func in self.exclusion_functions]):
                        return move_log
                    
                    file_hash = utils.FilesUtils.get_hash(new_path)
                    file_entry = None
                    while not file_entry:
                        
                        file_entry = db.session.query(FileModel)\
                            .filter(FileModel.hash == file_hash).first()
                        if not file_entry:
                            file_entry = FileModel(hash=file_hash,
                                                   size=os.path.getsize(new_path),
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
                    entry_dir_list = utils.FileServerUtils.split_path(location_entry.file_server_directories)
                    new_location_list = new_path_list[file_server_root_index:] + entry_dir_list[len(old_server_dirs_list)-1:]
                    new_location_server_dirs = os.path.join(*new_location_list)

                    # remove any entries that are already in the new location
                    existing_loc_path = os.path.join(flask.current_app.config.get('ARCHIVES_LOCATION'), new_location_server_dirs, location_entry.filename)
                    remove_file_entry, remove_location_entry = self.remove_file_from_db(db, file_server_root_index, file_path=existing_loc_path)
                    if remove_file_entry:
                        move_log['files_entries_effected'] += 1
                    if remove_location_entry:
                        move_log['location_entries_effected'] += 1

                    # check if the file exists in the new location. If it does, update the location entry.
                    file_exists = os.path.exists(existing_loc_path)
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
                        root_server_dirs = os.path.join(*utils.FileServerUtils.split_path(root)[file_server_root_index:])
                        for location_entry in effected_location_entries:
                            if location_entry.filename == relocated_file and location_entry.file_server_directories == root_server_dirs:
                                located_in_db = True
                                break
                        
                        if not located_in_db:
                            # if the file is excluded by the exclusion functions, do not add it to the database
                            if any([exclusion_func(relocated_file) for exclusion_func in self.exclusion_functions]):
                                continue
                            
                            task_kwargs = {'filepath': os.path.join(root, relocated_file)}
                            nk_results = utils.RQTaskUtils.enqueue_new_task(db= db,
                                                                            enqueued_function=archiver_tasks.add_file_to_db_task,
                                                                            task_kwargs=task_kwargs)


            utils.RQTaskUtils.complete_task_subroutine(q_id=queue_id, sql_db=db, task_result=move_log)
            return move_log


