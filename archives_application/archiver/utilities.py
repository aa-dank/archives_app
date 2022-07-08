import fitz
import subprocess
import re
import os
import sys
from archives_application import config as config
from PIL import Image

def split_path(path):
    '''splits a path into each piece that corresponds to a mount point, directory name, or file'''
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


def mounted_path_to_networked_path(mounted_path, network_location=config.RECORDS_SERVER_LOCATION):
    """

    :param mounted_path: string version of the path
    :param network_location:
    :return:
    """

    def is_similar_ip_address(ip_address1, ip_address2):
        just_the_digits = lambda s: [char for char in s if char.isdigit()]
        return just_the_digits(ip_address1) == just_the_digits(ip_address2)

    mounted_path_list = split_path(mounted_path)
    # If mounted_path is already a network path to same location, 
    if is_similar_ip_address(mounted_path_list[0], network_location):
        return mounted_path

    # need to determine if the path is actually a mounted path
    count_alpha_chars = lambda s: len([char for char in s if char.isalpha()])
    if not count_alpha_chars(mounted_path_list[0]) == 1:
        raise Exception(f"mounted_path parameter doesn't appear to have a mount point: \n{mounted_path}")

    mounted_path_list[0] = network_location
    return os.path.join(*mounted_path_list)


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
    return output_path