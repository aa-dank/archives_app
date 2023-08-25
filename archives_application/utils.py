import bz2
import fitz
import flask
import flask_sqlalchemy
import hashlib
import subprocess
import random
import re
import os
import pandas as pd
import psutil
import sys
from datetime import datetime
from flask_login import current_user
from functools import wraps
from PIL import Image, ImageFilter
from pathlib import Path, PureWindowsPath
from sqlalchemy import select
from sqlalchemy.sql.expression import func
from typing import Union 
from archives_application.models import WorkerTaskModel



def split_path(path):
    """
    Split a path into a list of directories/files/mount points. It is built to accomodate Splitting both Windows and Linux paths
    on linux systems. (It will not necessarily work to process linux paths on Windows systems)
    :param path: The path to split.
    """

    def detect_filepath_type(filepath):
        """
        Detects the cooresponding OS of the filepath. (Windows, Linux, or Unknown)
        :param filepath: The filepath to detect.
        :return: The OS of the filepath. (Windows, Linux, or Unknown)
        """
        windows_pattern = r"^[A-Za-z]:\\(.+)$"
        linux_pattern = r"^/([^/]+/)*[^/]+$"

        if re.match(windows_pattern, filepath):
            return "Windows"
        elif re.match(linux_pattern, filepath):
            return "Linux"
        else:
            return "Unknown"
        
    def split_windows_path(filepath):
        """"""
        parts = []
        curr_part = ""
        is_absolute = False

        if filepath.startswith("\\\\"):
            # UNC path
            parts.append(filepath[:2])
            filepath = filepath[2:]
        elif len(filepath) >= 2 and filepath[1] == ":":
            # Absolute path
            parts.append(filepath[:2])
            filepath = filepath[2:]
            is_absolute = True

        for char in filepath:
            if char == "\\":
                if curr_part:
                    parts.append(curr_part)
                    curr_part = ""
            else:
                curr_part += char

        if curr_part:
            parts.append(curr_part)

        if not is_absolute and not parts:
            # Relative path with a single directory or filename
            parts.append(curr_part)

        return parts
    
    def split_other_path(path):

        allparts = []
        while True:
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

    path = str(path)
    path_type = detect_filepath_type(path)
    
    if path_type == "Windows":
        return split_windows_path(path)
    
    return split_other_path(path)


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
            [network_re_matches.extend(re.findall(pattern, pth)) for pattern in url_patterns if re.findall(pattern, pth)]
            network_re_matches = list(set(network_re_matches))
            return network_re_matches

        # first find all instances in the path that match one of the network url patterns.
        url_regex_1 = r"([\w]{1,}[.]{1}[\w]{1,}[.]{1}[\w]{1,})"
        url_regex_2 = r"""
        \b                  # Word boundary
        (smb|nfs|ftp|ftps|)// # Protocol
        (?:[-\w.]+|[\d.]+)? # Host (domain or IP) 
        /[-\w.~/]+          # Path
        \b                  # Word boundary
        """
        network_url_patterns = [url_regex_1, url_regex_2]
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


def convert_tiff(tiff_path: str, destination_directory: str = None, output_file_type: str = 'jpg', max_width=1080):
    """
    Converts a tiff file to a jpg or png file. If a destination directory is not provided, the converted file will be
    saved in the same directory as the original file. If a destination directory is provided, the converted file will
    be saved in that directory. The converted file will have the same name as the original file, except with the
    extension changed to the output_file_type.
    :param tiff_path: path to the tiff file to be converted
    :param destination_directory: directory to save the converted file in
    :param output_file_type: the type of file to convert to. Must be either 'jpg' or 'png'
    :param max_width: the maximum width of the converted file in pixels
    :return: path to the converted file
    """
    # We are setting the max image pixels to None because the default value is too small
    # for some of the tiff files we are working with (blueprints).
    Image.MAX_IMAGE_PIXELS = None

    if output_file_type.lower() not in ['jpg', 'png']:
        raise ValueError("output_file_type must be either 'jpg' or 'png'")
    
    if not destination_directory:
        destination_directory = os.path.dirname(tiff_path)
    
    tiff_filename = os.path.basename(tiff_path)
    converted_filename = ".".join(tiff_filename.split(".")[:-1]) + "." + output_file_type
    converted_path = os.path.join(destination_directory, converted_filename)

    with Image.open(tiff_path) as tiff:
        
        if output_file_type == 'jpg':
            # not recommended to use pillow to convert tiff to jpg.
            tiff.save(converted_path, 'JPEG', quality=90)
        
        else:
            tiff.save(converted_path, output_file_type.upper())
    return converted_path


def create_temp_file_path(filename: str):
    """
    Pattern for creating a path for a temp file on the server.
    """
    return os.path.join(os.getcwd(), *["archives_application", "static", "temp_files", filename])


def get_hash(filepath, hash_algo=hashlib.sha1):
    """"
    This function takes a filepath and a hash algorithm as input and returns the hash of the file at the filepath
    """
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
    dt_stamp = datetime.now().strftime("%m/%d/%Y, %H:%M:%S")
    print(dt_stamp + "\n" + str(to_print), file=sys.stderr)


