import os
import shutil
from .. import utilities


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
        self.change_type_possibilities = ('DELETE', 'RENAME', 'MOVE', 'CREATE')
        self.change_type = change_type
        self.new_path = None
        if new_path:
            self.new_path = utilities.user_path_to_app_path(path_from_user=new_path,
                                                            location_path_prefix=server_location)

        self.old_path = None
        if old_path:
            self.old_path = utilities.user_path_to_app_path(path_from_user=old_path,
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
            if self.data_effected and self.data_effected > effected_data_limit:
                raise Exception(
                    f"ServerEdit data limit breached. Too much data effected by change type '{self.change_type}'.\nOld path: {self.old_path}\nNew path: {self.new_path}")

            if self.files_effected and self.files_effected > files_limit:
                raise Exception(
                    f"ServerEdit file limit breached. Too many files effected by change type '{self.change_type}.'\nOld path: {self.old_path}\nNew path: {self.new_path}")


        def get_quantity_effected(dir_path):
            for root, _, files in os.walk(dir_path):
                for file in files:
                    self.files_effected += 1
                    self.data_effected += os.path.getsize(os.path.join(root, file))


        # If the change type is 'DELETE'
        if self.change_type.upper() == self.change_type_possibilities[0]:
            if os.path.isfile(self.old_path):
                self.data_effected = os.path.getsize(self.old_path)
                os.remove(self.old_path)
                self.change_executed = True
                self.files_effected = 1
                return self.change_executed

            # if the deleted asset is a dir we need to add up all the files and their sizes before removing
            get_quantity_effected(self.old_path)

            # make sure change is not in excess of limits set
            check_against_limits()

            # remove directory and contents
            shutil.rmtree(self.old_path)
            self.change_executed = True
            return self.change_executed

        # If the change type is 'RENAME'
        if self.change_type.upper() == self.change_type_possibilities[1]:
            old_path = self.old_path
            old_path_list = utilities.split_path(old_path)
            new_path_list = utilities.split_path(self.new_path)
            if not len(old_path_list) == len(new_path_list):
                raise Exception(
                    f"Attempt at renaming paths failed. Paths are not the same length: \n {self.new_path}\n{self.old_path}")
                return self.change_executed

            # if this a change of a filepath, we need to cleanse the filename
            if os.path.isfile(old_path):
                self.files_effected = 1
                self.data_effected = os.path.getsize(old_path) #bytes
                new_path_list[-1] = utilities.cleanse_filename(new_path_list[-1])

            else:
                get_quantity_effected(self.old_path)

            # make sure change is not in excess of limits set
            check_against_limits()
            while True:
                if old_path == self.new_path:
                    break
                for idx, new_path_dir in enumerate(new_path_list):
                    if new_path_dir != old_path_list[idx]:
                        new_change_path = os.path.join(*new_path_list[:idx+1])
                        old_change_path = os.path.join(*old_path_list[:idx+1])
                        try:
                            os.rename(old_change_path, new_change_path)
                            old_path = os.path.join(new_change_path, *old_path_list[idx+1:])
                            old_path_list = utilities.split_path(old_path)
                        except Exception as e:
                            raise Exception(f"There was an issue trying to change the name. If it is permissions issue, consider that it might be someone using a directory that would be changed \n{e}")
                        break

            self.change_executed = True
            return self.change_executed

        # if the change_type is 'MOVE'
        if self.change_type.upper() == self.change_type_possibilities[2]:
            filename = utilities.split_path(self.old_path)[-1]
            destination_path = os.path.join(self.new_path, filename)
            if os.path.isfile(self.old_path):
                self.files_effected = 1
                self.data_effected = os.path.getsize(self.old_path)
                shutil.copyfile(src=self.old_path, dst=destination_path)
                os.remove(self.old_path)
                self.change_executed = True
                return self.change_executed

            else:
                get_quantity_effected(self.old_path)

            # make sure change is not in excess of limits set
            check_against_limits()

            # move directory and contents
            shutil.move(self.old_path, destination_path, copy_function=shutil.copytree)
            self.change_executed = True
            return self.change_executed

        # if the change_type is 'MAKE'
        if self.change_type.upper() == self.change_type_possibilities[3]:
            if os.path.exists(self.new_path):
                raise Warning(f"Trying to make a directory that already exists: {self.new_path}")
            os.makedirs(self.new_path)
            self.change_executed = True
            self.files_effected = 0
            self.data_effected = 0
            return self.change_executed

        return self.change_executed


