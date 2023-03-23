import os
from archives_application import create_app

os.environ['PYTHONPATH'] = r'C:\Users\adankert\Google Drive\GitHub\archives_app'
flask_app, celery_app = create_app()
flask_app.app_context().push()