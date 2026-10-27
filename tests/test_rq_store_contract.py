"""Opt-in worker integration against an explicitly selected key-value store."""

import operator
import os
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from rq import Queue, Retry, Worker
from rq.results import Result

from archives_application.key_value_store import rq_connection_from_url


def test_rq_forked_worker_results_dependencies_and_retry():
    """Exercise real queue operations, forked jobs, result streams, and failure retries."""
    url = os.environ.get("ARCHIVES_RQ_TEST_URL")
    socket = os.environ.get("ARCHIVES_RQ_TEST_SOCKET")
    if not url and not socket:
        pytest.skip("Select a disposable key-value service with ARCHIVES_RQ_TEST_URL or ARCHIVES_RQ_TEST_SOCKET")
    connection = rq_connection_from_url(url or f"unix://{socket}")
    queue = Queue(f"archives-rq-contract-{uuid4().hex}", connection=connection)
    jobs = []
    worker = None
    try:
        first = queue.enqueue(operator.add, 20, 22)
        jobs.append(first)
        dependent = queue.enqueue(operator.mul, 6, 7, depends_on=first)
        jobs.append(dependent)
        failing = queue.enqueue(operator.truediv, 1, 0, retry=Retry(max=1, interval=0))
        jobs.append(failing)
        worker = Worker([queue], connection=connection)
        worker.work(burst=True, with_scheduler=False)
        for job in jobs:
            job.refresh()
        assert first.is_finished and first.return_value() == 42
        assert dependent.is_finished and dependent.return_value() == 42
        assert failing.is_failed and failing.retries_left == 0
        assert "ZeroDivisionError" in failing.exc_info
        assert queue.count == 0
        assert queue.finished_job_registry.count == 2
        assert queue.failed_job_registry.count == 1
        assert queue.deferred_job_registry.count == 0
    finally:
        for job in jobs:
            Result.delete_all(job)
            job.delete()
        queue.delete(delete_jobs=True)
        if worker is not None:
            connection.delete(worker.key)
        connection.close()


def test_idle_worker_stays_connected_and_executes_a_late_job():
    """Wait beyond redis-py 8's five-second default, then process a forked job."""
    url = os.environ.get("ARCHIVES_RQ_TEST_URL")
    socket = os.environ.get("ARCHIVES_RQ_TEST_SOCKET")
    if not url and not socket:
        pytest.skip("Select a disposable key-value service with ARCHIVES_RQ_TEST_URL or ARCHIVES_RQ_TEST_SOCKET")
    connection = rq_connection_from_url(url or f"unix://{socket}")
    queue = Queue(f"archives-rq-contract-{uuid4().hex}", connection=connection)
    worker_name = f"archives-contract-{uuid4().hex}"
    worker_key = f"rq:worker:{worker_name}"
    environment = dict(os.environ, ARCHIVES_CONTRACT_QUEUE=queue.name, ARCHIVES_CONTRACT_WORKER=worker_name)
    process = subprocess.Popen([
        sys.executable, "-c",
        "import os; from rq import Worker, Queue; "
        "from archives_application.key_value_store import rq_connection_from_url; "
        "url = os.environ.get('ARCHIVES_RQ_TEST_URL') or 'unix://' + os.environ['ARCHIVES_RQ_TEST_SOCKET']; "
        "conn = rq_connection_from_url(url); "
        "conn.connection_pool.connection_kwargs['client_name'] = os.environ['ARCHIVES_CONTRACT_WORKER']; "
        "worker = Worker([Queue(os.environ['ARCHIVES_CONTRACT_QUEUE'], connection=conn)], "
        "connection=conn, name=os.environ['ARCHIVES_CONTRACT_WORKER'], worker_ttl=30); "
        "worker.work(max_jobs=1, with_scheduler=False, logging_level='ERROR')",
    ], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    job = None
    try:
        deadline = time.monotonic() + 15
        blocked = None
        while time.monotonic() < deadline:
            assert process.poll() is None, "Worker exited before blocking on its queue"
            blocked = next((client for client in connection.client_list()
                            if client["name"] == worker_name and "b" in client["flags"]), None)
            if blocked:
                break
            time.sleep(0.05)
        assert blocked is not None, "Worker never blocked on its queue"
        time.sleep(6)
        assert process.poll() is None, "Idle worker exited during blocking dequeue"
        assert any(client["id"] == blocked["id"] and "b" in client["flags"]
                   for client in connection.client_list()), "Blocking connection was lost or recycled"
        job = queue.enqueue(operator.add, 20, 22)
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stdout + stderr
        job.refresh()
        assert job.is_finished and job.return_value() == 42
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        if job is not None:
            Result.delete_all(job)
            job.delete()
        queue.delete(delete_jobs=True)
        connection.srem("rq:workers", worker_key)
        connection.srem(f"rq:workers:{queue.name}", worker_key)
        connection.delete(worker_key)
        connection.close()
