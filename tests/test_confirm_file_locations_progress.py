"""Verify confirmation progress survives interruptions at transaction boundaries."""

import hashlib
import json
import multiprocessing
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from rq.timeouts import JobTimeoutException
from sqlalchemy import event, text

from archives_application import create_app, db, utils
from archives_application.archiver import archiver_tasks as tasks
from archives_application.models import ArchivedFileModel, FileLocationModel, FileModel, UserModel, WorkerTaskModel


class TestConfig:
    SECRET_KEY = "confirmation-progress-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "confirmation-progress-test-client"
    REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    class DiskConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'confirmation.sqlite'}"

    archive = tmp_path / "archive"
    archive.mkdir()
    app = create_app(DiskConfig)
    app.config["ARCHIVES_LOCATION"] = str(archive)
    monkeypatch.setattr(tasks, "app", app)
    tables = [WorkerTaskModel.__table__, UserModel.__table__, FileModel.__table__,
              FileLocationModel.__table__, ArchivedFileModel.__table__]
    with app.app_context():
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        for table in tables:
            table.create(db.engine)
        yield app
        db.session.remove()
        for table in reversed(tables):
            table.drop(db.engine)


def add_location(app, filename, *, present=False, confirmed=datetime(2020, 1, 1), file=None):
    if file is None:
        file = FileModel(hash=hashlib.sha1(filename.encode()).hexdigest(), size=1, extension="pdf")
        db.session.add(file)
        db.session.flush()
    location = FileLocationModel(file_id=file.id, file_server_directories="A", filename=filename,
                                 existence_confirmed=confirmed)
    db.session.add(location)
    db.session.commit()
    if present:
        path = Path(app.config["ARCHIVES_LOCATION"]) / "A" / filename
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"contents")
    return location


def record_task(task_id="confirm"):
    db.session.add(WorkerTaskModel(task_id=task_id, time_enqueued=datetime.now(), origin="default",
                                   function_name="confirm_file_locations_task", status="queued"))
    db.session.commit()


def confirm(app, seconds=60, task_id="confirm"):
    record_task(task_id)
    return tasks.confirm_file_locations_task(app.config["ARCHIVES_LOCATION"],
                                             timedelta(seconds=seconds), task_id)


def add_archival_event(file_id):
    user = UserModel(email="archivist@example.org", roles="ADMIN")
    db.session.add(user)
    db.session.flush()
    record = ArchivedFileModel(file_id=file_id, destination_path="A/original.pdf", file_size=1,
                               date_archived=datetime(2020, 1, 1), archivist_id=user.id)
    db.session.add(record)
    db.session.commit()
    return record


def test_empty_table_stops_and_populates_task_timestamps(app):
    result = confirm(app)
    task = db.session.query(WorkerTaskModel).one()
    assert result["Stop Reason"] == "no_locations"
    assert result["Locations Checked"] == 0
    assert task.status == "finished"
    assert task.time_started is not None
    assert task.time_updated == task.time_completed
    assert task.status_details["Current File"] is None


def test_active_path_and_committed_counters_are_visible_before_next_check(app, monkeypatch):
    first = add_location(app, "a.pdf", present=True)
    second = add_location(app, "b.pdf", present=True, confirmed=datetime(2020, 1, 2))
    first_id, second_id = first.id, second.id
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_exists = os.path.exists
    observed = []

    def check(path):
        if str(path).startswith(app.config["ARCHIVES_LOCATION"] + os.sep):
            with db.engine.connect() as connection:
                row = connection.execute(text(
                    "SELECT status, status_details, time_updated, time_completed FROM worker_tasks"
                )).one()
                metadata = connection.execute(text(
                    "SELECT existence_confirmed FROM file_locations WHERE id=:id"
                ), {"id": first_id}).scalar_one()
            details = json.loads(row[1])
            assert row[0] == "started" and row[2] is not None and row[3] is None
            observed.append(details)
            if path.endswith("b.pdf"):
                assert details["Locations Checked"] == details["Files Confirmed"] == 1
                assert details["Last Completed Location ID"] == first_id
                assert metadata != "2020-01-01 00:00:00.000000"
                clock.now = 10
        return original_exists(path)

    monkeypatch.setattr(tasks.os.path, "exists", check)
    result = confirm(app, seconds=10)
    assert [snapshot["Current Location ID"] for snapshot in observed] == [first_id, second_id]
    assert result["Stop Reason"] == "time_limit"
    assert result["Locations Checked"] == 1
    db.session.refresh(second)
    assert second.existence_confirmed == datetime(2020, 1, 2)


