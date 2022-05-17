import os
import shutil
import archiver.helpers as helpers

class ServerEdit:
    def __init__(self, change_type, user, new_path=None, old_path=None):
        """

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
            self.new_path = helpers.mounted_path_to_networked_path(new_path)
        self.old_path = None
        if old_path:
            self.old_path = helpers.mounted_path_to_networked_path(old_path)
        self.user = user
        self.change_executed = False

    def execute(self):

        # If the change type is 'DELETE'
        if self.change_type.upper() == self.change_type_possibilities[0]:
            if os.path.isfile(self.old_path):
                os.remove(self.old_path)
                self.change_executed = True
                return self.change_executed

            shutil.rmtree(self.old_path)
            self.change_executed = True
            return self.change_executed

        # If the change type is 'RENAME'
        if self.change_type.upper() == self.change_type_possibilities[1]:
            old_path = self.old_path
            old_path_list = helpers.split_path(old_path)
            new_path_list = helpers.split_path(self.new_path)
            if not len(old_path_list) == len(new_path_list):
                raise Exception(
                    f"Attempt at renaming paths failed. Paths are not the same length: \n {self.new_path}\n{self.old_path}")
                return self.change_executed

            while True:
                if old_path == self.new_path:
                    break
                for idx, new_path_dir in enumerate(new_path_list):
                    if new_path_dir != old_path_list[idx]:
                        new_change_path = os.path.join(*new_path_list[:idx+1]) #TODO test these indexes are correct
                        old_change_path = os.path.join(*old_path_list[:idx+1])
                        os.rename(old_change_path, new_change_path)
                        old_path = os.path.join(new_change_path, *old_path_list[idx+1:])
                        old_path_list = helpers.split_path(old_path)
                        break

            self.change_executed = True
            return self.change_executed

        # if the change_type is 'MOVE'
        if self.change_type.upper() == self.change_type_possibilities[2]:
            filename = helpers.split_path(self.old_path)[-1]
            destination_path = os.path.join(self.new_path, filename)
            if os.path.isfile(self.old_path):
                shutil.copyfile(src=self.old_path, dst=destination_path)
                self.change_executed = True
                return self.change_executed

            shutil.move(self.old_path, destination_path, copy_function=shutil.copytree)
            self.change_executed = True
            return self.change_executed

        # if the change_type is 'MAKE'
        if self.change_type.upper() == self.change_type_possibilities[3]:
            if os.path.exists(self.new_path):
                raise Warning(f"Trying to make a directory that already exists: {self.new_path}")
            os.makedirs(self.new_path)
            self.change_executed = True
            return self.change_executed

        return self.change_executed

