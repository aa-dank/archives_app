"""Verify durable backup phases, publication, and worker failure reporting."""

import bz2
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest
from rq.timeouts import JobTimeoutException
from sqlalchemy import text

from archives_application import create_app, db, utils
from archives_application.main import main_tasks as tasks
from archives_application.models import WorkerTaskModel


class TestConfig:
    SECRET_KEY = "backup-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "backup-test-client"
    REDIS_URL = "redis://localhost:6379/0"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    class DiskConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'tasks.sqlite'}"

    app = create_app(DiskConfig)
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    app.config["DATABASE_BACKUP_LOCATION"] = str(backup_dir)
    app.config["POSTGRESQL_EXECUTABLES_LOCATION"] = ""
    # The initialized SQLAlchemy engine stays SQLite; pg_dump gets a fake PG URL.
    app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql+psycopg://backup:secret@database.test/archives"
    monkeypatch.setattr(tasks, "app", app)
    with app.app_context():
        WorkerTaskModel.__table__.create(db.engine)
        db.session.add(WorkerTaskModel(task_id="backup", time_enqueued=datetime.now(),
                                      origin="default", function_name="db_backup_task", status="queued"))
        db.session.commit()
        yield app
        db.session.remove()
        WorkerTaskModel.__table__.drop(db.engine)


def read_task():
    with db.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT status, status_details, time_started, time_updated, time_completed "
            "FROM worker_tasks WHERE task_id='backup'"
        )).one()
    return row[0], json.loads(row[1]), row[2:]


class FakeDump:
    def __init__(self, content=b"CREATE TABLE example (id integer);\n", return_code=0):
        self.content = content
        self.stdout = io.BytesIO(content)
        self.diagnostics = None
        self.return_code = return_code
        self.running = True
        self.killed = False

    def start(self, *args, **kwargs):
        self.diagnostics = kwargs["stderr"]
        self.diagnostics.write(b"pg_dump diagnostic\n")
        return self

    def poll(self):
        return None if self.running else self.return_code

    def wait(self):
        self.running = False
        return self.return_code

    def kill(self):
        self.killed = True
        self.running = False


def test_phase_saves_are_committed_before_operations_and_results_remain_typed(app, monkeypatch):
    proc = FakeDump(content=b"SELECT 1;\n" * 200000)
    phases = []
    original_update = utils.RQTaskUtils.update_task_subroutine
    original_replace = tasks.os.replace

    def update(**kwargs):
        original_update(**kwargs)
        status, details, times = read_task()
        assert status == "started" and times[0] and times[1] and times[2] is None
        phases.append(details["phase"])

    def popen(command, **kwargs):
        assert read_task()[1]["phase"] == "dumping_and_compressing"
        assert "secret" not in json.dumps(read_task()[1])
        assert command[1].startswith("postgresql://")
        assert kwargs["env"]["PGPASSWORD"] == "secret"
        return proc.start(command, **kwargs)

    def wait():
        assert read_task()[1]["phase"] in ("waiting_for_pg_dump", "publishing")
        proc.running = False
        return 0

    def replace(source, destination):
        assert read_task()[1]["phase"] == "publishing"
        assert not Path(destination).exists()
        assert bz2.decompress(Path(source).read_bytes()) == proc.content
        original_replace(source, destination)

    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    monkeypatch.setattr(tasks.subprocess, "Popen", popen)
    monkeypatch.setattr(proc, "wait", wait)
    monkeypatch.setattr(tasks.os, "replace", replace)
    result = tasks.db_backup_task("backup")
    status, details, times = read_task()
    assert phases == ["dumping_and_compressing", "waiting_for_pg_dump", "publishing"]
    assert status == "finished" and details == result and all(times)
    assert details["phase"] == "complete" and details["errors"] == []
    assert isinstance(details["backup_command"], list)
    assert details["uncompressed_size"] == len(proc.content)
    assert details["compressed_size"] == Path(details["backup_location"]).stat().st_size
    assert isinstance(details["return_code"], int)
    assert isinstance(details["duration_seconds"], float)
    assert isinstance(details["dump_and_compress_seconds"], float)
    assert isinstance(details["rename_seconds"], float)
    assert bz2.decompress(Path(details["backup_location"]).read_bytes()) == proc.content
    assert not list(Path(app.config["DATABASE_BACKUP_LOCATION"]).glob("*.part"))
    assert proc.stdout.closed and proc.diagnostics.closed and not proc.killed


@pytest.mark.parametrize("failure", ["exit", "read", "timeout", "publish", "progress"])
def test_failures_cleanup_before_saving_failed_and_propagate(app, monkeypatch, failure):
    proc = FakeDump(return_code=7 if failure == "exit" else 0)
    monkeypatch.setattr(tasks.subprocess, "Popen", proc.start)
    error = JobTimeoutException("backup timed out") if failure == "timeout" else OSError("injected failure")
    original_update = utils.RQTaskUtils.update_task_subroutine
    original_failed = utils.RQTaskUtils.failed_task_subroutine

    def fail(*args, **kwargs):
        raise error

    def update(**kwargs):
        if kwargs["status_details"]["phase"] == "waiting_for_pg_dump":
            raise error
        original_update(**kwargs)

    def save_failure(**kwargs):
        assert not proc.running
        assert proc.stdout.closed and proc.diagnostics.closed
        assert not list(Path(app.config["DATABASE_BACKUP_LOCATION"]).iterdir())
        original_failed(**kwargs)

    if failure in ("read", "timeout"):
        monkeypatch.setattr(proc.stdout, "read", fail)
    elif failure == "publish":
        monkeypatch.setattr(tasks.os, "replace", fail)
    elif failure == "progress":
        monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", update)
    monkeypatch.setattr(utils.RQTaskUtils, "failed_task_subroutine", save_failure)

    with pytest.raises(RuntimeError if failure == "exit" else type(error)) as raised:
        tasks.db_backup_task("backup")
    if failure != "exit":
        assert raised.value is error
    status, details, times = read_task()
    assert status == "failed" and all(times)
    assert details["stop_reason"] == "failed"
    assert isinstance(details["errors"], list) and details["errors"]
    assert "Traceback" in details["errors"][-1]["stack_trace"]
    assert details["phase"] != "complete"
    assert details["stderr"] == "pg_dump diagnostic\n"
    assert proc.killed == (failure in ("read", "timeout", "progress"))
    if failure == "exit":
        assert details["return_code"] == 7
        assert "pg_dump diagnostic" in details["stderr"]