def test_slow_check_refreshes_counters_after_commit(app, monkeypatch):
    add_location(app, "a.pdf", present=True)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_exists = os.path.exists
    original_update = utils.RQTaskUtils.update_task_subroutine
    heartbeat = []

    def check(path):
        if str(path).startswith(app.config["ARCHIVES_LOCATION"] + os.sep):
            clock.now = 31
        return original_exists(path)

    def update(*args, **kwargs):
        original_update(*args, **kwargs)
        if kwargs["status_details"]["Locations Checked"]:
            with db.engine.connect() as connection:
                snapshot = connection.execute(text("SELECT status_details FROM worker_tasks")).scalar_one()
            heartbeat.append(json.loads(snapshot))
            clock.now = 60

    monkeypatch.setattr(tasks.os.path, "exists", check)
    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    result = confirm(app)
    assert len(heartbeat) == 1
    assert heartbeat[0]["Time Elapsed"] == "31.000s"
    assert heartbeat[0]["Files Confirmed"] == 1
    assert result["Stop Reason"] == "time_limit"


def test_missing_locations_remove_last_file_and_unlink_history_atomically(app):
    first = add_location(app, "missing-a.pdf")
    file_id = first.file_id
    add_location(app, "missing-b.pdf", file=db.session.get(FileModel, file_id))
    history = add_archival_event(file_id)
    result = confirm(app)
    assert result["Locations Checked"] == result["Locations Missing"] == 2
    assert result["Files Removed"] == 1
    assert result["Stop Reason"] == "no_locations"
    assert db.session.query(FileLocationModel).count() == db.session.query(FileModel).count() == 0
    db.session.refresh(history)
    assert history.file_id is None


def test_missing_copy_preserves_content_with_another_location(app, monkeypatch):
    missing = add_location(app, "missing.pdf")
    file_id = missing.file_id
    remaining = add_location(app, "present.pdf", present=True, confirmed=datetime(2021, 1, 1),
                              file=db.session.get(FileModel, file_id))
    history = add_archival_event(file_id)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_exists = os.path.exists

    def check(path):
        if path.endswith("present.pdf"):
            clock.now = 10
        return original_exists(path)

    monkeypatch.setattr(tasks.os.path, "exists", check)
    result = confirm(app, seconds=10)
    assert result["Locations Missing"] == 1 and result["Files Removed"] == 0
    assert db.session.query(FileLocationModel).one().id == remaining.id
    assert db.session.get(FileModel, file_id) is not None
    db.session.refresh(history)
    assert history.file_id == file_id


def test_cleanup_failure_rolls_back_location_history_and_counters(app, monkeypatch):
    location = add_location(app, "missing.pdf")
    location_id, file_id = location.id, location.file_id
    history = add_archival_event(file_id)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))

    def fail_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM files "):
            clock.now = 10
            raise RuntimeError("injected final cleanup failure")

    event.listen(db.engine, "before_cursor_execute", fail_delete)
    try:
        result = confirm(app, seconds=10)
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_delete)
    assert len(result["Errors"]) == 1
    assert result["Locations Checked"] == result["Locations Missing"] == result["Files Removed"] == 0
    assert db.session.get(FileLocationModel, location_id) is not None
    assert db.session.get(FileModel, file_id) is not None
    db.session.refresh(history)
    assert history.file_id == file_id


def test_rq_timeout_is_not_swallowed_and_leaves_location_eligible(app, monkeypatch):
    location = add_location(app, "a.pdf")
    location_id = location.id
    original_exists = os.path.exists

    def check(path):
        if path.endswith("a.pdf"):
            raise JobTimeoutException("injected timeout")
        return original_exists(path)

    monkeypatch.setattr(tasks.os.path, "exists", check)
    with pytest.raises(JobTimeoutException):
        confirm(app)
    task = db.session.query(WorkerTaskModel).one()
    assert task.status == "failed" and task.time_completed == task.time_updated
    assert task.status_details["Current Location ID"] == location_id
    assert task.status_details["Locations Checked"] == 0
    db.session.refresh(location)
    assert location.existence_confirmed == datetime(2020, 1, 1)


