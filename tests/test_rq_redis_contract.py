"""Opt-in worker integration against an explicitly selected Redis instance."""

import operator
import os
from uuid import uuid4

import pytest
import redis
from rq import Queue, Retry, Worker


def test_rq_forked_worker_results_dependencies_and_retry():
    """Exercise real queue operations, forked jobs, result streams, and failure retries."""
    url = os.environ.get("ARCHIVES_RQ_TEST_URL")
    socket = os.environ.get("ARCHIVES_RQ_TEST_SOCKET")
    if not url and not socket:
        pytest.skip("Select a disposable Redis service with ARCHIVES_RQ_TEST_URL or ARCHIVES_RQ_TEST_SOCKET")
    connection = redis.Redis.from_url(url) if url else redis.Redis(unix_socket_path=socket)
    queue = Queue(f"archives-rq-contract-{uuid4().hex}", connection=connection)
    jobs = []
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
            job.delete()
        queue.delete(delete_jobs=True)
        connection.close()
