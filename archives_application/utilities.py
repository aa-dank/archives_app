import datetime
import fitz
import flask
import hashlib
import subprocess
import re
import os
import sys
from flask_login import current_user
from functools import wraps
from PIL import Image
from pathlib import Path, PureWindowsPath, PurePosixPath



def split_path(path):
    '''splits a path into each piece that corresponds to a mount point, directory name, or file'''
    path = str(path)
    allparts = []
    while 1:
        parts = os.path.split(path)
        if parts[0] == path:  # sentinel for absolute paths
            allparts.insert(0, parts[0])
            break
        elif parts[1] == path:  # sentinel for relative paths
            allparts.insert(0, parts[1])
            break
        else:
            path = parts[0]
            allparts.insert(0, parts[1])
    return allparts


def roles_required(roles: list[str]):
    """
    This function is a Flask decorator that restricts access to a route to only users with certain roles. The roles
    parameter is a list of allowed roles. The decorator takes a function func as an argument and returns a new function
    that wraps the original function.

    When the wrapped function is called, the user's roles are retrieved and split into a list of individual roles. If
    the user has at least one role and at least one of those roles is in the roles list, the original function func is
    called with the original arguments and keyword arguments. Otherwise, the user is shown a warning message and
    redirected to the home page.

    :param roles: list of the roles that can access the endpoint
    :return: actual decorator function
    """

    def decorator(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            # if the user has at least a single role and at least one of the user roles is in roles...
            if hasattr(current_user, 'roles') and [role for role in roles if role in current_user.roles.split(",")]:
                return func(*args, **kwargs)
            else:
                mssg = "Access Denied. Are you logged in? Do you have the correct account role to access this?"
                flask.flash(mssg, 'warning')
                return flask.redirect(flask.url_for('main.home'))

        return wrap

    return decorator


def prefixes_from_project_number(project_no: str):
    """
    returns root directory prefix for given project number.
    eg project number 10638 returns 106xx, project number 9805A returns 98xx
    :param project_no: string project number
    :return: project directory root directory prefix for choosing correct root directory
    """
    project_no = project_no.split("-")[0]
    project_no = ''.join(i for i in project_no if i.isdigit())
    prefix = project_no[:3]
    if len(project_no) <= 4:
        prefix = project_no[:2]
    return prefix + 'xx', project_no


def file_code_from_destination_dir(destination_dir_name):
    """

    :param destination_dir_name: full destination directory name
    :return: string filing code
    """
    file_code = ''
    dir_name_index = 0
    while destination_dir_name[dir_name_index] != '-':
        file_code += destination_dir_name[dir_name_index]
        dir_name_index += 1
    return file_code.strip().upper()


def open_file_with_system_application(filepath):
    """
    System agnostic file opener
    :param filepath: str path to file that will be opened
    :return:
    """

    system_identifier = sys.platform
    if system_identifier.lower().startswith("linux"):
        subprocess.call(('xdg-open', filepath))
        return
    if system_identifier.lower().startswith("darwin"):
        subprocess.call(('open', filepath))
        return
    else:
        os.startfile(filepath)
        return



def clean_path(path: str):
    """
    Process a path string such that it can be used regardless of the os and regardless of whether its length
    surpasses the limit in windows file systems
    :param path:
    :return:
    """
    path = path.replace('/', os.sep).replace('\\', os.sep)
    if os.sep == '\\' and '\\\\?\\' not in path:
        # fix for Windows 260 char limit
        relative_levels = len([directory for directory in path.split(os.sep) if directory == '..'])
        cwd = [directory for directory in os.getcwd().split(os.sep)] if ':' not in path else []
        path = '\\\\?\\' + os.sep.join(cwd[:len(cwd) - relative_levels] \
                                       + [directory for directory in path.split(os.sep) if directory != ''][
                                         relative_levels:])
    return path


def is_valid_email(potential_email: str):
    email_regex = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    return re.fullmatch(email_regex, potential_email)


def mounted_path_to_networked_path(mounted_path, network_location):
    """

    :param mounted_path: string version of the path
    :param network_location:
    :return:
    """
    def is_already_network_location(location, some_network_location): #TODO fix this sub-function
        test_location = "".join(i for i in str(location) if i not in "\/:.")
        test_network_loc = "".join(i for i in str(some_network_location) if i not in "\/:.")
        if test_location.lower().startswith(test_network_loc.lower()):
            return True
        return False

    mounted_path = Path(mounted_path)
    new_path_list = [network_location] + list(mounted_path.parts[1:])
    new_network_path = os.path.join(*new_path_list)
    if not new_network_path.startswith(r"\\"):
        if new_network_path.startswith("/"):
            new_network_path = new_network_path.lstrip("/" + "\\")
        new_network_path = "\\\\" + new_network_path
    return new_network_path


def user_path_to_app_path(path_from_user, location_path_prefix):
    '''
    Converts the location entered by the user to a local_path that can be used by the application server.
    Attempts to handle network paths and mounted windows paths from user.
    Attempts to handle server location_path_prefix that are either network paths or linux mount paths.
    @param path_from_user: path of asset from the user
    @param location_path_prefix: Base location where the path_from_user can be found.
    @return:
    '''

    def matches_network_url(some_path: str):
        """
        The function first defines a nested sub-function url_regex_matches, which takes a path and a list of URL patterns
        as input and returns a list of all matches of the URL patterns in the path. The URL patterns used are defined as
        network_url_patterns.

        Then the function finds all instances in the path that match one of the network url patterns, using the sub-function
        url_regex_matches. If no matches are found, the function confirms that the path is not a network URL and returns False.

        If matches are found, the function removes confounding characters and strings from the path and the matches,
        and checks whether any of the modified matches is at the beginning of the modified path. If a match is found,
        the function returns True, indicating that the path is a network URL. Otherwise, it returns False.
        @param some_path: path or url string
        @return: Bool
        """
        def url_regex_matches(pth: str, url_patterns):
            network_re_matches = []
            [network_re_matches.append(*re.findall(pattern, pth)) for pattern in url_patterns if re.findall(pattern, pth)]
            return network_re_matches

        # first find all instances in the path that match one of the network url patterns.
        url_regex_1 = r"([\w]{1,}[.]{1}[\w]{1,}[.]{1}[\w]{1,})"
        network_url_patterns = [url_regex_1]
        pattern_matches = url_regex_matches(pth=some_path, url_patterns=network_url_patterns)

        # if no regex patterns match anything, We have confirmed it is not a network path
        if not pattern_matches:
            return False

        # modify the path and url matches to remove confounding strings and chars.
        # Then see if the network url match is at the begining of the path
        modified_test_str = lambda input_str: re.sub(r'[^a-zA-Z0-9\.]|(file|http|https)', '', input_str).lower()
        test_path = modified_test_str(some_path)
        pattern_matches = [modified_test_str(match) for match in pattern_matches]
        is_network_url = any([test_path.startswith(match) for match in pattern_matches])
        return is_network_url

    # If we are not using a network url then the location prefix is the mount location on either a windows or
    # linux machine.
    if not matches_network_url(location_path_prefix):

        if matches_network_url(path_from_user):
            # mapping a network url entered by the user to the linux mount location equivalent is a difficult problem.
            # Probably requires looking at how the server is mounted using linux 'mount' command
            raise Exception("Application limitation -- Unable to map from a network url location to a mounted location.")

        path_from_user = PureWindowsPath(path_from_user)
        user_path_list = list(path_from_user.parts)

        server_mount_path_list = split_path(location_path_prefix)
        local_path_list = server_mount_path_list + user_path_list[1:]
        app_path = os.path.join(*local_path_list)
        return app_path

    # following is for Windows machine. ie location_path_prefix is a local network url
    if matches_network_url(path_from_user):
        app_path = "\\\\" + path_from_user.lstrip("/" + "\\")
    if not matches_network_url(path_from_user):
        app_path = mounted_path_to_networked_path(mounted_path=path_from_user, network_location=location_path_prefix)

    return app_path


def cleanse_filename(proposed_filename: str):
    clean_filename = proposed_filename.replace('\n', '')
    clean_filename = "".join(i for i in clean_filename if i not in "\/:*?<>|")
    clean_filename = clean_filename.strip()
    return clean_filename


def pdf_preview_image(pdf_path, image_destination, max_width=1080):
    """

    :param pdf_path:
    :param image_destination:
    :return:
    """
    # Turn the pdf filename into equivalent png filename and create destination path
    pdf_filename = split_path(pdf_path)[-1]
    preview_filename = ".".join(pdf_filename.split(".")[:-1])
    preview_filename += ".png"
    output_path = os.path.join(image_destination, preview_filename) #TODO avoid filename of existing file

    # use pymupdf to get pdf data for pillow Image object
    fitz_doc = fitz.open(pdf_path)
    page_pix_map = fitz_doc.load_page(0).get_pixmap()
    page_img = Image.frombytes("RGB", [page_pix_map.width, page_pix_map.height], page_pix_map.samples)

    # if the preview image is beyond our max_width we resize it to that max_width
    if page_img.width > max_width:
        max_width_percent = (max_width / float(page_img.size[0]))
        hsize = int((float(page_img.size[1]) * float(max_width_percent)))
        page_img = page_img.resize((max_width, hsize), Image.ANTIALIAS)

    page_img.save(output_path)
    fitz_doc.close()
    return output_path

def establish_location_path(location, sqlite_url=False):
    # TODO the logic of this function is poorly tested.
    # example of working test config url: r'sqlite://///ppcou.ucsc.edu\Data\Archive_Data\archives_app.db'
    sqlite_prefix = r"sqlite://"
    is_network_path = lambda some_path: (os.path.exists(r"\\" + some_path), os.path.exists(r"//" + some_path))
    bck_slsh, frwd_slsh = is_network_path(location)
    has_sqlite_prefix = location.lower().startswith("sqlite")

    # if network location, process as such, including
    if frwd_slsh:
        location = r"//" + location
        if (os.name in ['nt']) and (not has_sqlite_prefix) and sqlite_url:
            location = r"/" + location
        if sqlite_url and not has_sqlite_prefix:
            location = sqlite_prefix + location
        return location

    if bck_slsh:
        location = r"\\" + location
        return location

    return location


def get_hash(filepath, hash_algo=hashlib.sha1):
    def chunk_reader(fobj, chunk_size=1024):
        """ Generator that reads a file in chunks of bytes """
        while True:
            chunk = fobj.read(chunk_size)
            if not chunk:
                return
            yield chunk

    hashobj = hash_algo()
    with open(filepath, "rb") as f:
        for chunk in chunk_reader(f):
            hashobj.update(chunk)

    return hashobj.hexdigest()


def debug_printing(to_print):
    dt_stamp = datetime.datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
    print(dt_stamp + "\n" + str(to_print), file=sys.stderr)