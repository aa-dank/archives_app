import os
import shutil
import helpers

class server_change:
    def __int__(self, change_type, new_path, old_path, archivist):
        self.change_type_possibilities = ('DELETE', 'RENAME', 'MOVE', 'MAKE')
        self.change_type = change_type
        self.new_path = helpers.mounted_path_to_network_path(new_path)
        self.old_path = helpers.mounted_path_to_network_path(old_path)
        self.archivist = archivist


    def execute(self):

        # If the change type is 'DELETE'
        if self.change_type.upper() == self.change_type_possibilities[0]:
            if os.path.isfile(self.old_path):
                os.remove(self.old_path)
                return True

            shutil.rmtree(self.old_path)
            return True

        # If the change type is 'RENAME'
        if self.change_type.upper() == self.change_type_possibilities[1]:
            old_path = self.old_path
            old_path_list = helpers.split_path(self.old_path)
            new_path_list = helpers.split_path(self.new_path)
            if not len(old_path_list) == len(new_path_list):
                raise Exception(
                    f"Attempt at renaming paths failed. Paths are not the same length: \n {self.new_path}\n{self.old_path}")
                return False

            while True:
                for idx, new_path_dir in enumerate(new_path_list):
                    if new_path_dir != old_path_list[idx]:
                        new_change_path = os.path.join(*new_path_list[:idx])
                        old_change_path = os.path.join(*old_path_list[:idx])
                        os.rename(old_change_path, new_change_path)
                        old_path = os.path.join(new_change_path, *old_path_list[idx:])
                        old_path_list = helpers.split_path(old_path)
                        break

        # if the change_type is 'MOVE'
        if self.change_type.upper() == self.change_type_possibilities[2]:
            #TODO complete this
            pass

        # if the change_type is 'MAKE'
        if self.change_type.upper() == self.change_type_possibilities[3]:
            if os.path.exists(self.new_path):
                raise Warning(f"Trying to make a directory that already exists: {self.new_path}")
            os.makedirs(self.new_path)
            return True

        return False