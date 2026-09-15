"""Application bootstrap helpers for background tasks.

Task modules must remain importable without requiring deployment-only
configuration.  The worker initializes the task app before starting RQ, while
direct or synchronous task calls lazily create an app or reuse the active
Flask application context.
"""

from contextlib import contextmanager
from threading import Lock
from typing import Iterator, Optional

import flask

from archives_application import create_app


_task_app: Optional[flask.Flask] = None
_task_app_lock = Lock()


def initialize_task_app(app: Optional[flask.Flask] = None) -> flask.Flask:
    """Initialize and return the application used by background tasks."""
    global _task_app

    if app is not None:
        _task_app = app
        return app

    if _task_app is None:
        with _task_app_lock:
            if _task_app is None:
                _task_app = create_app()

    return _task_app


def get_task_app() -> flask.Flask:
    """Return the active app, the worker app, or a lazily-created task app."""
    if flask.has_app_context():
        return flask.current_app._get_current_object()
    return initialize_task_app()


@contextmanager
def task_app_context() -> Iterator[flask.Flask]:
    """Run a task inside the application context used by the worker."""
    app = get_task_app()
    with app.app_context():
        yield app
