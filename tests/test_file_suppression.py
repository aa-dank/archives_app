"""Restricted identities survive reconciliation; text display remains suppressed."""

import hashlib
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
from flask import g
from sqlalchemy import event, text

from archives_application import bcrypt, create_app, db, utils
from archives_application.archiver import archiver_tasks as tasks, archive_search as search
from archives_application.archiver import file_info, server_edit
from archives_application.models import ArchivedFileModel, FileLocationModel, FileModel, UserModel, WorkerTaskModel


class TestConfig:
    SECRET_KEY = "suppression-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "suppression-test-client"
    KEY_VALUE_STORE_URL = "redis://localhost:6379/0"
    USER_ARCHIVES_LOCATION = r"N:\Records"
    WTF_CSRF_ENABLED = False


@pytest.fixture
def app(tmp_path, monkeypatch, create_file_content_tables):
    class DiskConfig(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'files.sqlite'}"

    app = create_app(DiskConfig)
    archive = tmp_path / "archive"
    archive.mkdir()
    app.config["ARCHIVES_LOCATION"] = str(archive)
    monkeypatch.setattr(tasks, "app", app)
    monkeypatch.setattr(server_edit, "app", app)
    monkeypatch.setattr(utils.FlaskAppUtils, "create_temp_filepath",
                        lambda filename, **kwargs: str(tmp_path / filename))
    with app.app_context():
        event.listen(db.engine, "connect", lambda connection, record:
                     connection.create_function("concat", 2, lambda left, right: left + right))
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        for table in (UserModel.__table__, FileModel.__table__, FileLocationModel.__table__,
                      ArchivedFileModel.__table__, WorkerTaskModel.__table__):
            table.create(db.engine)
        create_file_content_tables()
        db.session.add(UserModel(id=1, email="reader@example.org", active=True, roles="USER",
                                  password=bcrypt.generate_password_hash("test-password").decode()))
        db.session.commit()
        yield app
        db.session.remove()
        db.engine.dispose()


def add_file(*, restricted=True, located=True, content=b"test file", name="plans.pdf", directory="A"):
    file = FileModel(hash=hashlib.sha1(content).hexdigest(), size=len(content), extension="pdf",
                     content_restricted_at=datetime(2026, 11, 1) if restricted else None,
                     restriction_reason="private-reason-marker" if restricted else None)
    db.session.add(file)
    db.session.flush()
    if located:
        db.session.add(FileLocationModel(file_id=file.id, file_server_directories=directory, filename=name))
    db.session.commit()
    return file


def add_dependents(file):
    db.session.execute(text("""INSERT INTO file_contents (file_hash, source_text, text_length)
        VALUES (:hash, 'SENSITIVE-TEXT-MARKER', 900)"""), {"hash": file.hash})
    db.session.execute(text("""INSERT INTO file_content_fts_chunks (file_hash, chunk_text, chunk_index)
        VALUES (:hash, 'SENSITIVE-SNIPPET-MARKER', 0)"""), {"hash": file.hash})
    db.session.execute(text("""INSERT INTO file_date_mentions (file_hash, mention_date, granularity)
        VALUES (:hash, '2026-01-02', 'day')"""), {"hash": file.hash})
    db.session.execute(text("""INSERT INTO file_content_failures (file_hash, stage, error)
        VALUES (:hash, 'embed', 'synthetic failure')"""), {"hash": file.hash})
    db.session.commit()


def add_history(file):
    history = ArchivedFileModel(file_id=file.id, archivist_id=1, destination_path="A/plans.pdf",
                                file_size=file.size, date_archived=datetime(2020, 1, 1))
    db.session.add(history)
    db.session.commit()
    return history


def task_record(name):
    db.session.add(WorkerTaskModel(task_id=name, time_enqueued=datetime.now(), origin="default",
                                   function_name=name, status="queued"))
    db.session.commit()


def dependent_counts(file_hash):
    return [db.session.execute(text(f"SELECT count(*) FROM {table} WHERE file_hash=:hash"),
                               {"hash": file_hash}).scalar_one()
            for table in ("file_content_fts_chunks", "file_date_mentions", "file_content_failures", "file_contents")]


