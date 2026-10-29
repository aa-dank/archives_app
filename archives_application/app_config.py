# archives_application/app_config.py

import flask
import json
import logging
import math
import os
import pathlib
import tomllib

from archives_application.cache import KeyValueCache, positive_integer


CACHE_DEFAULTS = {
    "ARCHIVE_SEARCH_CACHE_ENABLED": False,
    "ARCHIVE_SEARCH_CACHE_TTL_SECONDS": 432000,
    "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS": 518400,
    "CACHE_NAMESPACE": None,
    "CACHE_TIMEOUT_SECONDS": 0.5,
    "CACHE_MAX_ENTRY_BYTES": 5242880,
}


def configure_cache(app) -> None:
    """Apply optional defaults and register one independent cache pool per app/process.

    Initialization performs no network I/O. Disabled or invalid configuration
    leaves app.extensions["cache"] as None, logs the effective state, and keeps
    the fresh-search workflow and existing RQ connection available.
    """
    for name, default in CACHE_DEFAULTS.items():
        app.config.setdefault(name, default)
    app.extensions["cache"] = None
    enabled = app.config["ARCHIVE_SEARCH_CACHE_ENABLED"]
    try:
        if type(enabled) is not bool:
            raise ValueError("ARCHIVE_SEARCH_CACHE_ENABLED must be a boolean")
        # Workbook retention remains active when result caching is disabled, so
        # validate the lifetime settings before taking the disabled-cache branch.
        retention = positive_integer(
            app.config["ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS"],
            "ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS",
        )
        ttl = positive_integer(app.config["ARCHIVE_SEARCH_CACHE_TTL_SECONDS"],
                               "ARCHIVE_SEARCH_CACHE_TTL_SECONDS")
        if retention <= ttl:
            raise ValueError("ARCHIVE_SEARCH_WORKBOOK_RETENTION_SECONDS must exceed the search lifetime")
        if not enabled:
            app.logger.info("archive_search_cache state=disabled")
            return
        namespace = app.config["CACHE_NAMESPACE"]
        if not isinstance(namespace, str) or not namespace.strip():
            raise ValueError("CACHE_NAMESPACE must be a nonempty environment/dataset prefix")
        url = app.config.get("KEY_VALUE_STORE_URL")
        if not isinstance(url, str) or not url.strip():
            raise ValueError("KEY_VALUE_STORE_URL is required when caching is enabled")
        positive_integer(app.config["CACHE_MAX_ENTRY_BYTES"], "CACHE_MAX_ENTRY_BYTES")
        timeout = app.config["CACHE_TIMEOUT_SECONDS"]
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("CACHE_TIMEOUT_SECONDS must be a positive finite number")
        try:
            app.extensions["cache"] = KeyValueCache.from_url(url, timeout)
        except (ValueError, TypeError, OverflowError):
            # URL parsing errors can contain credentials. Report only the setting.
            raise ValueError("KEY_VALUE_STORE_URL is invalid for the key-value cache adapter") from None
    except (ValueError, TypeError, OverflowError) as error:
        app.logger.error("archive_search_cache state=disabled reason=invalid_configuration detail=%s", error)
        return
    app.logger.info("archive_search_cache state=enabled namespace=%s ttl_seconds=%s",
                    namespace, ttl)

def retrieve_app_version():
    """
    Retrieves the version of the app from the pyproject.toml file. This is used for logging purposes.
    :return: string version number
    """
    v_number = None
    with open(pathlib.Path(__file__).parent.parent / "pyproject.toml", "rb") as f:
        v_number = tomllib.load(f)["project"]["version"]
    return v_number

def google_creds_from_creds_json(creds_path):
    with open(creds_path) as creds_json:
        creds_dict = json.load(creds_json)['web']
        client_id = creds_dict.get('client_id')
        client_secret = creds_dict.get('client_secret')

    return client_id, client_secret

def assemble_postgresql_url(host, db_name, username, password="", port="", dialect="", ssl=""):
    '''
    Assembles a postgresql url from the component parameters.
    @param host: Host location
    @param db_name: database name on psql server
    @param username:
    @param password:
    @param port:
    @param dialect: Which dialect to use. Options are psycopg2, pg8000, asyncpg, or None. https://docs.sqlalchemy.org/en/14/core/engines.html
    @return: string url
    '''
    if port:
        port = ":" + port

    if password:
        password = ":" + password

    if dialect:
        dialect = "+" + dialect

    uri = f"postgresql{dialect}://{username}{password}@{host}{port}/{db_name}"
    if ssl.lower() == 'true':
        uri = uri + "?sslmode=require"

    return uri