def test_progress_save_failure_stops_before_next_filesystem_check(app, monkeypatch):
    add_location(app, "a.pdf", present=True)
    add_location(app, "b.pdf", present=True, confirmed=datetime(2020, 1, 2))
    original_update = utils.RQTaskUtils.update_task_subroutine
    snapshots = []

    def update(*args, **kwargs):
        snapshots.append(dict(kwargs["status_details"]))
        if len(snapshots) == 2:
            raise RuntimeError("injected progress save failure")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    with pytest.raises(RuntimeError, match="progress save failure"):
        confirm(app)
    task = db.session.query(WorkerTaskModel).one()
    assert task.status == "failed"
    assert task.status_details["Files Confirmed"] == 1
    assert task.status_details["Last Completed File"] == os.path.join("A", "a.pdf")
    assert task.status_details["Current File"] == os.path.join("A", "b.pdf")


@pytest.mark.parametrize("confirmed", [None, datetime(2010, 1, 1)])
def test_oldest_and_never_confirmed_locations_are_selected_first(app, monkeypatch, confirmed):
    add_location(app, "new.pdf", present=True, confirmed=datetime(2020, 1, 1))
    oldest = add_location(app, "old.pdf", present=True, confirmed=confirmed)
    oldest_id = oldest.id
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original = tasks._confirm_file_location

    def confirm_once(*args):
        result = original(*args)
        clock.now = 10
        return result

    monkeypatch.setattr(tasks, "_confirm_file_location", confirm_once)
    result = confirm(app, seconds=10)
    assert result["Last Completed Location ID"] == oldest_id
    assert result["Locations Checked"] == 1


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires a forked worker")
def test_killed_worker_keeps_progress_and_next_run_selects_unfinished_location(app, monkeypatch):
    first = add_location(app, "a.pdf", present=True)
    second = add_location(app, "b.pdf", present=True, confirmed=datetime(2020, 1, 2))
    first_id, second_id = first.id, second.id
    record_task("killed")
    db.session.remove()
    context = multiprocessing.get_context("fork")
    checking = context.Event()
    hold = context.Event()
    original_exists = os.path.exists

    def blocked_check(path):
        if path.endswith("b.pdf"):
            checking.set()
            hold.wait(20)
            raise RuntimeError("child was not terminated")
        return original_exists(path)

    monkeypatch.setattr(tasks.os.path, "exists", blocked_check)

    def run_child():
        with app.app_context():
            db.engine.dispose(close=False)
        tasks.confirm_file_locations_task(app.config["ARCHIVES_LOCATION"], timedelta(minutes=1), "killed")

    process = context.Process(target=run_child)
    process.start()
    try:
        assert checking.wait(10), "worker did not reach the second location"
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
    assert task.status_details["Locations Checked"] == task.status_details["Files Confirmed"] == 1
    assert task.status_details["Current Location ID"] == second_id
    assert task.status_details["Last Completed Location ID"] == first_id
    monkeypatch.setattr(tasks.os.path, "exists", original_exists)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_confirm = tasks._confirm_file_location

    def confirm_once(*args):
        result = original_confirm(*args)
        clock.now = 10
        return result

    monkeypatch.setattr(tasks, "_confirm_file_location", confirm_once)
    result = confirm(app, seconds=10, task_id="resumed")
    assert result["Last Completed Location ID"] == second_id
    assert result["Files Confirmed"] == 1


@pytest.mark.parametrize("minutes,expected", [(10, 1200), (1500, 90600), (0, None), (-1, None)])
def test_confirmation_route_uses_full_positive_duration(app, monkeypatch, minutes, expected):
    user = UserModel(email="admin@example.org", roles="ADMIN")
    db.session.add(user)
    db.session.commit()
    calls = []

    def enqueue(**kwargs):
        calls.append(kwargs)
        return {"task_id": "enqueued"}

    monkeypatch.setattr(utils.RQTaskUtils, "enqueue_new_task", enqueue)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["_user_id"] = str(user.id)
            session["_fresh"] = True
        response = client.get("/confirm_file_locations", query_string={"confirming_time": minutes})
    if expected is None:
        assert response.status_code == 400 and calls == []
    else:
        assert response.status_code == 200
        assert calls[0]["timeout"] == expected
