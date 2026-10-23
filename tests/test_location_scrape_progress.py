"""Exercise scoped confirmation, parent progress, and queued child accounting."""

import hashlib
import json
import multiprocessing
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from rq.timeouts import JobTimeoutException
from sqlalchemy import event, text

from archives_application import create_app, db, utils
from archives_application.archiver import archiver_tasks as tasks
from archives_application.models import ArchivedFileModel, FileLocationModel, FileModel, UserModel, WorkerTaskModel


class TestConfig:
    SECRET_KEY = "location-scrape-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "location-scrape-test-client"
    REDIS_URL = "redis://localhost:6379/0"


class FakeQueue:
    """Keep real child task SQL writes without connecting to Redis."""

    def __init__(self):
        self.calls = []
        self.before_enqueue = None

    def enqueue_call(self, **kwargs):
        if self.before_enqueue:
            self.before_enqueue(kwargs)
        self.calls.append(kwargs)
        return SimpleNamespace(origin="default")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    class DiskConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'location.sqlite'}"

    archive = tmp_path / "archive"
    (archive / "Project1").mkdir(parents=True)
    app = create_app(DiskConfig)
    app.config["ARCHIVES_LOCATION"] = str(archive)
    app.q = FakeQueue()
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


def write_file(app, relative, content=b"contents"):
    path = Path(app.config["ARCHIVES_LOCATION"]) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def add_location(app, directory, filename, present=False):
    file = FileModel(hash=hashlib.sha1(f"{directory}/{filename}".encode()).hexdigest(), size=1, extension="pdf")
    db.session.add(file)
    db.session.flush()
    location = FileLocationModel(file_id=file.id, file_server_directories=directory, filename=filename,
                                 existence_confirmed=datetime(2020, 1, 1))
    db.session.add(location)
    db.session.commit()
    if present:
        write_file(app, os.path.join(directory or "", filename))
    return location


def record_parent():
    db.session.add(WorkerTaskModel(task_id="parent", time_enqueued=datetime.now(), origin="default",
                                   function_name="scrape_location_files_task", status="queued"))
    db.session.commit()


def run_scrape(app, relative="Project1", **kwargs):
    record_parent()
    return tasks.scrape_location_files_task(os.path.join(app.config["ARCHIVES_LOCATION"], relative),
                                            "parent", **kwargs)


def read_parent():
    with db.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT status, status_details, time_started, time_updated, time_completed "
            "FROM worker_tasks WHERE task_id='parent'"
        )).one()
    return row[0], json.loads(row[1]), row[2:]


def test_phases_paths_and_child_jobs_are_durable_and_indexing_is_separate(app, monkeypatch):
    known = add_location(app, "Project1", "known.pdf", present=True)
    add_location(app, os.path.join("Project1", "sub"), "known.pdf", present=True)
    add_location(app, "Project1", "missing.pdf")
    write_file(app, "Project1/new.pdf")
    write_file(app, "Project1/sub/new.pdf")
    write_file(app, "Project1/Thumbs.db")
    write_file(app, "Project1/excluded.ini")
    original_exists = os.path.exists
    observed = []

    def check(path):
        if str(path).startswith(app.config["ARCHIVES_LOCATION"] + os.sep):
            status, details, times = read_parent()
            assert status == "started" and times[0] and times[1] and times[2] is None
            assert details["Phase"] == "confirming_locations"
            assert details["Current File"] == os.path.relpath(path, app.config["ARCHIVES_LOCATION"])
            observed.append(details)
        return original_exists(path)

    def before_enqueue(kwargs):
        status, details, _ = read_parent()
        assert status == "started" and details["Phase"] == "discovering_files"
        assert details["Current File"] == os.path.relpath(
            kwargs["kwargs"]["filepath"], app.config["ARCHIVES_LOCATION"])
        assert details["Files Enqueued to Add"] == len(app.q.calls)

    monkeypatch.setattr(tasks.os.path, "exists", check)
    app.q.before_enqueue = before_enqueue
    result = run_scrape(app)
    assert len(observed) == result["Locations Checked"] == 3
    assert result["Files Confirmed"] == result["Files Already Known"] == 2
    assert result["Locations Missing"] == result["Files Records Removed"] == 1
    assert result["Files Excluded"] == result["Files Enqueued to Add"] == 2
    assert result["Files Examined"] == 6
    assert result["Directories Scanned"] == 2
    assert result["Phase"] == "complete" and result["Stop Reason"] == "completed"
    assert result["Current File"] is None
    children = db.session.query(WorkerTaskModel).filter_by(function_name="add_file_to_db_task").all()
    assert set(result["Child Task IDs"]) == {child.task_id for child in children}
    assert all(child.status == "queued" for child in children)
    assert read_parent()[0] == "finished"
    assert db.session.query(FileLocationModel).count() == 2
    db.session.refresh(known)
    assert known.existence_confirmed != datetime(2020, 1, 1)


