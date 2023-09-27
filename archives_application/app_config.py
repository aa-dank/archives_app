import flask
import json
import logging
import os
import subprocess
import time



DIRECTORY_CHOICES = ['A - General', 'B - Administrative Reviews and Approvals', 'C - Consultants',
                     'D - Environmental Review Process', 'E - Program and Design',
                     'F - Bid Documents and Contract Award', 'G - Construction', "H - Submittals and O&M's",
                     'A1 - Miscellaneous', 'A2 - Working File', 'A3 - Project Directory Matrix & Project Chronology',
                     "B1 - CPS and Chancellor's Approvals", 'B11 - LEED', 'B12 - Outside Regulatory Agencies',
                     'B13 - Coastal Commission', 'B2 - Office of the President UC Regents',
                     'B3 - State Public Works Board', 'B4 - Department of Finance', 'B5 - Legislative Submittals',
                     'B6 - State Fire Marshal', 'B7 - Office of State Architect  (DSA)', 'B8 -  General Counsel',
                     'B8.1 - General Counsel - Confidential', 'B10 - Storm Water Pollution Prevention Plan (SWPPP)',
                     'B11 - Leadership in Energy & Environmental Design (LEED)', 'B12 - Outside Regulatory Agencies',
                     'B13 - Coastal Commission Approval',  'C1 - Executive Architect', 'C1.1 - Selection',
                     'C1.2 - Correspondence', 'C1.3 - Agreement', 'C2 - Other Consultants', 'C2.1 - Selection',
                     'C2.2 - Correspondence', 'C2.3 - Agreement', 'D1 - Environmental Correspondences',
                     'D2 - EIC Forms', 'D3 - CEQA Documentation', 'D4 - Mitigation Monitoring Program', 'E1 - DPP',
                     'E2 - PPG', 'E3 - Budget Cost Estimates', 'E4 - Planning Schedules',
                     'E5 - Program and Design Correspondences', 'E5.1 - Executive Architect Correspondences',
                     'E5.2 - Special Consultants', 'E5.3 - Users. Building Committee. Campus Correspondences',
                     'E5.4 - PPC and PP', 'E5.5 - Office of the President to.from', 'E5.6 - Building Committee to.from',
                     'E5.7 - Other', 'E5.8 - Office of General Counsel', 'E6 - Reports (soils, structural, calcs)',
                     'E7 - Value Engineering', 'E7.1 - Value Engineering Correspondence',
                     'E7.2 - VE Workshop Minutes, Summaries, Final Reports', 'E8 - Program and Design Meeting Minutes',
                     'E9 - Sustainability Measures', 'F1 - Bid and Contract Award Correspondence',
                     'F1.1 - Executive Architect Correspondences', 'F1.2 - Special Consultants Correspondences',
                     'F1.4 - PPC and PP', 'F1.5 - Office of the President Correspondences',
                     'F1.6 - General Counsel Correspondences', 'F1.7 - Pre-Qualification', 'F1.8 - Other',
                     'F10 - Escrow Agreement', 'F2 - Reviews', 'F2.1 - Constructibility, Code Reviews',
                     'F2.2 - In-house. PP reviews', 'F3 - Structural, Title 24, Mech Calculations',
                     'F4 - Plan Deposits, Planholders, Ads for Bid', 'F2.3 - Independent Cost Review',
                     'F2.4 - Independent Seismic Review', 'F2.5 - Other', 'F5 - Drawings and Spec',
                     'F6 - Affirmative Action', 'F7 - Bid Summary Forms', 'F7.1 - Bid Protest', 'F8 - Contract',
                     'F9 - Builders Risk Insurance', 'G1 - Construction Correspondence',
                     'G1.1 - Contractor Correspondences', 'G1.2 - Executive Architect Correspondences',
                     'G1.3 - Users.Building Committee.Campus Correspondences', 'G1.4 - PPC and PP. Certified Payroll',
                     'G1.5 - Geotechnical Engineer Correspondences',
                     'G1.6 - Testing and Inspection to Laboratory Correspondences',
                     'G1.7 - General Counsel Correspondences', 'G1.8 - Other',
                     'G10 - Testing and Inspection Reports.Other',
                     'G11 - Proposal Requests. Bulletins. Contractors Response', 'G12 - Request for Information RFI',
                     'G13 - Letter of Instruction LOI', 'G14 - User Request Change in Scope', 'G15 - Change Order',
                     'G16 - Field Orders', 'G17 - Warranties and Guarantees', 'G18 - Punchlist',
                     'G19 - Notice of Completion', 'G2 - Certificate of Payment', 'G20 - Warranty Deficiency',
                     'G21 - Construction Photos', 'G22 - Claims. Public Records Act', 'G22.1 - Claims Confidential',
                     'G23 - Commissioning', 'G24 - Building Permits', "G3 - Contractor's Schedule and Updates",
                     'G4 - Progress Meeting Notes', 'G5 - UCSC Inspectors Daily Reports', 'G5.1 - Hot Work Permits',
                     'G6 - UCSC Memoranda', 'G6.1 - Architects Field Reports', 'G7 - Contractors Daily Reports',
                     'G8 - Testing and Inspection Reports. Geotechnical Engineer',
                     'G9 - Testing and Inspection Reports. Testing Laboratory']


def google_creds_from_creds_json(creds_path):
    with open(creds_path) as creds_json:
        creds_dict = json.load(creds_json)['web']
        client_id = creds_dict.get('client_id')
        client_secret = creds_dict.get('client_secret')

    return client_id, client_secret


def assemble_location(location):
    """
    This takes paths and modifies them to work on either windows or linux systems
    @param location:
    @return:
    """
    # TODO: need to test functionality more thoroughly
    is_network_path = lambda some_path: (os.path.exists(r"\\" + some_path), os.path.exists(r"//" + some_path))
    bck_slsh, frwd_slsh = is_network_path(location)

    # if network location, process as such, including
    if frwd_slsh:
        location = r"//" + location
        return location

    if bck_slsh:
        location = r"\\" + location
        return location


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

    config_dict['ARCHIVES_LOCATION'] = assemble_location(location=config_dict['ARCHIVES_LOCATION'])
    config_dict["ARCHIVIST_INBOX_LOCATION"] = assemble_location(location=config_dict["ARCHIVIST_INBOX_LOCATION"])
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