def resolve_key_value_store_url(config, environ=None) -> str:
    """Resolve the one endpoint shared by the cache, web queue, and RQ worker.

    Only KEY_VALUE_STORE_URL is recognized. Process environment takes precedence
    over JSON/class values. An explicit empty or invalidly typed URL is a
    configuration error, never a silent fallback.
    Return the URL without logging credentials or changing its connection options.
    """
    environment = os.environ if environ is None else environ
    for source in (environment, config):
        if "KEY_VALUE_STORE_URL" in source:
            url = source["KEY_VALUE_STORE_URL"]
            if not isinstance(url, str) or not url.strip():
                raise ValueError("KEY_VALUE_STORE_URL must be a nonempty string")
            return url.strip()
    raise ValueError("KEY_VALUE_STORE_URL is required for the shared key-value store connection")


def key_value_store_url_from_config_file(config_json_path: str) -> str:
    """Load the worker's shared endpoint from the selected VALUE/DESCRIPTION JSON.

    Use the same resolver as app initialization so environment overrides
    cannot send the producer and worker to different services.
    This helper reads connection configuration without creating an app or queue.
    """
    with open(config_json_path) as config_file:
        entries = json.load(config_file)
    return resolve_key_value_store_url({key: entry["VALUE"] for key, entry in entries.items()})

def json_to_config_factory(google_creds_path: str, config_json_path: str):
    """Build a Flask configuration class from described JSON values and OAuth inputs.

    Unwrap VALUE entries, assemble the existing database URI, and resolve the
    shared key-value endpoint using the same precedence as the worker and app factory.
    DESCRIPTION entries remain in the source JSON for the configuration editor.

    :param config_json_path: Selected application JSON file.
    :param google_creds_path: OAuth JSON containing the web client credentials.
    :return: DynamicServerConfig class with resolved settings as attributes.
    """
    with open(config_json_path) as config_file:
        config_dict = json.load(config_file)

    # Remove sub-dictionary and descriptions
    config_dict = {k: config_dict[k]['VALUE'] for k in list(config_dict.keys())}
    config_dict['GOOGLE_CLIENT_ID'], config_dict['GOOGLE_CLIENT_SECRET'] = google_creds_from_creds_json(google_creds_path)
    config_dict['OAUTHLIB_INSECURE_TRANSPORT'] = True
    # this url is where other api endpoints in the google ecosystem are indexed
    config_dict['GOOGLE_DISCOVERY_URL'] = (r"https://accounts.google.com/.well-known/openid-configuration")

    # Assemble Sqlalchemy url
    config_dict['SQLALCHEMY_DATABASE_URI'] = assemble_postgresql_url(host=config_dict["Sqalchemy_Database_Location"],
                                                                        db_name=config_dict["POSTGRESQL_DATABASE"],
                                                                        username=config_dict["POSTGRESQL_USERNAME"],
                                                                        password=config_dict["POSTGRESQL_PASSWORD"],
                                                                        dialect="psycopg",
                                                                        port=config_dict["POSTGRESQL_PORT"],
                                                                        ssl=config_dict["POSTGRESQL_SSL"])
    config_dict['CONFIG_JSON_PATH'] = config_json_path

    # Resolve the shared endpoint once, applying environment overrides to
    # web and worker alike.
    config_dict['KEY_VALUE_STORE_URL'] = resolve_key_value_store_url(config_dict)

    # The DynamicServerConfig class is created dynamically using Python's type() function, which takes three arguments:
    # the name of the class, a tuple of parent classes (empty in this case), and a dictionary of attributes and their
    # values, the config_dict here.
    return type("DynamicServerConfig", (), config_dict)

def setup_sql_logging(log_filepath):
    """
    Set up a logger instance to log SQLAlchemy database activity to a file and to the console.

    @param log_filepath:
    @return:
    """
    handler = logging.RotatingFileHandler(log_filepath, maxBytes=10000, backupCount=1)
    handler.setLevel(logging.DEBUG)
    if flask.current_app.config.get('DEFAULT_LOGGING_FORMATTER'):
       formatter = flask.current_app.config.get('DEFAULT_LOGGING_FORMATTER')
       handler.setFormatter(formatter)
    db_logger = logging.getLogger('sqlalchemy.engine')
    db_logger.addHandler(handler)

    # Add a StreamHandler to log to the console as well
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    if flask.current_app.config.get('DEFAULT_LOGGING_FORMATTER'):
        formatter = flask.current_app.config.get('DEFAULT_LOGGING_FORMATTER')
        console_handler.setFormatter(formatter)
    db_logger.addHandler(console_handler)
    db_logger.setLevel(logging.DEBUG)
    return db_logger


