# archives_application/archiver/archival_file.py

import os
import logging
import shutil
import typing
from .. import utils
from dateutil import parser
from datetime import datetime
from collections import defaultdict



class ArchivalFile:

    def __init__(self, current_path: str, project: str = None, destination_path: str = None, new_filename: str = None,
                 notes: str = None, destination_dir: str = None, document_date: str = None,
                 archives_location: str = None, directory_choices: typing.List[str] = []):
        """
        :param archives_location: string path to archives directory
        :param current_path: path to  file
        :param project: project number string
        :param destination_path: the desired path for the file when tit is archived
        :param new_filename: optional file name for the destination file
        :param notes: for recording notes in the database
        :param destination_dir: chosen directory from the directory templates
        :param directory_choices: list of destination directories to choose from
        """
        self.current_path = current_path
        self.size = 0
        if self.current_path and os.path.exists(self.current_path):
            self.size = str(os.path.getsize(current_path))
        
        self.archives_location = archives_location
        self.directory_choices = directory_choices
        self.project_number = project
        self.destination_dir = destination_dir
        self.new_filename = new_filename
        self.notes = notes
        self.cached_destination_path = destination_path
        self.datetime_archived = None
        self.file_code = None
        if destination_dir:
            self.file_code = utils.FileServerUtils.file_code_from_destination_dir(destination_dir)
        self.document_date = None
        if document_date:
            self.document_date = parser.parse(document_date)


    def assemble_destination_filename(self):
        """
        returns the resulting anticipated filename from an anticipated archival process. Handles extensions by copying
        them from current filename to desired new filename
        :return:
        """
        current_filename = utils.FileServerUtils.split_path(self.current_path)[-1]
        dest_filename = current_filename
        if self.new_filename:
            dest_filename = self.new_filename

        current_filename_list = current_filename.split(".")
        extension = current_filename_list[-1]
        split_dest_components = dest_filename.split(".")

        # if the filename already starts with the project number and filing code prefix, remove them.
        if len(split_dest_components) > 2 and dest_filename.lower().startswith(self.project_number.lower() + "."):
            no_prefix_name = dest_filename[len(self.project_number) + 1:]
            if no_prefix_name.lower().startswith(self.file_code.lower() + "."):
                no_prefix_name = no_prefix_name[len(self.file_code) + 1:]
                split_dest_components = no_prefix_name.split(".")

        # if the destination filename didn't include the file extension add it to the filename component list
        if not split_dest_components[-1] == extension:
            split_dest_components.append(extension)

        # previously we had added preppended the project number and file code to the filename here.
        # Removed 3-14-2025
        destination_filename = ".".join(split_dest_components)
        return destination_filename
    
    @staticmethod
    def _get_intermediate_code(code_prefix: str):
        """Extract intermediate code from sub-codes like
        'F7.1' -> 'F7 - '
        'F7 - ' -> 'F7 - '
        'F7 - Drawings and Specifications' -> 'F7 - '
        'F - ' -> None
        """
        code_prefix = code_prefix.rstrip(" - ").strip()
        
        if "." in code_prefix:
            return code_prefix.split(".")[0] + " - "
        
        # if there is not a digit in code_prefix, return None
        if not any(char.isdigit() for char in code_prefix):
            return None
        
        # return the code prefix with a trailing space dash space
        return code_prefix + " - "

    def destination_hierarchy_parent_dir(self):
        """
        eg  r"E - Program and Design\E5 - Correspondence"

        :return:
        """
        # TODO handle situation when there is a cached_destination_path but no destination_dir_name
        if not self.destination_dir:
            return ''

        if self.destination_dir[1].isdigit():
            # a directory from self.directory_choices is parent directory if it shares same first char and doesn't have a
            # digit in second char position eg "E - " is a parent directory of "E5 - "
            is_parent_dir = lambda child_dir, dir: dir[0] == child_dir[0] and not dir[1].isdigit()
            parent_dirs = [dir for dir in self.directory_choices if is_parent_dir(self.destination_dir, dir)]
            matching_parent_dir = parent_dirs[0] if  parent_dirs else ''
            return str(matching_parent_dir)
        
        return ''
    
    def destination_hierarchy_intermediate_dir(self):
        """
        Returns the intermediate directory for the destination hierarchy.
        This is used to determine the parent directory for the destination file.
        
        :return: str - The intermediate directory name
        """
        if not self.destination_dir or not self.destination_hierarchy_parent_dir():
            return ''
        
        destination_dir_prefix = self.destination_dir.split(" ")[0] + " - "
        # if the destination_dir is a parent directory or intermediate directory, return empty string
        if not destination_dir_prefix[1].isdigit() or not '.' in destination_dir_prefix:
            return ''

        prefix = self._get_intermediate_code(destination_dir_prefix)
        if not prefix:
            return ''
        
        # get the entry from self.directory_choices that matches the prefix
        matching_dirs = [dir for dir in self.directory_choices if dir.startswith(prefix)]
        if not matching_dirs:
            raise Exception(
                f"No matching intermediate directory found for prefix '{prefix}' in directory choices: {self.directory_choices}"
            )
        
        # TODO assuming there is only one matching directory
        return matching_dirs[0]

    def get_destination_path(self):
        """
        Constructs the destination path for archiving a file based on project number and filing codes.
        
        This method implements a complex algorithm to determine the appropriate storage location
        within the archives' hierarchical directory structure. It follows these steps:
        
        1. If a cached destination path exists (self.cached_destination_path), returns it immediately.
        2. Otherwise, constructs a path through the following process:
        a. Determines the "xx" level directory prefix from the project number (e.g., "106xx" for project "10638")
        b. Locates the matching root directory in the archives location
        c. Searches for directories that match either the exact project number or its prefix
        d. Navigates through the hierarchy to find or determine the appropriate filing location
        e. Builds the final path including the destination filename
        
        The method now supports a three-level nested directory structure:
        - Parent directory (e.g., "F - Bid Documents and Contract Award")
        - Intermediate directory (e.g., "F7 - Bid Summary Forms")
        - Destination directory (e.g., "F7.1 - Bid Protest")
        
        The algorithm intelligently navigates existing directory structures, looking for:
        1. First, if the destination directory already exists directly in the project directory
        2. If not, it checks for the intermediate directory and navigates into it
        3. If not, it checks for the parent directory and navigates the hierarchy
        4. For any level that doesn't exist, it creates the appropriate directory structure
        
        The method handles several edge cases:
        - Multiple directories matching the same project number (raises an exception)
        - No existing directories for the project (constructs a proposed path with new directories)
        - Nested filing structures with parent-child directory relationships
        - Various directory naming conventions and formats
        
        Once constructed, the path is cached in self.cached_destination_path for future calls.
        
        Returns:
            str: The complete file path where the archive file should be stored
            
        Raises:
            Exception: If multiple directories match the same project number or prefix,
                    indicating potential duplicates in the archives
        
        Notes:
            - The method uses destination_hierarchy_parent_dir(), destination_hierarchy_intermediate_dir() 
            and assemble_destination_filename() to determine the destination directory structure and filename
            - The algorithm prioritizes existing directory structures when available
            - This method determines the path but does not create any directories or files
            (actual directory creation happens in archive_in_destination())
            - For subcodes (like F7.1), files are placed in a properly nested structure
            (F - Bid Documents/F7 - Bid Summary Forms/F7.1 - Bid Protest)
        """

        def list_of_child_dirs(parent_directory_path: str):
            """Return the names of direct child directories under parent_directory_path."""
            try:
                return [entry.name for entry in os.scandir(parent_directory_path) if entry.is_dir()]
            except FileNotFoundError:
                return []
        

        def path_from_project_num_dir_to_destination(path_to_project_num_dir: str, destination_filename: str):
            """
            Sub-routine for constructing the remainder of the destination path after building the path up to the
            directory corresponding to the archive file project number.

            :param path_to_project_num_dir: path thus constructed to the directory corresponding to the archive file
            project number
            :param destination_dir_structure: the nested large template destination
            :param destination_filename: given by ArchivalFile.assemble_destination_filename()
            :return: string final destination path
            """
            def existing_parent_dir(some_path):
                parent_dir_prefix = self.destination_hierarchy_parent_dir().split(" ")[0] + " - "
                path_child_dirs = list_of_child_dirs(some_path)
                existing_parent_dirs = [dir_name for dir_name in path_child_dirs if dir_name.upper().startswith(parent_dir_prefix.upper())]
                if len(existing_parent_dirs) > 0:
                    return existing_parent_dirs[0]
                return None
            
            def existing_intermediate_dir(some_path):
                intermediate_destination_dir = self.destination_hierarchy_intermediate_dir()
                if intermediate_destination_dir:
                    intermediate_destination_dir_prefix = intermediate_destination_dir.split(" ")[0] + " - "
                    path_child_dirs = list_of_child_dirs(some_path)
                    existing_intermediate_dirs = [dir_name for dir_name in path_child_dirs if dir_name.upper().startswith(intermediate_destination_dir_prefix.upper())]
                    if len(existing_intermediate_dirs) > 0:
                        return existing_intermediate_dirs[0]
                return None
            
            def existing_destination_dir(some_path):
                destination_dir_prefix = self.destination_dir.split(" ")[0] + " - "
                path_child_dirs = list_of_child_dirs(some_path)
                existing_destination_dirs = [dir_name for dir_name in path_child_dirs if dir_name.upper().startswith(destination_dir_prefix.upper())]
                if len(existing_destination_dirs) > 0:
                    return existing_destination_dirs[0]
                return None
        

            new_path = path_to_project_num_dir

            # if new_path doesn't exist we will just add directory structure and filename to new_path and return it
            if not os.path.exists(new_path):
                new_path = os.path.join(new_path,
                                        self.destination_hierarchy_parent_dir(),
                                        self.destination_hierarchy_intermediate_dir(),
                                        self.destination_dir)
                new_path = os.path.join(new_path, destination_filename)
                return new_path

            # first ceck if the destination directory is already in the directory
            existing_dest_dir = existing_destination_dir(new_path)
            if existing_dest_dir:
                new_path = os.path.join(new_path, existing_dest_dir)
                new_path = os.path.join(new_path, destination_filename)
                return new_path
            
            # if the destination directory is not in the path, we will look for an intermediate directory
            intermediate_dest_dir = existing_intermediate_dir(new_path)
            if intermediate_dest_dir:
                new_path = os.path.join(new_path, intermediate_dest_dir)
                
                existing_dest_dir = existing_destination_dir(new_path)
                # if existing destination directory equivalent exists, we will use it,
                # otherwise we will create a new destination directory
                if existing_dest_dir:
                    new_path = os.path.join(new_path, existing_dest_dir)
                    new_path = os.path.join(new_path, destination_filename)
                    return new_path

                else:
                    new_path = os.path.join(new_path, self.destination_dir)
                    new_path = os.path.join(new_path, destination_filename)
                    return new_path
                
            # if the destination directory and intermediate directory are not in the path, we will look for a parent directory
            existing_destination_parent_dir = existing_parent_dir(new_path)
            if existing_destination_parent_dir:
                new_path = os.path.join(new_path, existing_destination_parent_dir)
                
                # look for destination directory in the parent directory
                existing_dest_dir = existing_destination_dir(new_path)
                if existing_dest_dir:
                    new_path = os.path.join(new_path, existing_dest_dir)
                    new_path = os.path.join(new_path, destination_filename)
                    return new_path
                
                # look for intermediate directory in the parent directory
                intermediate_dest_dir = existing_intermediate_dir(new_path)
                if intermediate_dest_dir:
                    new_path = os.path.join(new_path, intermediate_dest_dir)
                    
                    existing_dest_dir = existing_destination_dir(new_path)
                    if existing_dest_dir:
                        new_path = os.path.join(new_path, existing_dest_dir)
                        new_path = os.path.join(new_path, destination_filename)
                        return new_path
                    
                    # if no destination directory exists, we will create it
                    new_path = os.path.join(new_path, self.destination_dir)
                    new_path = os.path.join(new_path, destination_filename)
                    return new_path
                
                # if no intermediate directory exists, we will create it
                new_path = os.path.join(new_path, self.destination_hierarchy_intermediate_dir(), 
                                        self.destination_dir)
                new_path = os.path.join(new_path, destination_filename)
                return new_path
            
            # if no parent directory exists, we will create it
            new_path = os.path.join(new_path, self.destination_hierarchy_parent_dir(),
                                    self.destination_hierarchy_intermediate_dir(),
                                    self.destination_dir)
            new_path = os.path.join(new_path, destination_filename)
            return new_path
            

        ############### Start of get_destination_path() #################
        if not self.cached_destination_path:

            # sept
            xx_level_dir_prefix, project_num_prefix = utils.FileServerUtils.prefixes_from_project_number(self.project_number)
            root_directories_list = list_of_child_dirs(self.archives_location)
            matching_root_dirs = [dir_name for dir_name in root_directories_list if
                                  dir_name.lower().startswith(xx_level_dir_prefix.lower())]

            # if we have more than one matching root dir we throw an error
            if len(matching_root_dirs) != 1:
                raise Exception(
                    f"{len(matching_root_dirs)} matching directories in {self.archives_location} for project number {self.project_number}")

            # add the directory matching the xx level prefix for this project number
            new_path = os.path.join(self.archives_location, matching_root_dirs[0])
            # list of contents of xx level directory which are not files (ie directories in xx level directory)
            xx_dir_dirs = list_of_child_dirs(new_path)

            # lambda functions that check whether a directory name starts with either project number or
            # prefix respectively.
            proj_num_in_dir_name = lambda dir_name: self.project_number == dir_name.split(" ")[0]
            prefix_in_dir_name = lambda dir_name: project_num_prefix == dir_name.split(" ")[0]
            dirs_matching_proj_num = [dir_name for dir_name in xx_dir_dirs if proj_num_in_dir_name(dir_name)]

            # if more than one directory starts with the same project number...
            if len(dirs_matching_proj_num) > 1:
                raise Exception(
                    f"{len(dirs_matching_proj_num)} matching directories in {new_path} for project number {self.project_number}; expected 0 or 1.\nThis is likely due to a duplicate project number in the archives.")

            # if no directories match the project number...
            if len(dirs_matching_proj_num) == 0:
                dirs_matching_prefix = [dir_name for dir_name in xx_dir_dirs if prefix_in_dir_name(dir_name)]
                if len(dirs_matching_prefix) > 1:
                    raise Exception(
                        f"{len(dirs_matching_prefix)} matching directories in {new_path} for prefix for project number {self.project_number}; expected 0 or 1.\nThis is likely due to a duplicate project number in the archives.")

                # if there is now project number or prefix directory at the 'xx' level, it will need to be made
                if len(dirs_matching_prefix) == 0:
                    new_path = os.path.join(new_path, project_num_prefix)
                    new_path = os.path.join(new_path, self.project_number)
                    new_path = os.path.join(new_path,
                                            self.destination_hierarchy_parent_dir(),
                                            self.destination_hierarchy_intermediate_dir(),
                                            self.destination_dir)
                    new_path = os.path.join(new_path, self.assemble_destination_filename())
                    self.cached_destination_path = new_path
                    return new_path

                if len(dirs_matching_prefix) == 1:
                    # if a dir exists that does begin with the prefix, we'll add it to our path and look again for
                    # directories that begin with the project number

                    new_path = os.path.join(new_path, dirs_matching_prefix[0])
                    prefix_dir_dirs = list_of_child_dirs(new_path)
                    dirs_matching_proj_num = [dir_name for dir_name in prefix_dir_dirs if
                                              proj_num_in_dir_name(dir_name)]
                    if len(dirs_matching_proj_num) > 1:
                        logging.exception(
                            f"{len(dirs_matching_proj_num)} matching directories in {new_path} for project number {self.project_number}; expected 0 or 1.\nThis is likely due to a duplicate project number in the archives.",
                            exc_info=True)
                        return ''

                # if no dirs are equivalent to the project number
                if len(dirs_matching_proj_num) == 0:
                    new_path = os.path.join(new_path, self.project_number)
                    new_path = path_from_project_num_dir_to_destination(path_to_project_num_dir=new_path,
                                                                        destination_filename=self.assemble_destination_filename())
                    self.cached_destination_path = new_path
                    return self.cached_destination_path
                
                # if we do find a dir that corresponds with the project number...
                if len(dirs_matching_proj_num) == 1:
                    new_path = os.path.join(new_path, dirs_matching_proj_num[0])
                    new_path = path_from_project_num_dir_to_destination(path_to_project_num_dir=new_path,
                                                                        destination_filename=self.assemble_destination_filename())
                    self.cached_destination_path = new_path
                    return self.cached_destination_path

            # if we do find a dir that corresponds with the project number...
            if len(dirs_matching_proj_num) == 1:
                new_path = os.path.join(new_path, dirs_matching_proj_num[0])
                #look for another project number directory in the dirs of this project number directory
                proj_num_dir_dirs = list_of_child_dirs(new_path)
                dirs_matching_proj_num = [dir_name for dir_name in proj_num_dir_dirs if proj_num_in_dir_name(dir_name)]

                # if more than one directory starts with the same project number...
                if len(dirs_matching_proj_num) not in (0,1):
                    raise Exception(
                        f"{len(dirs_matching_proj_num)} matching directories in {new_path} for project number {self.project_number}; expected 0 or 1.")

                if len(dirs_matching_proj_num) == 0:
                    new_path = os.path.join(new_path, self.project_number)

                if len(dirs_matching_proj_num) == 1:
                    new_path = os.path.join(new_path, dirs_matching_proj_num[0])

                new_path = path_from_project_num_dir_to_destination(path_to_project_num_dir=new_path,
                                                                    destination_filename=self.assemble_destination_filename())
                self.cached_destination_path = new_path
                return self.cached_destination_path
            
            self.cached_destination_path = new_path
        return self.cached_destination_path


    def attribute_defaultdict(self):
        """
        This method is used to create a dictionary of attributes for the archival file object.
        """
        date_stamp = ''
        doc_date = ''
        if self.datetime_archived:
            date_stamp = self.datetime_archived.strftime("%m/%d/%Y, %H:%M:%S")
        if self.document_date:
            doc_date = self.document_date.strftime("%m/%d/%Y, %H:%M:%S")
        if (self.get_destination_path() or self.current_path) and not self.size:
            if not os.path.isfile(self.get_destination_path()):
                self.size = str(os.path.getsize(self.current_path))
            else:
                self.size = str(os.path.getsize(self.get_destination_path()))

        #if we don't have a file code, generate one from the destination
        if self.destination_dir and not self.file_code:
            self.file_code = utils.FileServerUtils.file_code_from_destination_dir(self.destination_dir)

        if not self.project_number:
            self.project_number = utils.project_number_from_path(self.get_destination_path())

        attribute_dict = {"date_archived": date_stamp,
                          "project_number": self.project_number,
                          "destination_path": self.get_destination_path(),
                          "document_date": doc_date,
                          "destination_directory": self.destination_dir,
                          "file_code": self.file_code,
                          "file_size": self.size,
                          "notes": self.notes}
        return defaultdict(lambda: None, attribute_dict)


    def check_permissions(self):
        """
        Returns a string describing issues with permissions that may arise when trying to archive the file.
        :return:
        """
        if not os.path.exists(self.current_path):
            return f"The file no longer exists {self.current_path}"

        issues_found = ''
        try:
            os.rename(self.current_path, self.current_path)
        except OSError as e:
            issues_found = "Access error on file using renaming test:" + '! \n' + str(e) + "\n"

        if not os.access(self.current_path, os.R_OK):
            issues_found += "No read access for the file.\n"
        if not os.access(self.current_path, os.W_OK):
            issues_found += "No write access for the file.\n"
        if not os.access(self.current_path, os.X_OK):
            issues_found += "No execution access for the file.\n"
        return issues_found


    def archive_in_destination(self):

        destination_path_list = utils.FileServerUtils.split_path(self.get_destination_path())
        destination_dir_path = os.path.join(*destination_path_list[:-1])

        if not os.path.exists(destination_dir_path):
            os.makedirs(destination_dir_path, exist_ok=True)
        self.datetime_archived = datetime.now()
        try:
            shutil.copyfile(src=self.current_path, dst=self.get_destination_path())
        except Exception as e:
            return False, e
        try:
            os.remove(self.current_path)
            return True, ''
        except Exception as e:
            return False, e
