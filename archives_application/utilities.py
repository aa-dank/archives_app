import fitz
import subprocess
import re
import os
import sys
import flask
from flask_login import current_user
from functools import wraps
from PIL import Image
from pathlib import Path




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
    :param roles: list of the roles that can access the endpoint
    :return: actual decorator function
    """

    def decorator(func):
        @wraps(func)
        def wrap(*args, **kwargs):
            user_role_list = current_user.roles.split(",")
            # if the user has at least a single role and at least one of the user roles is in roles...
            if hasattr(current_user, 'roles') and [role for role in roles if role in user_role_list]:
                return func(*args, **kwargs)
            else:
                flask.flash("Need a different role to access this.", 'warning')
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
    def is_already_network_location(location, some_network_location):
        test_location = "".join(i for i in str(location) if i not in "\/:.")
        test_network_loc = "".join(i for i in str(some_network_location) if i not in "\/:.")
        if test_location.startswith(test_network_loc):
            return True
        return False

    if is_already_network_location(location=mounted_path, some_network_location=network_location):
        if not mounted_path.startswith(r"//"):
            mounted_path = "//" + mounted_path
        return mounted_path

    mounted_path = Path(mounted_path)
    network_loc_as_path = Path(network_location)
    new_path_list = list(network_loc_as_path.parts) + list(mounted_path.parts[1:])
    new_network_path = os.path.join(*new_path_list)
    if not new_network_path.startswith(r"//"):
        new_network_path = "//" + new_network_path
    return new_network_path


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
    fitz_doc = fitz.open(pdf_path) #TODO do I need to close this?
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