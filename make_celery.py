import os
from archives_application import create_app

os.environ['PYTHONPATH'] = r'C:\Users\adankert\Google Drive\GitHub\archives_app'
flask_app = create_app()
celery_app = flask_app.extensions["celery"]