@pytest.mark.parametrize("directory,neighbor", [("Project1", "Project10"), ("Project%_", "ProjectXY")])
@pytest.mark.parametrize("recursive", [False, True])
def test_confirmation_and_discovery_share_exact_directory_scope(app, directory, neighbor, recursive):
    add_location(app, directory, "missing.pdf")
    nested = add_location(app, os.path.join(directory, "sub"), "missing.pdf")
    unrelated = add_location(app, neighbor, "missing.pdf")
    nested_id, unrelated_id = nested.id, unrelated.id
    write_file(app, f"{directory}/new.pdf")
    write_file(app, f"{directory}/sub/new.pdf")
    write_file(app, f"{neighbor}/new.pdf")
    result = run_scrape(app, directory, recursively=recursive)
    assert result["Locations Checked"] == result["Locations Missing"] == (2 if recursive else 1)
    assert result["Files Records Removed"] == (2 if recursive else 1)
    assert result["Files Enqueued to Add"] == (2 if recursive else 1)
    assert db.session.get(FileLocationModel, unrelated_id) is not None
    assert (db.session.get(FileLocationModel, nested_id) is None) == recursive
    assert all(os.path.commonpath([os.path.join(app.config["ARCHIVES_LOCATION"], directory),
                                  call["kwargs"]["filepath"]]) ==
               os.path.join(app.config["ARCHIVES_LOCATION"], directory) for call in app.q.calls)


@pytest.mark.parametrize("recursive", [False, True])
def test_archive_root_and_nullable_root_directories(app, recursive):
    add_location(app, "", "root.pdf", present=True)
    add_location(app, None, "legacy-root.pdf", present=True)
    child = add_location(app, "sub", "known.pdf", present=True)
    write_file(app, "new-empty.pdf", b"")
    write_file(app, "sub/new.pdf")
    result = run_scrape(app, "", recursively=recursive)
    assert result["Files Confirmed"] == result["Files Already Known"] == (3 if recursive else 2)
    assert result["Files Enqueued to Add"] == (2 if recursive else 1)
    db.session.refresh(child)
    assert (child.existence_confirmed != datetime(2020, 1, 1)) == recursive


def test_confirmation_can_be_disabled_without_skipping_known_files(app, monkeypatch):
    known = add_location(app, "Project1", "known.pdf", present=True)
    write_file(app, "Project1/new.pdf")

    def forbidden(*args):
        pytest.fail("confirmation must not run")

    monkeypatch.setattr(tasks, "_confirm_file_location", forbidden)
    result = run_scrape(app, confirm_data=False)
    assert result["Locations Checked"] == result["Files Already Known"] == 0
    assert result["Files Enqueued to Add"] == 2
    db.session.refresh(known)
    assert known.existence_confirmed == datetime(2020, 1, 1)


def test_slow_confirmation_saves_committed_counters_before_phase_change(app, monkeypatch):
    add_location(app, "Project1", "known.pdf", present=True)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(monotonic=lambda: clock.now))
    original_exists = os.path.exists
    original_update = utils.RQTaskUtils.update_task_subroutine
    saved = []

    def check(path):
        if path.endswith("known.pdf"):
            clock.now = 31
        return original_exists(path)

    def update(*args, **kwargs):
        original_update(*args, **kwargs)
        if kwargs["status_details"]["Phase"] == "confirming_locations" and clock.now == 31:
            saved.append(read_parent()[1])

    monkeypatch.setattr(tasks.os.path, "exists", check)
    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    result = run_scrape(app)
    assert len(saved) == 1
    assert saved[0]["Locations Checked"] == saved[0]["Files Confirmed"] == 1
    assert saved[0]["Time Elapsed"] == "31.000s"
    assert result["Phase"] == "complete"


