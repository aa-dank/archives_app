# archives_application/app_config.py

import flask
import json
import logging



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


def assemble_redis_url(redis_location, redis_port):
    return'redis://' + redis_location + ":" + redis_port


def json_to_config_factory(google_creds_path: str, config_json_path: str):
    """
    THis function turns a json file of config info and a google credentials json file into a flask app config class.
    The purpose is to allow the changing of app settings using a json file. Where different json files represent new
    configurations and configurations can be changed by changing the json file
    :param config_json_path: string path
    :param google_creds_path: string path
    :return: DynamicServerConfig class with json keys as attributes
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
                                                                        port=config_dict["POSTGRESQL_PORT"],
                                                                        ssl=config_dict["POSTGRESQL_SSL"])
    config_dict['CONFIG_JSON_PATH'] = config_json_path

    # Assemble Redis url
    if config_dict.get('REDIS_LOCATION'):
        config_dict['REDIS_URL'] = assemble_redis_url(redis_location=config_dict.get('REDIS_LOCATION'),
                                                      redis_port=config_dict.get('REDIS_PORT'))

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


