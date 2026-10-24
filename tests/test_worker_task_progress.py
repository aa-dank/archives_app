"""Check persistence boundaries and primary-error preservation in shared progress."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from flask import Flask
from rq.timeouts import JobTimeoutException

from archives_application import db, utils
from archives_application.models import WorkerTaskModel


@pytest.fixture()
def app(tmp_path):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'progress.sqlite'}"
    db.init_app(app)
    with app.app_context():
        WorkerTaskModel.__table__.create(db.engine)
        db.session.add(WorkerTaskModel(task_id="task", time_enqueued=datetime.now(),
                                      origin="default", status="queued"))
        db.session.commit()
        yield app
        db.session.remove()


def task_record():
    db.session.expire_all()
    return db.session.query(WorkerTaskModel).filter_by(task_id="task").one()


def test_failure_save_rolls_back_pending_work_before_committing_snapshot(app):
    clock = SimpleNamespace(now=0)
    details = {"committed": 1, "Errors": ["task failed"]}
    progress = utils.WorkerTaskProgress(db, "task", details, clock=lambda: clock.now)
    progress.start()
    # A failed operation has flushed a row but has not committed it.
    db.session.add(WorkerTaskModel(task_id="unfinished", time_enqueued=datetime.now(),
                                  origin="default", status="queued"))
    db.session.flush()
    clock.now = 31
    progress.fail()
    assert db.session.query(WorkerTaskModel).filter_by(task_id="unfinished").count() == 0
    task = task_record()
    assert task.status == "failed" and task.time_completed == task.time_updated
    assert task.status_details == {**details, "Time Elapsed": "31.000s"}


@pytest.mark.parametrize("failure_at", ["rollback", "save"])
def test_failure_reporting_cannot_replace_original_timeout(app, monkeypatch, caplog, failure_at):
    progress = utils.WorkerTaskProgress(db, "task", {})
    progress.start()
    original_error = JobTimeoutException("original task timeout")

    def fail(*args, **kwargs):
        raise OSError("failure reporting unavailable")

    if failure_at == "rollback":
        monkeypatch.setattr(db.session, "rollback", fail)
    else:
        monkeypatch.setattr(utils.RQTaskUtils, "failed_task_subroutine", fail)
    with pytest.raises(JobTimeoutException) as raised:
        try:
            raise original_error
        except JobTimeoutException:
            progress.fail()
            raise
    assert raised.value is original_error
    assert "Could not save failed worker task task" in caplog.text
    assert "failure reporting unavailable" in caplog.text
    assert task_record().status == "started"


def test_failed_save_does_not_postpone_next_eligible_snapshot(app, monkeypatch):
    clock = SimpleNamespace(now=0)
    details = {"committed": 0}
    progress = utils.WorkerTaskProgress(db, "task", details, clock=lambda: clock.now)
    progress.start()
    original_update = utils.RQTaskUtils.update_task_subroutine

    def fail(**kwargs):
        raise OSError("temporary save failure")

    clock.now = 31
    details["committed"] = 1
    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", fail)
    with pytest.raises(OSError, match="temporary save failure"):
        progress.save()
    assert task_record().status_details == {"committed": 0}
    monkeypatch.setattr(utils.RQTaskUtils, "update_task_subroutine", original_update)
    progress.save()
    assert task_record().status_details["committed"] == 1
    details["committed"] = 2
    clock.now = 60
    progress.save()
    assert task_record().status_details["committed"] == 1
    clock.now = 61
    progress.save()
    assert task_record().status_details["committed"] == 2
