"""Operator purge removes only search keys/artifacts and reports partial failures."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import redis

from archives_application import create_app, utils
from archives_application.archiver import search_cache_maintenance as maintenance


class TestConfig:
    SECRET_KEY = "purge-test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    GOOGLE_CLIENT_ID = "purge-test-client"
    KEY_VALUE_STORE_URL = "redis://localhost:6379/0"
    CACHE_NAMESPACE = "test:files"


class MemoryRedis:
    def __init__(self):
        self.keys = {b"test:files:archive-search:v1:result:old", b"test:files:archive-search:v1:workbook:old",
                     b"test:files:archive-search:v2:result:new", b"other:archive-search:v1:result:old",
                     b"test:files:other-cache:entry", b"rq:job:123"}
        self.fail = None
        self.patterns = []
        self.close_count = 0

    def scan_iter(self, match, count):
        self.patterns.append(match)
        # Return even nonmatching keys to exercise the defensive literal-prefix
        # guard independently from Redis glob matching.
        yield from sorted(self.keys)
        if self.fail == "scan":
            raise redis.ConnectionError("synthetic scan failure")

    def delete(self, key):
        if self.fail == "delete":
            raise redis.ConnectionError("synthetic delete failure")
        present = key in self.keys
        self.keys.discard(key)
        return int(present)

    def close(self):
        self.close_count += 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    app = create_app(TestConfig)
    backend = MemoryRedis()
    monkeypatch.setattr(maintenance.KeyValueCache, "from_url", lambda *args: SimpleNamespace(client=backend))
    monkeypatch.setattr(maintenance.utils.FlaskAppUtils, "create_temp_filepath",
                        lambda filename=None, **kwargs: str(tmp_path / filename if filename else tmp_path))
    exports = ["archive_search_results_20261119123456.xlsx", "archive_search_results_20261119123456000001.xlsx",
               "archive_search_results_20261119123456000002.xlsx.reserve", ".archive-search-abcd.xlsx"]
    unrelated = ["preview.png", "upload.docx", "archive_search_results_invalid.xlsx", "other_report.xlsx"]
    for name in exports + unrelated:
        (tmp_path / name).write_bytes(b"synthetic old artifact")
    return SimpleNamespace(app=app, backend=backend, directory=tmp_path, exports=exports, unrelated=unrelated)


def test_purge_all_versions_and_unreferenced_files_preserves_other_data_and_repeats(env):
    result = maintenance.purge_archive_search_output(env.app, "test:files")
    assert result.complete
    assert result.cache_keys_removed == 3 and result.export_files_removed == 4
    assert env.backend.close_count == 1
    assert env.backend.keys == {b"other:archive-search:v1:result:old", b"test:files:other-cache:entry", b"rq:job:123"}
    assert sorted(path.name for path in env.directory.iterdir()) == sorted(env.unrelated)
    again = maintenance.purge_archive_search_output(env.app, "test:files")
    assert again.complete and again.cache_keys_removed == again.export_files_removed == 0


def test_purge_escapes_namespace_globs_and_only_matches_literal_namespace(env):
    namespace = "test:[files]*?"
    target = f"{namespace}:archive-search:v1:result:old".encode()
    env.backend.keys.add(target)
    result = maintenance.purge_archive_search_output(env.app, namespace)
    assert result.complete and result.cache_keys_removed == 1
    assert b"test:files:archive-search:v1:result:old" in env.backend.keys
    assert target not in env.backend.keys
    assert env.backend.patterns == [r"test:\[files\]\*\?:archive-search:*"]


@pytest.mark.parametrize("failure", ["scan", "delete"])
def test_cache_failure_does_not_skip_physical_export_removal_and_retry_finishes(env, failure):
    env.backend.fail = failure
    result = maintenance.purge_archive_search_output(env.app, "test:files")
    assert not result.complete and result.cache_errors and not result.filesystem_errors
    assert env.backend.close_count == 1
    assert not any((env.directory / name).exists() for name in env.exports)
    env.backend.fail = None
    assert maintenance.purge_archive_search_output(env.app, "test:files").complete


def test_filesystem_failure_does_not_prevent_cache_cleanup_or_claim_completion(env, monkeypatch):
    original = Path.unlink
    def fail_one(path, *args, **kwargs):
        if path.name == env.exports[0]:
            raise PermissionError("synthetic export removal failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_one)
    result = env.app.test_cli_runner().invoke(args=["purge-archive-search-output", "--publishers-stopped"])
    assert result.exit_code == 1
    assert "Filesystem:" in result.output and "Purge incomplete" in result.output
    assert env.exports[0] in result.output
    assert "Purge complete." not in result.output
    assert (env.directory / env.exports[0]).exists()
    assert not any(key.startswith(b"test:files:archive-search:") for key in env.backend.keys)


def test_cli_requires_stopped_publishers_and_known_namespace_even_with_cache_disabled(env):
    runner = env.app.test_cli_runner()
    assert runner.invoke(args=["purge-archive-search-output"]).exit_code == 2
    assert len(env.backend.keys) == 6
    env.app.config["CACHE_NAMESPACE"] = None
    assert runner.invoke(args=["purge-archive-search-output", "--publishers-stopped"]).exit_code == 1
    result = runner.invoke(args=["purge-archive-search-output", "--namespace", "test:files", "--publishers-stopped"])
    assert result.exit_code == 0 and "Purge complete." in result.output


def test_purged_workbook_route_and_static_urls_are_unavailable(env):
    env.app.static_folder = str(env.directory.parent)
    # A real static directory layout permits exercising the previously exposed
    # static URL as well as the validated download route.
    static_temp = env.directory / "temp_files"
    static_temp.mkdir()
    env.app.static_folder = str(env.directory)
    timestamp = "20261119123456"
    name = f"archive_search_results_{timestamp}.xlsx"
    path = static_temp / name
    path.write_bytes(b"synthetic cached workbook")
    from archives_application.archiver import routes
    client = env.app.test_client()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(routes.archive_search_service, "archive_search_workbook_path", lambda value: str(static_temp / name))
        assert client.get("/archives_search", query_string={"timestamp": timestamp}).status_code == 200
        assert client.get(f"/static/temp_files/{name}").status_code == 200
        patch.setattr(maintenance.utils.FlaskAppUtils, "create_temp_filepath",
                      lambda filename=None, **kwargs: str(static_temp / filename if filename else static_temp))
        assert maintenance.purge_archive_search_output(env.app, "test:files").complete
        response = client.get("/archives_search", query_string={"timestamp": timestamp})
        assert response.status_code == 404 and b"rerun the search" in response.data
        assert client.get(f"/static/temp_files/{name}").status_code == 404


def test_temp_path_helper_returns_directory_and_respects_filename_reuse(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = Path(utils.FlaskAppUtils.create_temp_filepath())
    assert directory.is_dir()
    path = Path(utils.FlaskAppUtils.create_temp_filepath("preview.png"))
    assert path.parent == directory
    path.write_bytes(b"preview")
    assert utils.FlaskAppUtils.create_temp_filepath("preview.png", unique_filepath=False) == str(path)
    assert Path(utils.FlaskAppUtils.create_temp_filepath("preview.png")) != path
    assert path.read_bytes() == b"preview"
