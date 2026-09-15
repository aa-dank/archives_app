"""Shared archive file exclusion policy."""

from archives_application import utils


EXCLUDED_FILENAMES = ['Thumbs.db', 'thumbs.db', 'desktop.ini']
EXCLUDED_FILE_EXTENSIONS = ['DS_Store', '.ini', '.git']


def exclude_extensions(f_path, extensions_list=EXCLUDED_FILE_EXTENSIONS):
    """Return whether a path uses one of the excluded file extensions."""
    filename = utils.FileServerUtils.split_path(f_path)[-1].lower()
    return any(filename.endswith(ext.lower()) for ext in extensions_list)


def exclude_filenames(f_path, excluded_names=EXCLUDED_FILENAMES):
    """Return whether a path uses one of the excluded file names."""
    filename = utils.FileServerUtils.split_path(f_path)[-1].lower()
    return any(filename == name.lower() for name in excluded_names)