def enqueue_new_task(db, enqueued_function: callable, function_kwargs: dict = {}, enqueue_call_kwargs: dict = {}, timeout: Union[int, None] = None):
    """
    Adds a function to the rq task queue to be executed asynchronously. The function must have a paramater called 'queue_id' which will
    give the function access to the task id of the rq task. This can be used for updating the status of the task in the database.
    :param function: function to be executed
    :param function_kwargs: keyword arguments for the function
    :param timeout: timeout for the function. Measured in minutes.
    :return: None
    """
    def random_string(length=5):
        chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
        return ''.join(random.choice(chars) for _ in range(length))


    job_id = f"{enqueued_function.__name__}_{datetime.now().strftime(r'%Y%m%d%H%M%S')}"
    
    # Check if job_id already exists in the database. If it does, add a random string to the end of it.
    while db.session.query(WorkerTaskModel).filter(WorkerTaskModel.task_id == job_id).first():
        job_id = f"{enqueued_function.__name__}_{datetime.now().strftime(r'%Y%m%d%H%M%S')}_{random_string()}"
        
    
    function_kwargs['queue_id'] = job_id
    timeout = timeout * 60 if timeout else None
    enqueue_call_kwargs['job_id'] = job_id
    enqueue_call_kwargs['timeout'] = timeout
    enqueue_call_kwargs['func'] = enqueued_function
    enqueue_call_kwargs['kwargs'] = function_kwargs

    task = flask.current_app.q.enqueue_call(**enqueue_call_kwargs)
    new_task_record = WorkerTaskModel(task_id=job_id,
                                      time_enqueued=str(datetime.now()),
                                      origin=task.origin,
                                      function_name=enqueued_function.__name__,
                                      status="queued")
    db.session.add(new_task_record)
    db.session.commit()
    results = task.__dict__
    results["task_id"] = job_id
    return results


def initiate_task_subroutine(q_id, sql_db):
    """
    Updates the database to indicate that the task has started. 
    This is meant to be called at the begining of a task sent to the rq worker.
    :param q_id: the task id of the task being executed
    :param sql_db: the database object
    """
    start_task_db_updates = {"status": 'started'}
    sql_db.session.query(WorkerTaskModel).filter(WorkerTaskModel.task_id == q_id).update(start_task_db_updates)
    sql_db.session.commit()


def complete_task_subroutine(q_id, sql_db, task_result):
    """
    Updates the database to indicate that the task has completed.
    This is meant to be called at the end of a task sent to the rq worker.
    :param q_id: the task id of the task being executed
    :param sql_db: the database object
    """
    # update the database to indicate that the task has completed
    task_db_updates = {"status": 'finished', "task_results": task_result, "time_completed":datetime.now()}
    sql_db.session.query(WorkerTaskModel).filter(WorkerTaskModel.task_id == q_id).update(task_db_updates)
    sql_db.session.commit()


def failed_task_subroutine(q_id, sql_db, task_result):
    """
    Updates the database to indicate that the task has failed.
    This is meant to be called at after an exception is raised in a task sent to the rq worker.
    :param q_id: the task id of the task being executed
    :param sql_db: the database object
    :param task_result: dicionary data from the task having run
    """
    task_db_updates = {"status": 'failed', "task_results": task_result, "time_completed":datetime.now()}
    sql_db.session.query(WorkerTaskModel).filter(WorkerTaskModel.task_id == q_id).update(task_db_updates)
    sql_db.session.commit()


def db_query_to_df(query: flask_sqlalchemy.query.Query, dataframe_size_limit= None, query_count_concern_threshold = 100000):
    """
    Converts a sqlalchemy query to a pandas dataframe. checks the size of the dataframe and raises a ValueError if it is too large.
    :param query: the sqlalchemy query to be converted
    :param dataframe_size_limit: the maximum size of the dataframe in bytes. If the dataframe is larger than this, a ValueError will be raised.
    :param query_count_concern_threshold: the number of rows in the query that will trigger an estimation of the query size.
    """
    
    def get_row_size_estimate(sample_size = 50):
        """
        Creates a random sample of rows from the query and returns the average size of the rows in bytes.
        """
        subquery = query.order_by(func.random()).limit(sample_size).subquery()
        sample = select(subquery).execute().fetchall()
        sample_df = pd.DataFrame([row.__dict__ for row in sample])
        sample_average = sample_df.memory_usage(deep=True).sum() / sample_size
        return sample_average 
    
    # calculate a sensible dataframe size limit if one is not provided
    if not dataframe_size_limit:
        available_memory = psutil.virtual_memory().available
        memory_usage_buffer = 10000000 if (available_memory * .1) < 10000000 else available_memory * .1
        dataframe_size_limit = psutil.virtual_memory().available - memory_usage_buffer
    
    # check if the query results are too large to be returned as a dataframe
    query_results_count = query.count()
    if query_results_count > query_count_concern_threshold:
        total_df_size_estimate = get_row_size_estimate() * query_results_count
        if total_df_size_estimate > dataframe_size_limit:
            e_str = f"Query results are too large to be returned as a dataframe. \n Estimated size: {total_df_size_estimate} bytes \n Limit: {dataframe_size_limit} bytes \n Query results count: {query_results_count} \n Query: {query.statement}"
            raise ValueError(e_str)
    
    results = query.all()
    df = pd.DataFrame([row.__dict__ for row in results])
    
    # drop the sqlalchemy state column if it exists
    state_col = '_sa_instance_state'
    if state_col in df.columns:
        df = df.drop(columns=[state_col])
    return df