@pytest.mark.parametrize("restricted", [False, True])
def test_last_location_cleanup_preserves_only_restricted_identity_and_history(app, restricted):
    file = add_file(restricted=restricted)
    file_id, file_hash = file.id, file.hash
    add_dependents(file)
    history = add_history(file)
    unrelated = add_file(content=b"other content")
    add_dependents(unrelated)
    location = FileLocationModel.query.filter_by(file_id=file_id).one()
    outcome = location.remove_from_db()
    assert outcome == {"location_removed": True, "file_removed": not restricted,
                       "restricted_file_retained": restricted}
    # Another connection still sees the original committed records: the method
    # cannot independently commit the deletion or history change.
    with db.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM file_locations WHERE file_id=:id"),
                                  {"id": file_id}).scalar_one() == 1
    db.session.commit()
    assert dependent_counts(file_hash) == [0, 0, 0, 0]
    assert dependent_counts(unrelated.hash) == [1, 1, 1, 1]
    db.session.refresh(history)
    assert history.file_id == (file_id if restricted else None)
    assert (db.session.get(FileModel, file_id) is not None) == restricted
    if restricted:
        assert file.content_restricted_at == datetime(2026, 11, 1)
        assert file.restriction_reason == "private-reason-marker"
    assert not any(location.remove_from_db().values())


def test_remaining_copy_preserves_content_and_stale_loaded_flag_is_refreshed(app):
    file = add_file(restricted=False)
    add_dependents(file)
    second = FileLocationModel(file_id=file.id, file_server_directories="B", filename="copy.pdf")
    db.session.add(second)
    db.session.commit()
    first = FileLocationModel.query.filter(FileLocationModel.id != second.id).one()
    assert first.remove_from_db() == {"location_removed": True, "file_removed": False,
                                      "restricted_file_retained": False}
    db.session.commit()
    assert dependent_counts(file.hash) == [1, 1, 1, 1]
    # Simulate a completed SQL maintenance change after the object was loaded.
    db.session.execute(text("UPDATE files SET content_restricted_at='2026-11-02' WHERE id=:id"), {"id": file.id})
    assert file.content_restricted_at is None
    assert second.remove_from_db()["restricted_file_retained"]
    db.session.commit()
    assert FileModel.query.one().content_restricted_at == datetime(2026, 11, 2)