def test_enqueue_failures_are_not_counted_as_successes(app):
    write_file(app, "Project1/a.pdf")
    write_file(app, "Project1/b.pdf")

    def fail_a(kwargs):
        if kwargs["kwargs"]["filepath"].endswith("a.pdf"):
            raise RuntimeError("injected enqueue failure")

    app.q.before_enqueue = fail_a
    result = run_scrape(app, confirm_data=False)
    assert result["Stop Reason"] == "completed_with_errors"
    assert result["Files Enqueued to Add"] == len(result["Child Task IDs"]) == 1
    assert result["Last Enqueued File"] == os.path.join("Project1", "b.pdf")
    assert result["Errors"][0]["Phase"] == "discovering_files"
    assert db.session.query(WorkerTaskModel).filter_by(function_name="add_file_to_db_task").count() == 1


@pytest.mark.parametrize("phase", ["confirming_locations", "discovering_files"])
def test_progress_failure_stops_the_parent_and_retains_committed_children(app, monkeypatch, phase):
    add_location(app, "Project1", "a.pdf", present=True)
    write_file(app, "Project1/b.pdf")
    original_update = utils.RQTaskUtils.update_task_subroutine

    def update(*args, **kwargs):
        details = kwargs["status_details"]
        if details["Phase"] == phase and (
            details["Current File"] == os.path.join("Project1", "a.pdf") if phase == "confirming_locations"
            else details["Files Enqueued to Add"] == 1
        ):
            raise RuntimeError("injected progress failure")
        return original_update(*args, **kwargs)

    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    with pytest.raises(RuntimeError, match="progress failure"):
        run_scrape(app, confirm_data=phase == "confirming_locations")
    status, details, times = read_parent()
    assert status == "failed" and times[2] is not None
    assert details["Phase"] == phase and details["Stop Reason"] == "failed"
    assert details["Files Enqueued to Add"] == (1 if phase == "discovering_files" else 0)
    assert len(app.q.calls) == len(details["Child Task IDs"])


@pytest.mark.parametrize("phase", ["confirming_locations", "discovering_files"])
def test_rq_timeouts_propagate_from_each_phase(app, monkeypatch, phase):
    location = add_location(app, "Project1", "a.pdf", present=True)
    original_exists = os.path.exists

    def check(path):
        if path.endswith("a.pdf"):
            raise JobTimeoutException("injected timeout")
        return original_exists(path)

    def enqueue(kwargs):
        raise JobTimeoutException("injected timeout")

    if phase == "confirming_locations":
        monkeypatch.setattr(tasks.os.path, "exists", check)
    else:
        app.q.before_enqueue = enqueue
    with pytest.raises(JobTimeoutException):
        run_scrape(app, confirm_data=phase == "confirming_locations")
    status, details, _ = read_parent()
    assert status == "failed" and details["Phase"] == phase
    assert details["Files Enqueued to Add"] == 0
    db.session.refresh(location)
    assert location.existence_confirmed == datetime(2020, 1, 1)


def test_directory_enumeration_failure_is_fatal(app, monkeypatch):
    def broken_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("injected enumeration failure"))
        yield

    monkeypatch.setattr(tasks.os, "walk", broken_walk)
    with pytest.raises(PermissionError):
        run_scrape(app, confirm_data=False)
    status, details, _ = read_parent()
    assert status == "failed" and details["Phase"] == "discovering_files"


@pytest.mark.parametrize("relative,error", [("missing", FileNotFoundError), ("../outside", ValueError)])
def test_invalid_locations_fail_before_removing_records(app, relative, error):
    location = add_location(app, "Project1", "missing.pdf")
    location_id = location.id
    with pytest.raises(error):
        run_scrape(app, relative)
    assert db.session.get(FileLocationModel, location_id) is not None
    assert read_parent()[0] == "failed"


