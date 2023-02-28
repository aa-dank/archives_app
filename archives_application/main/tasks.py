import flask
import json
import os
import subprocess
import shutil
import sys
from celery import shared_task
from celery.result import AsyncResult


@shared_task(ignore_result=False)
def test_task(a: int, b: int) -> int:
    celery = flask.current_app.extensions["celery"]
    result_str = f"Backend url: {celery.conf.broker_url}, a*b: {a*b}"
    print(result_str)
    return result_str