def test_preparation_failure_is_failed_without_starting_dump(app, monkeypatch):
    app.config["DATABASE_BACKUP_LOCATION"] = None
    original_make_url = tasks.make_url

    def make_url(url):
        status, details, times = read_task()
        assert status == "started" and details["phase"] == "preparing"
        assert times[0] and times[1] and times[2] is None
        return original_make_url(url)

    def unexpected_popen(*args, **kwargs):
        pytest.fail("pg_dump should not start after preparation fails")

    monkeypatch.setattr(tasks.subprocess, "Popen", unexpected_popen)
    monkeypatch.setattr(tasks, "make_url", make_url)
    with pytest.raises(TypeError):
        tasks.db_backup_task("backup")
    status, details, times = read_task()
    assert status == "failed" and details["phase"] == "preparing" and all(times)


def test_dump_start_failure_is_failed_and_does_not_publish(app, monkeypatch):
    def popen(*args, **kwargs):
        raise FileNotFoundError("pg_dump is unavailable")

    monkeypatch.setattr(tasks.subprocess, "Popen", popen)
    with pytest.raises(FileNotFoundError, match="pg_dump is unavailable"):
        tasks.db_backup_task("backup")
    status, details, _ = read_task()
    assert status == "failed" and details["phase"] == "dumping_and_compressing"
    assert not list(Path(app.config["DATABASE_BACKUP_LOCATION"]).iterdir())


def test_cleanup_failure_after_publication_fails_task_and_retains_backup(app, monkeypatch):
    proc = FakeDump()
    monkeypatch.setattr(tasks.subprocess, "Popen", proc.start)

    def close():
        raise OSError("cannot close pipe")

    monkeypatch.setattr(proc.stdout, "close", close)
    with pytest.raises(OSError, match="cannot close pipe"):
        tasks.db_backup_task("backup")
    status, details, _ = read_task()
    assert status == "failed" and details["phase"] == "publishing"
    assert len(details["errors"]) == 1
    assert details["errors"][0]["during"] == "cleanup"
    assert bz2.decompress(Path(details["backup_location"]).read_bytes()) == proc.content
    assert proc.diagnostics.closed


def test_cleanup_error_preserves_original_timeout_and_records_both_errors(app, monkeypatch):
    proc = FakeDump()
    error = JobTimeoutException("original timeout")
    monkeypatch.setattr(tasks.subprocess, "Popen", proc.start)

    def read(*args):
        raise error

    def remove(*args):
        raise PermissionError("cannot remove partial backup")

    monkeypatch.setattr(proc.stdout, "read", read)
    monkeypatch.setattr(tasks.os, "remove", remove)
    with pytest.raises(JobTimeoutException) as raised:
        tasks.db_backup_task("backup")
    assert raised.value is error
    status, details, _ = read_task()
    assert status == "failed"
    assert len(details["errors"]) == 2
    assert any(item.get("during") == "cleanup" for item in details["errors"])
    assert details["errors"][-1]["error"] == "original timeout"
    assert proc.killed and proc.stdout.closed and proc.diagnostics.closed


def test_failed_status_save_preserves_original_task_exception(app, monkeypatch, caplog):
    proc = FakeDump()
    error = JobTimeoutException("original timeout")
    monkeypatch.setattr(tasks.subprocess, "Popen", proc.start)

    def read(*args):
        raise error

    def save_failure(**kwargs):
        raise OSError("status database unavailable")

    monkeypatch.setattr(proc.stdout, "read", read)
    monkeypatch.setattr(utils.RQTaskUtils, "failed_task_subroutine", save_failure)
    with pytest.raises(JobTimeoutException) as raised:
        tasks.db_backup_task("backup")
    assert raised.value is error
    assert "Could not save failed worker task backup" in caplog.text
    assert "status database unavailable" in caplog.text
    assert read_task()[0] == "started"
    assert proc.killed and proc.stdout.closed and proc.diagnostics.closed
    assert not list(Path(app.config["DATABASE_BACKUP_LOCATION"]).iterdir())


def test_real_subprocess_can_write_large_diagnostics_before_dump_output(app, monkeypatch):
    real_popen = tasks.subprocess.Popen
    diagnostics = "diagnostic\n" * 32768
    content = b"SELECT 1;\n" * 1000

    def popen(command, **kwargs):
        # Exercise actual OS descriptors and process cleanup without PostgreSQL.
        script = ("import sys; sys.stderr.write('diagnostic\\n' * 32768); "
                  "sys.stdout.buffer.write(b'SELECT 1;\\n' * 1000)")
        return real_popen([sys.executable, "-c", script], **kwargs)

    monkeypatch.setattr(tasks.subprocess, "Popen", popen)
    result = tasks.db_backup_task("backup")
    assert read_task()[0] == "finished"
    assert result["stderr"] == diagnostics
    assert bz2.decompress(Path(result["backup_location"]).read_bytes()) == content