def test_confirmation_cleanup_failure_rolls_back_history_and_counts(app):
    location = add_location(app, "Project1", "missing.pdf")
    location_id, file_id = location.id, location.file_id
    user = UserModel(email="archivist@example.org")
    db.session.add(user)
    db.session.flush()
    history = ArchivedFileModel(file_id=file_id, destination_path="Project1/missing.pdf", file_size=1,
                               date_archived=datetime(2020, 1, 1), archivist_id=user.id)
    db.session.add(history)
    db.session.commit()

    def fail_delete(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("DELETE FROM files "):
            raise RuntimeError("injected cleanup failure")

    event.listen(db.engine, "before_cursor_execute", fail_delete)
    try:
        result = run_scrape(app)
    finally:
        event.remove(db.engine, "before_cursor_execute", fail_delete)
    assert result["Stop Reason"] == "completed_with_errors"
    assert result["Locations Checked"] == result["Locations Missing"] == result["Files Records Removed"] == 0
    assert db.session.get(FileLocationModel, location_id) is not None
    db.session.refresh(history)
    assert history.file_id == file_id


def test_removed_path_that_reappears_is_discovered_again(app, monkeypatch):
    add_location(app, "Project1", "reappeared.pdf")
    original_confirm = tasks._confirm_file_location

    def confirm_and_reappear(*args):
        result = original_confirm(*args)
        write_file(app, "Project1/reappeared.pdf")
        return result

    monkeypatch.setattr(tasks, "_confirm_file_location", confirm_and_reappear)
    result = run_scrape(app)
    assert result["Locations Missing"] == result["Files Enqueued to Add"] == 1
    assert result["Files Already Known"] == 0


def test_confirmation_batches_do_not_skip_rows_after_deletion(app):
    file = FileModel(hash="shared", size=1, extension="pdf")
    db.session.add(file)
    db.session.flush()
    db.session.add_all([FileLocationModel(file_id=file.id, file_server_directories="Project1",
                                        filename=f"missing-{index}.pdf") for index in range(1001)])
    db.session.commit()
    result = run_scrape(app)
    assert result["Locations Checked"] == result["Locations Missing"] == 1001
    assert result["Files Records Removed"] == 1
    assert db.session.query(FileLocationModel).count() == 0
    assert result["Files Enqueued to Add"] == 0


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires a forked worker")
def test_killed_parent_retains_phase_active_file_and_successful_child_ids(app):
    write_file(app, "Project1/a.pdf")
    write_file(app, "Project1/b.pdf")
    record_parent()
    db.session.remove()
    context = multiprocessing.get_context("fork")
    enqueueing = context.Event()
    hold = context.Event()

    def block_b(kwargs):
        if kwargs["kwargs"]["filepath"].endswith("b.pdf"):
            enqueueing.set()
            hold.wait(20)
            raise RuntimeError("child was not terminated")

    app.q.before_enqueue = block_b

    def run_child():
        with app.app_context():
            db.engine.dispose(close=False)
        tasks.scrape_location_files_task(os.path.join(app.config["ARCHIVES_LOCATION"], "Project1"),
                                         "parent", confirm_data=False)

    process = context.Process(target=run_child)
    process.start()
    try:
        assert enqueueing.wait(10), "worker did not reach the second enqueue"
        process.kill()
        process.join(5)
        assert process.exitcode is not None and process.exitcode < 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
    status, details, times = read_parent()
    assert status == "started" and times[0] and times[1] and times[2] is None
    assert details["Phase"] == "discovering_files"
    assert details["Current File"] == os.path.join("Project1", "b.pdf")
    assert details["Last Enqueued File"] == os.path.join("Project1", "a.pdf")
    assert details["Files Enqueued to Add"] == len(details["Child Task IDs"]) == 1
    child = db.session.query(WorkerTaskModel).filter_by(function_name="add_file_to_db_task").one()
    assert child.task_id == details["Child Task IDs"][0] and child.status == "queued"
