from celery import current_app as current_celery_app
from celery import Task


def make_celery(app):
    class ContextTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return Task.__call__(self, *args, **kwargs)

    celery = current_celery_app
    celery.config_from_object(app.config, namespace="CELERY")
    celery.set_default()
    celery.Task = ContextTask
    return celery