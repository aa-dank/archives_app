"""Verify durable scrape checkpoints, replay, and cooperative deadlines."""

import hashlib
import json
import multiprocessing
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from rq.timeouts import JobTimeoutException

from archives_application import create_app, db, utils
from archives_application.archiver import archiver_tasks as tasks, routes
from archives_application.models import FileLocationModel, FileModel, UserModel, WorkerTaskModel


class TestConfig:
    SECRET_KEY = "scrape-progress-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "scrape-progress-test-client"
    REDIS_URL = "redis://localhost:6379/0"
    USER_ARCHIVES_LOCATION = r"N:\Archives"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """Use a disposable on-disk database so a killed child cannot fake durability."""
    archive = tmp_path / "archive"
    archive.mkdir()
    class DiskConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'tasks.sqlite'}"
    app = create_app(DiskConfig)
    app.config["ARCHIVES_LOCATION"] = str(archive)
    monkeypatch.setattr(tasks, "app", app)
    tables = [WorkerTaskModel.__table__, FileModel.__table__, FileLocationModel.__table__, UserModel.__table__]
    with app.app_context():
        for table in tables:
            table.create(db.engine)
        yield app
        db.session.remove()
        for table in reversed(tables):
            table.drop(db.engine)


def write_file(app, relative, content=b"contents"):
    path = os.path.join(app.config["ARCHIVES_LOCATION"], relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(content)
    return path


def record_task(task_id, **overrides):
    values = dict(task_id=task_id, time_enqueued=datetime.now(), origin="default",
                  function_name="scrape_file_data_task", status="queued")
    values.update(overrides)
    task = WorkerTaskModel(**values)
    db.session.add(task)
    db.session.commit()
    return task


def scrape(app, task_id="scrape", start=None, seconds=60, exclusions=()):
    record_task(task_id)
    return tasks.scrape_file_data_task(
        archives_location=app.config["ARCHIVES_LOCATION"],
        start_location=start or app.config["ARCHIVES_LOCATION"],
        file_server_root_index=0, exclusion_functions=list(exclusions),
        scrape_time=timedelta(seconds=seconds), queue_id=task_id,
    )


def test_full_pass_wraps_and_replay_preserves_exclusions(app):
    write_file(app, "A/a.pdf", b"a")
    start = os.path.dirname(write_file(app, "B/b.pdf", b"b"))
    write_file(app, "root.pdf", b"root")
    write_file(app, "A/empty.pdf", b"")
    write_file(app, "A/Thumbs.db", b"excluded name")
    write_file(app, "A/skip.tmp", b"excluded extension")
    exclusions = [routes.exclude_filenames, lambda path: routes.exclude_extensions(path, [".tmp"])]
    result = scrape(app, start=start, exclusions=exclusions)
    assert result["Stop Reason"] == "scan_complete"
    assert result["Next Start Location"] == "."
    assert result["Files Added"] == result["File Locations Added"] == 3
    result = scrape(app, "replay", start=start, exclusions=exclusions)
    assert result["Files Added"] == result["File Locations Added"] == 0
    assert result["Files Confirmed"] == 3
    assert db.session.query(FileLocationModel).count() == 3
    assert db.session.query(FileModel).count() == 3


@pytest.mark.parametrize("known_content", [False, True])
def test_changed_content_reconciles_existing_path(app, known_content):
    path = write_file(app, "A/report.pdf", b"original")
    if known_content:
        write_file(app, "A/copy.pdf", b"replacement")
    scrape(app)
    with open(path, "wb") as stream:
        stream.write(b"replacement")
    result = scrape(app, "changed")
    location = db.session.query(FileLocationModel).filter_by(filename="report.pdf").one()
    assert location.file.hash == hashlib.sha1(b"replacement").hexdigest()
    assert result["File Locations Updated"] == 1
    assert result["File Locations Added"] == 0


def test_deadline_within_directory_leaves_partial_file_for_replay(app, monkeypatch):
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        write_file(app, f"A/{name}", name.encode())
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original = utils.FilesUtils.get_hash
    attempts = []

    def slow_hash(filepath, check_progress=None):
        attempts.append(filepath)
        digest = original(filepath)
        clock.now += 6
        check_progress()
        return digest

    monkeypatch.setattr(utils.FilesUtils, "get_hash", slow_hash)
    result = scrape(app, seconds=10)
    assert result["Stop Reason"] == "time_limit"
    assert result["Next Start Location"] == "A"
    assert result["Current File"] == os.path.join("A", "b.pdf")
    assert result["Last Completed File"] == os.path.join("A", "a.pdf")
    assert len(attempts) == 2
    assert db.session.query(FileModel).count() == db.session.query(FileLocationModel).count() == 1
    monkeypatch.setattr(utils.FilesUtils, "get_hash", original)
    clock.now = 0
    start = routes.retrieve_location_to_start_scraping()
    scrape(app, "resumed", start=start)
    assert db.session.query(FileLocationModel).count() == 3


def test_hash_checks_between_reads_and_does_not_return_partial_digest(tmp_path):
    path = tmp_path / "large"
    path.write_bytes(b"x" * (2 * 1024 * 1024))
    checks = []

    def deadline():
        checks.append(True)
        if len(checks) == 2:
            raise tasks.ScrapeTimeLimitReached()

    with pytest.raises(tasks.ScrapeTimeLimitReached):
        utils.FilesUtils.get_hash(path, check_progress=deadline)
    assert utils.FilesUtils.get_hash(path, check_progress=lambda: None) == utils.FilesUtils.get_hash(path)


def test_long_hash_persists_heartbeat_before_returning(app, monkeypatch):
    write_file(app, "A/a.pdf")
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original = utils.FilesUtils.get_hash

    def slow_hash(filepath, check_progress=None):
        clock.now = 31
        check_progress()
        with db.engine.connect() as connection:
            row = connection.execute(text(
                "SELECT status_details, time_started, time_updated FROM worker_tasks WHERE task_id='scrape'"
            )).one()
        assert json.loads(row[0])["Time Elapsed"] == "31.000s"
        assert row[1] is not None and row[2] is not None
        return original(filepath)

    monkeypatch.setattr(utils.FilesUtils, "get_hash", slow_hash)
    assert scrape(app)["Stop Reason"] == "scan_complete"


def test_rq_timeout_is_reraised_with_checkpoint_preserved(app, monkeypatch):
    write_file(app, "A/a.pdf")

    def timeout(**kwargs):
        raise JobTimeoutException("injected timeout")

    monkeypatch.setattr(utils.FilesUtils, "get_hash", timeout)
    with pytest.raises(JobTimeoutException):
        scrape(app)
    task = db.session.query(WorkerTaskModel).one()
    assert task.status == "failed"
    assert task.time_completed == task.time_updated
    assert task.time_started is not None
    assert task.status_details["Next Start Location"] == "A"
    assert task.status_details["Current File"] == os.path.join("A", "a.pdf")
    assert db.session.query(FileModel).count() == 0


def test_failed_location_insert_rolls_back_new_content(app):
    write_file(app, "A/a.pdf")

    def fail_insert(*args):
        raise RuntimeError("injected location write failure")

    event.listen(FileLocationModel, "before_insert", fail_insert)
    try:
        result = scrape(app)
    finally:
        event.remove(FileLocationModel, "before_insert", fail_insert)
    assert len(result["Errors"]) == 1
    assert db.session.query(FileModel).count() == 0
    assert db.session.query(FileLocationModel).count() == 0


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires a forked worker")
def test_killed_worker_keeps_checkpoint_and_resumes_without_skipping(app, monkeypatch):
    write_file(app, "A/earlier.pdf", b"earlier")
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        write_file(app, f"B/{name}", name.encode())
    record_task("killed")
    db.session.remove()
    context = multiprocessing.get_context("fork")
    hashing = context.Event()
    hold = context.Event()
    original = utils.FilesUtils.get_hash

    def blocked_hash(filepath, check_progress=None):
        if filepath.endswith("b.pdf"):
            hashing.set()
            hold.wait(20)
            raise RuntimeError("child was not terminated")
        return original(filepath, check_progress=check_progress)

    monkeypatch.setattr(utils.FilesUtils, "get_hash", blocked_hash)

    def run_child():
        with app.app_context():
            db.engine.dispose(close=False)
        tasks.scrape_file_data_task(app.config["ARCHIVES_LOCATION"], app.config["ARCHIVES_LOCATION"],
                                    0, [], timedelta(minutes=1), "killed")

    process = context.Process(target=run_child)
    process.start()
    try:
        assert hashing.wait(10), "worker did not reach the expected file"
        process.kill()
        process.join(5)
        assert process.exitcode is not None and process.exitcode < 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    task = db.session.query(WorkerTaskModel).filter_by(task_id="killed").one()
    assert task.status == "started" and task.time_completed is None
    assert task.time_started is not None and task.time_updated is not None
    assert task.status_details["Next Start Location"] == "B"
    assert task.status_details["Current File"] == os.path.join("B", "b.pdf")
    assert task.status_details["Last Completed File"] == os.path.join("B", "a.pdf")
    assert db.session.query(FileLocationModel).count() == 2
    monkeypatch.setattr(utils.FilesUtils, "get_hash", original)
    scrape(app, "resumed", start=routes.retrieve_location_to_start_scraping())
    assert db.session.query(FileLocationModel).count() == 4
    assert db.session.query(FileModel).count() == 4


def test_resume_uses_newest_scrape_checkpoint_including_interruption(app):
    root = app.config["ARCHIVES_LOCATION"]
    os.makedirs(os.path.join(root, "old"))
    os.makedirs(os.path.join(root, "new"))
    record_task("old", time_enqueued=datetime(2026, 10, 20), status="finished",
                time_completed=datetime(2026, 10, 20, 4), time_updated=datetime(2030, 1, 1),
                status_details={"Next Start Location": "old"})
    record_task("new", time_enqueued=datetime(2026, 10, 21), status="started",
                status_details={"Next Start Location": "new"})
    for task_id, values in [
        ("wrong-function", {"function_name": "other", "status_details": {"Next Start Location": "old"}}),
        ("test", {"origin": "test", "status_details": {"Next Start Location": "old"}}),
        ("empty", {"status_details": {}}),
        ("malformed", {"status_details": ["Next Start Location"]}),
        ("outside", {"status_details": {"Next Start Location": "../outside"}}),
    ]:
        record_task(task_id, **values)
    assert routes.retrieve_location_to_start_scraping() == os.path.join(root, "new")


def test_missing_checkpoint_directory_falls_back_to_parent(app):
    root = app.config["ARCHIVES_LOCATION"]
    os.makedirs(os.path.join(root, "A"))
    record_task("missing", status_details={"Next Start Location": "A/deleted"})
    assert routes.retrieve_location_to_start_scraping() == os.path.join(root, "A")
    result = scrape(app, start=os.path.join(root, "A", "deleted"))
    assert result["Resume Fallback"] == os.path.join(root, "A")


def test_deadline_while_seeking_does_not_replace_checkpoint_with_earlier_directory(app, monkeypatch):
    write_file(app, "A/a.pdf")
    start = os.path.dirname(write_file(app, "B/b.pdf"))
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_walk = os.walk

    def slow_walk(*args, **kwargs):
        for root, directories, files in original_walk(*args, **kwargs):
            if root.endswith(os.sep + "A"):
                clock.now = 11
            yield root, directories, files

    monkeypatch.setattr(tasks.os, "walk", slow_walk)
    result = scrape(app, start=start, seconds=10)
    assert result["Next Start Location"] == "B"
    assert result["Stop Reason"] == "time_limit"
    assert db.session.query(FileLocationModel).count() == 0


def test_directory_enumeration_error_fails_instead_of_skipping_subtree(app, monkeypatch):
    write_file(app, "A/a.pdf")
    original_walk = os.walk

    def broken_walk(*args, **kwargs):
        for root, directories, files in original_walk(*args, **kwargs):
            yield root, directories, files
            if root.endswith(os.sep + "A"):
                kwargs["onerror"](PermissionError("injected enumeration failure"))

    monkeypatch.setattr(tasks.os, "walk", broken_walk)
    with pytest.raises(PermissionError):
        scrape(app)
    task = db.session.query(WorkerTaskModel).one()
    assert task.status == "failed"
    assert task.status_details["Next Start Location"] == "A"
    assert db.session.query(FileLocationModel).count() == 1


def test_task_lifecycle_populates_timestamps_and_allows_empty_details(app, monkeypatch):
    calls = {}

    def enqueue_call(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(origin="default")

    monkeypatch.setattr(app, "q", SimpleNamespace(enqueue_call=enqueue_call))
    result = utils.RQTaskUtils.enqueue_new_task(db, tasks.scrape_file_data_task, task_info={"initial": True})
    task = db.session.query(WorkerTaskModel).filter_by(task_id=result["task_id"]).one()
    assert task.time_updated is not None and task.time_started is None
    assert task.time_completed is None and task.status_details == {"initial": True}
    utils.RQTaskUtils.initiate_task_subroutine(task.task_id, db)
    db.session.refresh(task)
    started = task.time_started
    assert started == task.time_updated and started is not None
    utils.RQTaskUtils.update_task_subroutine(db, task.task_id, status_details={"progress": 1})
    utils.RQTaskUtils.update_task_subroutine(db, task.task_id, status_details={})
    db.session.refresh(task)
    assert task.status_details == {} and task.time_completed is None
    utils.RQTaskUtils.complete_task_subroutine(task.task_id, db, {"done": True})
    db.session.refresh(task)
    assert task.time_started == started
    assert task.time_updated == task.time_completed


@pytest.mark.parametrize("minutes,expected", [(180, 11100), (1500, 90300), (0, None), (-1, None)])
def test_route_preserves_explicit_start_and_uses_full_duration(app, monkeypatch, minutes, expected):
    user = UserModel(email="admin@example.org", roles="ADMIN")
    db.session.add(user)
    db.session.commit()
    record_task("previous", status_details={"Next Start Location": "."})
    write_file(app, "explicit/a.pdf")
    calls = []

    def enqueue(**kwargs):
        calls.append(kwargs)
        return {"task_id": "enqueued"}

    monkeypatch.setattr(utils.RQTaskUtils, "enqueue_new_task", enqueue)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        response = client.get("/scrape_files", query_string={
            "start_path": r"N:\Archives\explicit", "scrape_time": minutes,
        })
    if expected is None:
        assert response.status_code == 400 and calls == []
    else:
        assert response.status_code == 200
        assert calls[0]["timeout"] == expected
        assert calls[0]["task_kwargs"]["start_location"] == os.path.join(app.config["ARCHIVES_LOCATION"], "explicit")