@pytest.mark.parametrize("failure_table", ["file_content_failures", "files"])
def test_cleanup_failure_rolls_back_location_dependents_and_history(app, failure_table):
    file = add_file(restricted=False)
    add_dependents(file)
    history = add_history(file)
    location = FileLocationModel.query.one()

    def fail(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith(f"DELETE FROM {failure_table} "):
            raise RuntimeError("injected cleanup failure")

    event.listen(db.engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="injected"):
            location.remove_from_db()
        db.session.rollback()
    finally:
        event.remove(db.engine, "before_cursor_execute", fail)
    assert FileLocationModel.query.count() == FileModel.query.count() == 1
    assert dependent_counts(file.hash) == [1, 1, 1, 1]
    db.session.refresh(history)
    assert history.file_id == file.id


@pytest.mark.parametrize("method", ["queued_index", "daily_scrape", "move_fallback"])
def test_identical_reupload_under_new_name_reuses_restricted_id(app, method):
    file = add_file(located=False)
    original_id = file.id
    path = Path(app.config["ARCHIVES_LOCATION"]) / "B" / "renamed.pdf"
    path.parent.mkdir()
    path.write_bytes(b"test file")
    task_record("reattach")
    if method == "queued_index":
        tasks.add_file_to_db_task(str(path), "reattach")
    elif method == "daily_scrape":
        tasks._persist_scraped_file(db, str(path), app.config["ARCHIVES_LOCATION"], file.hash, path.stat().st_size)
    else:
        edit = object.__new__(server_edit.ServerEdit)
        edit.old_path = str(path.parent / "previous.pdf")
        edit.new_path = str(path)
        edit.is_file = True
        edit.exclusion_functions = []
        edit.add_move_to_db_task("reattach")
    db.session.expire_all()
    assert FileModel.query.count() == FileLocationModel.query.count() == 1
    assert FileLocationModel.query.one().file_id == original_id
    assert FileModel.query.one().content_restricted_at == datetime(2026, 11, 1)
    assert FileModel.query.one().restriction_reason == "private-reason-marker"


def test_index_replacement_retains_displaced_restricted_identity(app):
    old = add_file()
    old_id, old_hash = old.id, old.hash
    add_dependents(old)
    history = add_history(old)
    path = Path(app.config["ARCHIVES_LOCATION"]) / "A" / "plans.pdf"
    path.parent.mkdir()
    path.write_bytes(b"changed bytes")
    task_record("replace")
    new_id = tasks.add_file_to_db_task(str(path), "replace")
    db.session.expire_all()
    assert new_id != old_id
    assert FileLocationModel.query.one().file_id == new_id
    assert db.session.get(FileModel, new_id).content_restricted_at is None
    assert db.session.get(FileModel, old_id).content_restricted_at is not None
    assert history.file_id == old_id
    assert dependent_counts(old_hash) == [0, 0, 0, 0]


@pytest.mark.parametrize("scoped", [False, True])
def test_confirmation_retains_identity_and_reports_no_removed_file(app, scoped):
    file = add_file()
    add_dependents(file)
    task_record("confirm")
    if scoped:
        directory = Path(app.config["ARCHIVES_LOCATION"]) / "A"
        directory.mkdir()
        tasks.scrape_location_files_task(str(directory), "confirm")
    else:
        tasks.confirm_file_locations_task(app.config["ARCHIVES_LOCATION"], timedelta(seconds=30), "confirm")
    db.session.expire_all()
    assert FileModel.query.count() == 1
    assert FileLocationModel.query.count() == 0
    assert dependent_counts(file.hash) == [0, 0, 0, 0]
    details = WorkerTaskModel.query.one().status_details
    assert details["Files Records Removed" if scoped else "Files Removed"] == 0


@pytest.mark.parametrize("is_file", [False, True])
def test_server_delete_task_retains_restriction(app, is_file):
    file = add_file()
    add_dependents(file)
    edit = object.__new__(server_edit.ServerEdit)
    edit.old_path = str(Path(app.config["ARCHIVES_LOCATION"]) / "A" / "plans.pdf") if is_file else str(
        Path(app.config["ARCHIVES_LOCATION"]) / "A")
    edit.is_file = is_file
    task_record("delete")
    result = edit.add_deletion_to_db_task("delete")
    db.session.expire_all()
    assert result["location_entries_effected"] == 1
    assert result["files_entries_effected"] == 0
    assert FileModel.query.count() == 1 and FileLocationModel.query.count() == 0
    assert dependent_counts(file.hash) == [0, 0, 0, 0]


@pytest.mark.parametrize("directory,similar_sibling", [("A", "A2"), ("A_%", "A-other")])
def test_directory_cleanup_preserves_siblings_and_escapes_wildcards(app, directory, similar_sibling):
    file = add_file(directory=directory)
    add_dependents(file)
    db.session.add_all([
        FileLocationModel(file_id=file.id, file_server_directories=directory + "/child", filename="child.pdf"),
        FileLocationModel(file_id=file.id, file_server_directories=similar_sibling, filename="copy.pdf"),
    ])
    db.session.commit()
    edit = object.__new__(server_edit.ServerEdit)
    edit.old_path = str(Path(app.config["ARCHIVES_LOCATION"]) / directory)
    edit.is_file = False
    task_record("delete-directory")
    result = edit.add_deletion_to_db_task("delete-directory")
    db.session.expire_all()
    assert result["location_entries_effected"] == 2
    assert result["files_entries_effected"] == 0
    assert FileLocationModel.query.one().file_server_directories == similar_sibling
    assert dependent_counts(file.hash) == [1, 1, 1, 1]


def test_single_file_cleanup_removes_all_duplicate_index_rows(app):
    file = add_file()
    add_dependents(file)
    db.session.add(FileLocationModel(file_id=file.id, file_server_directories="A", filename="plans.pdf"))
    db.session.commit()
    edit = object.__new__(server_edit.ServerEdit)
    edit.old_path = str(Path(app.config["ARCHIVES_LOCATION"]) / "A" / "plans.pdf")
    edit.is_file = True
    task_record("delete-duplicates")
    result = edit.add_deletion_to_db_task("delete-duplicates")
    db.session.expire_all()
    assert result["location_entries_effected"] == 2 and result["files_entries_effected"] == 0
    assert FileLocationModel.query.count() == 0
    assert FileModel.query.one().content_restricted_at is not None
    assert dependent_counts(file.hash) == [0, 0, 0, 0]


def test_record_removal_uses_same_retention_policy(app):
    file = add_file()
    add_dependents(file)
    path = str(Path(app.config["ARCHIVES_LOCATION"]) / "A" / "plans.pdf")
    root_index = len(utils.FileServerUtils.split_path(app.config["ARCHIVES_LOCATION"]))
    assert server_edit.ServerEdit.record_file_server_file_removal(db, root_index, path) == (True, False)
    assert server_edit.ServerEdit.record_file_server_file_removal(db, root_index, path) == (False, False)
    assert dependent_counts(file.hash) == [0, 0, 0, 0]


@pytest.mark.parametrize("restricted", [False, True])
def test_move_reconciliation_cleans_missing_source_location(app, restricted):
    file = add_file(restricted=restricted)
    file_id, file_hash = file.id, file.hash
    add_dependents(file)
    history = add_history(file)
    edit = object.__new__(server_edit.ServerEdit)
    edit.old_path = str(Path(app.config["ARCHIVES_LOCATION"]) / "A")
    edit.new_path = str(Path(app.config["ARCHIVES_LOCATION"]) / "B")
    edit.is_file = False
    edit.exclusion_functions = []
    task_record("move")
    result = edit.add_move_to_db_task("move")
    db.session.expire_all()
    assert result["location_entries_effected"] == 1
    assert result["files_entries_effected"] == int(not restricted)
    assert FileLocationModel.query.count() == 0
    assert (db.session.get(FileModel, file_id) is not None) == restricted
    assert dependent_counts(file_hash) == [0, 0, 0, 0]
    assert history.file_id == (file_id if restricted else None)


def test_snippet_sql_excludes_restricted_chunks_even_when_ids_are_requested_directly(app):
    restricted = add_file()
    public = add_file(restricted=False, content=b"public content")
    add_dependents(restricted)
    add_dependents(public)
    db.session.execute(text("UPDATE file_content_fts_chunks SET chunk_text='PUBLIC TEXT' WHERE file_hash=:hash"),
                       {"hash": public.hash})
    db.session.commit()
    chunk_ids = db.session.execute(text("SELECT id FROM file_content_fts_chunks")).scalars().all()
    seen = []
    def headline(config, value, query, options):
        seen.append(value)
        assert value == "PUBLIC TEXT"
        return value
    connection = db.session.connection().connection
    connection.create_function("websearch_to_tsquery", 2, lambda config, query: query)
    connection.create_function("ts_headline", 4, headline)
    assert list(search._fetch_snippets("text", chunk_ids).values()) == ["PUBLIC TEXT"]
    assert seen == ["PUBLIC TEXT"]


def test_file_info_suppression_keeps_metadata_dates_and_status_without_text_queries(app):
    file = add_file()
    add_dependents(file)
    queries = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(db.engine, "before_cursor_execute", capture)
    try:
        data = file_info.get_file_info_api(file.hash, app, include_text=True)
        assert data["source_text"] is None
        assert data["content_restricted_at"] == "2026-11-01T00:00:00"
        assert data["detected_dates"] == [{"date": "2026-01-02", "occurrences": 1}]
        assert data["text_length"] == 900 and data["text_status"] == "content_searchable"
        assert data["location_count"] == 1
        assert "source_text" not in file_info.get_file_info_api(file.hash, app)
        assert "restriction_reason" not in data
        for signed_in in (False, True):
            client = app.test_client()
            if signed_in:
                with client.session_transaction() as session:
                    session["_user_id"] = "1"
                    session["_fresh"] = True
            # This fixture keeps an outer app context; force Flask-Login to
            # resolve each request's session rather than reuse g's prior user.
            g.pop("_login_user", None)
            response = client.get(f"/file_info/{file.hash}")
            assert response.status_code == 200
            if signed_in:
                assert response.headers["Cache-Control"] == "private, no-store"
            assert b"Extracted text is suppressed" in response.data
            assert b"SENSITIVE-TEXT-MARKER" not in response.data
            assert b"private-reason-marker" not in response.data
        assert not any("source_text" in query.lower() for query in queries)
    finally:
        event.remove(db.engine, "before_cursor_execute", capture)


def test_unrestricted_text_still_available_and_retained_hash_path_api_semantics(app, monkeypatch):
    file = add_file(restricted=False)
    add_dependents(file)
    assert file_info.get_file_info_api(file.hash, app, include_text=True)["source_text"] == "SENSITIVE-TEXT-MARKER"
    client = app.test_client()
    url = "/api/file_info"
    assert client.get(url, query_string={"file_hash": file.hash}).status_code == 401
    auth = ("reader@example.org", "test-password")
    file.content_restricted_at = datetime(2026, 11, 3)
    db.session.commit()
    hash_value = file.hash
    # The unchanged path resolver uses PostgreSQL-specific TRIM syntax. Stub
    # that boundary while exercising authentication, serialization and routing.
    monkeypatch.setattr(file_info, "resolve_user_path_to_file_hash", lambda **kwargs: (
        hash_value if FileLocationModel.query.count() else None))
    response = client.get(url, query_string={"user_path": r"N:\Records\A\plans.pdf", "include_text": "true"}, auth=auth)
    assert response.status_code == 200 and response.json["source_text"] is None
    FileLocationModel.query.one().remove_from_db()
    db.session.commit()
    response = client.get(url, query_string={"file_hash": hash_value, "include_text": "true"}, auth=auth)
    assert response.status_code == 200
    assert response.json["location_count"] == 0 and response.json["locations"] == []
    assert response.json["source_text"] is None and response.json["content_restricted_at"] is not None
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get(url, query_string={"user_path": r"N:\Records\A\plans.pdf"}, auth=auth).status_code == 404
    assert client.get(url, query_string={"file_hash": "unknown"}, auth=auth).status_code == 404
    html = client.get(f"/file_info/{hash_value}")
    assert html.status_code == 200 and b"No indexed locations" in html.data


def test_shared_text_reader_enforces_suppression_and_only_bounds_html(app):
    public = add_file(restricted=False)
    add_dependents(public)
    public_text = "Public text café\n" * 10
    db.session.execute(text("UPDATE file_contents SET source_text=:value, text_length=:length"),
                       {"value": public_text, "length": len(public_text)})
    db.session.commit()
    app.config["FILE_INFO_TEXT_WINDOW_CHARS"] = 12
    html = file_info.get_file_info(public.hash, app, include_text=True)
    assert html["extracted_text"] == public_text[:12]
    assert html["returned_text_length"] == 12 and html["text_truncated"]
    assert file_info.get_file_info_api(public.hash, app, include_text=True)["source_text"] == public_text
    public.content_restricted_at = datetime(2026, 11, 20)
    db.session.commit()
    # The shared reader itself enforces suppression for full and bounded reads,
    # even without the view-level metadata check.
    assert file_info._fetch_source_text(public.hash) is None
    assert file_info._fetch_source_text(public.hash, window_chars=12) is None
    assert file_info._fetch_source_text("unknown") is None


def test_shared_date_query_keeps_api_complete_and_html_bounded(app):
    file = add_file()
    add_dependents(file)
    db.session.execute(text("""INSERT INTO file_date_mentions (file_hash, mention_date, granularity, mentions_count)
        VALUES (:hash, '2026-01-01', 'day', 2), (:hash, '2026-01-02', 'month', 3)"""), {"hash": file.hash})
    db.session.commit()
    bounded = file_info._fetch_date_mentions(file.hash, limit=1)
    assert len(bounded) == 1 and bounded[0]["mention_date"].isoformat() == "2026-01-01"
    api = file_info.get_file_info_api(file.hash, app)
    assert api["detected_dates"] == [
        {"date": "2026-01-01", "occurrences": 2},
        {"date": "2026-01-02", "occurrences": 4},
    ]


def test_archived_or_not_handles_retained_hash_without_locations(app):
    add_file(located=False)
    client = app.test_client()
    response = client.post("/archived_or_not", data={"upload": (BytesIO(b"test file"), "plans.pdf")}, follow_redirects=True)
    assert response.status_code == 200
    assert b"No indexed copies are available" in response.data
    response = client.post("/api/archived_or_not?user=reader@example.org&password=test-password",
                           data={"file": (BytesIO(b"test file"), "plans.pdf")})
    assert response.status_code == 404


@pytest.mark.parametrize("scope", [search.ScopeResolution("all"), search.ScopeResolution("location", prefixes=["A"])])
def test_search_membership_and_coverage_require_locations_without_multiplying_copies(app, scope):
    present = add_file()
    add_dependents(present)
    absent = add_file(content=b"absent", located=False)
    add_dependents(absent)
    db.session.add(FileLocationModel(file_id=present.id, file_server_directories="A", filename="copy.pdf"))
    db.session.commit()
    params = {}
    cte = search._file_hash_scope_cte(scope, params)
    members = db.session.execute(text(f"WITH {cte} SELECT file_hash FROM scoped_file_hashes"), params).scalars().all()
    assert members == [present.hash]
    coverage = search._coverage_summary(scope, [], app)
    assert coverage["files_in_scope"] == coverage["content_searchable_files"] == 1


def test_content_candidate_sql_filters_absent_files_before_limits_and_keeps_restricted_matches(app, monkeypatch):
    statements = []
    class EmptyResult:
        def mappings(self):
            return self
        def all(self):
            return []
    monkeypatch.setattr(db.session, "execute", lambda statement, params: (statements.append(str(statement)) or EmptyResult()))
    search._execute_content_search("plans", search.ScopeResolution("all"), [], 10, app)
    sql = statements[0]
    assert sql.index("WHERE EXISTS (SELECT 1 FROM file_locations") < sql.index("LIMIT :chunk_candidate_limit")
    assert "content_restricted_at" not in sql
