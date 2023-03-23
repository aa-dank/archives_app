import flask
import json
import os
import subprocess
import shutil
import sys
from celery import shared_task
from celery.result import AsyncResult


@shared_task(bind =True, ignore_result=False)
def test_task(a: int, b: int) -> str:
    celery = flask.current_app.extensions["celery"]
    #celery.worker_main()
    result_str = f"Backend url: {celery.conf.broker_url}, a*b: {a*b}"
    print(result_str)
    return result_str


