import os
import logging
import shutil
import typing
from .. import utilities
from dateutil import parser
from datetime import datetime
from collections import defaultdict


# ArchivalFile class from Archives_archiver program should be interchangeable with this one if the above imports
# are preserved


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
            self.file_code = utilities.file_code_from_destination_dir(destination_dir)
        self.document_date = None
        if document_date:
            self.document_date = parser.parse(document_date)

    def assemble_destination_filename(self):
        """
        returns the resulting anticipated filename from an anticipated archival process. Handles extensions by copying
        them from current filename to desired new filename
        :return:
        """
        current_filename = utilities.split_path(self.current_path)[-1]
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

        prefix_list = [self.project_number, self.file_code]
        split_dest_components = prefix_list + split_dest_components
        destination_filename = ".".join(split_dest_components)
        return destination_filename

    def nested_large_template_destination_dir(self):
        """
        eg  "E - Program and Design\E5 - Correspondence"

        :return:
        """
        # TODO handle situation when there is a cached_destination_path but no destination_dir_name

        nested_dirs = self.destination_dir
        if nested_dirs[1].isdigit():
            # a directory from self.directory_choices is parent directory if it shares same first char and doesn't have a
            # digit in second char position
            is_parent_dir = lambda child_dir, dir: dir[0] == child_dir[0] and not dir[1].isdigit()
            parent_dir = [dir for dir in self.directory_choices if is_parent_dir(nested_dirs, dir)][0]
            nested_dirs = os.path.join(parent_dir, nested_dirs)
        return str(nested_dirs)

    def get_destination_path(self):
        """
        Major function that builds a plausible path string in the following steps:
        Step 1: If it already has a cached destination path, return that
        Step 2: Looks for xx directory in root (self.archives_location) and adds to path
        Step 3: Looks through next two levels in directory hierarchy for directories that start with the project number
            or a project number prefix and add them to the path.
        Step 4: Looks for desired directory location in nested levels and adds it to new path

        ...unless there is already a path in cached_destination_path attribute, in which case that will be returned
        :return: string (or path object?)
        """

        def list_of_child_dirs(parent_directory_path):
            """sub-function for getting a list of just the child directories given a parent directory path"""
            return [dir for dir in os.listdir(parent_directory_path) if
                    not os.path.isfile(os.path.join(parent_directory_path, dir))]

        def path_from_project_num_dir_to_destination(path_to_project_num_dir: str, large_template_destination: str,
                                                     destination_filename: str):
            """
            Sub-routine for constructing the remainder of the destination path after building the path up to the
            directory corresponding to the archive file project number.

            :param path_to_project_num_dir: path thus constructed to the directory corresponding to the archive file
            project number
            :param large_template_destination: given by ArchivalFile.nested_large_template_destination_dir()
            :param destination_filename: given by ArchivalFile.assemble_destination_filename()
            :return: string final destination path
            """

            new_path = path_to_project_num_dir

            # if the path to the dir corresponding to the project number doesn't exist, just return the completed
            # destination filepath
            if not os.path.exists(new_path):
                new_path = os.path.join(new_path, large_template_destination)
                return os.path.join(new_path, destination_filename)

            new_path_dirs = list_of_child_dirs(new_path)
            destination_dir = utilities.split_path(large_template_destination)[-1]
            destination_dir_prefix = destination_dir.split(" ")[0] + " - "  # eg "F5 - ", "G12 - ", "H - ", etc
            destination_dir_parent_dir = utilities.split_path(large_template_destination)[0]

            # if the destination directory is a large template child director...
            if not destination_dir_parent_dir == large_template_destination:

                # need to extrapolate the parent directory prefix given the desired destination directory. eg for
                # destination "F5 - Drawings and Specifications" the parent directory prefix is "F - "
                destination_dir_parent_dir_prefix = destination_dir_parent_dir.split(" ")[0] + " - "  # eg "F - ", "G - ", etc
                parent_dirs = [dir_name for dir_name in new_path_dirs if
                               dir_name.upper().startswith(destination_dir_parent_dir_prefix.upper())]
                if len(parent_dirs) > 0:
                    # TODO cause we're lazy we'll just assume parent_dirs is only len = 1. Maybe should handle other situations?
                    new_path = os.path.join(new_path, parent_dirs[0])
                    new_path_dirs = [dir_name for dir_name in os.listdir(new_path) if
                                     not os.path.isfile(os.path.join(new_path, dir_name))]
                    existing_destination_dirs = [dir_name for dir_name in new_path_dirs if
                                                 dir_name.upper().startswith(destination_dir_prefix)]
                    if existing_destination_dirs:
                        # again, assuming only one dir matches the destination dir prefix:
                        new_path = os.path.join(new_path, existing_destination_dirs[0])

                    else:
                        new_path = os.path.join(new_path, destination_dir)

                # if there is no directory in the destination project folder that corresponds to the parent directory of
                # destination directory in a large template path...
                else:
                    # check for existing equivalents of destination directory
                    new_path_dirs = list_of_child_dirs(new_path)
                    existing_destination_dirs = [dir_name for dir_name in new_path_dirs if
                                                 dir_name.upper().startswith(destination_dir_prefix)]
                    if existing_destination_dirs:
                        new_path = os.path.join(new_path, existing_destination_dirs[0])
                    else:
                        project_num_dirs = [dir for dir in new_path_dirs if dir.lower().startswith(self.project_number)]
                        if not project_num_dirs:
                            new_path = os.path.join(new_path, large_template_destination)
                        else:
                            new_path = os.path.join(new_path, project_num_dirs[0])
                            return path_from_project_num_dir_to_destination(path_to_project_num_dir=new_path,
                                                                            large_template_destination=large_template_destination,
                                                                            destination_filename=destination_filename)

            # if the destination_dir_name doesn't have a project template dir parent...
            else:
                existing_destination_dirs = [dir_name for dir_name in new_path_dirs if
                                             dir_name.upper().startswith(destination_dir_prefix)]
                if existing_destination_dirs:
                    new_path = os.path.join(new_path, existing_destination_dirs[0])
                else:
                    file_num_dirs = [dir for dir in new_path_dirs if
                                     dir.lower().startswith(self.project_number.lower())]
                    if not file_num_dirs:
                        new_path = os.path.join(new_path, large_template_destination)
                    else:
                        return path_from_project_num_dir_to_destination(path_to_project_num_dir=new_path,
                                                                        large_template_destination=large_template_destination,
                                                                        destination_filename=destination_filename)

            return os.path.join(new_path, destination_filename)

        ############### Start of get_destination_path() #################
        if not self.cached_destination_path:

            # sept
            xx_level_dir_prefix, project_num_prefix = utilities.prefixes_from_project_number(self.project_number)
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
                    f"{len(dirs_matching_proj_num)} matching directories in {new_path} for project number {self.project_number}; expected 0 or 1.")

            # if no directories match the project number...
            if len(dirs_matching_proj_num) == 0:
                dirs_matching_prefix = [dir_name for dir_name in xx_dir_dirs if prefix_in_dir_name(dir_name)]
                if len(dirs_matching_prefix) > 1:
                    raise Exception(
                        f"{len(dirs_matching_prefix)} matching directories in {new_path} for prefix for project number {self.project_number}; expected 0 or 1.")

                # if there is now project number or prefix directory at the 'xx' level, it will need to be made
                if len(dirs_matching_prefix) == 0:
                    new_path = os.path.join(new_path, project_num_prefix)
                    new_path = os.path.join(new_path, self.project_number)
                    new_path = os.path.join(new_path, self.nested_large_template_destination_dir())
                    new_path = os.path.join(new_path, self.assemble_destination_filename())
                    self.cached_destination_path = new_path
                    return new_path

                if len(dirs_matching_prefix) == 1:
                    # if a dir exists that does begin with the prefix, we'll add it to our path and look again for
                    # directories that begin with the project number #TODO ..and prefix again too?

                    new_path = os.path.join(new_path, dirs_matching_prefix[0])
                    prefix_dir_dirs = list_of_child_dirs(new_path)
                    dirs_matching_proj_num = [dir_name for dir_name in prefix_dir_dirs if
                                              proj_num_in_dir_name(dir_name)]
                    if len(dirs_matching_proj_num) > 1:
                        logging.exception(
                            f"{len(dirs_matching_proj_num)} matching directories in {new_path} for project number {self.project_number}; expected 0 or 1.",
                            exc_info=True)
                        return ''

                # if no dirs are equivalent to the project number
                if len(dirs_matching_proj_num) == 0:
                    new_path = os.path.join(new_path, self.project_number)
                    new_path = path_from_project_num_dir_to_destination(new_path,
                                                                        self.nested_large_template_destination_dir(),
                                                                        self.assemble_destination_filename())
                    self.cached_destination_path = new_path
                    return self.cached_destination_path

                if len(dirs_matching_proj_num) == 1:
                    new_path = os.path.join(new_path, dirs_matching_proj_num[0])
                    new_path = path_from_project_num_dir_to_destination(new_path,
                                                                        self.nested_large_template_destination_dir(),
                                                                        self.assemble_destination_filename())
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
                                                                    large_template_destination=self.nested_large_template_destination_dir(),
                                                                    destination_filename=self.assemble_destination_filename())
                self.cached_destination_path = new_path
                return self.cached_destination_path


            self.cached_destination_path = new_path
        return self.cached_destination_path

    def attribute_defaultdict(self):
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
            self.file_code = utilities.file_code_from_destination_dir(self.destination_dir)

        if not self.project_number:
            self.project_number = utilities.project_number_from_path(self.get_destination_path())

        attribute_dict = {"date_archived": date_stamp, "project_number": self.project_number,
                          "destination_path": self.get_destination_path(), "document_date": doc_date,
                          "destination_directory": self.destination_dir, "file_code": self.file_code,
                          "file_size": self.size, "notes": self.notes}
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

        # if the file has already been archived return the destination path
        if self.datetime_archived:
            return self.get_destination_path()

        destination_path_list = utilities.split_path(self.get_destination_path())
        destination_dir_path = os.path.join(*destination_path_list[:-1])

        if not os.path.exists(destination_dir_path):
            os.makedirs(destination_dir_path)
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
