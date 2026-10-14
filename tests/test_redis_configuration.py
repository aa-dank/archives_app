"""Verify one connection source for cache, queue producer, and RQ worker."""

import json
import runpy

import pytest

import archives_application
from archives_application import app_config, create_app
from archives_application.cache import RedisCache


@pytest.fixture()
def configuration_files(tmp_path, monkeypatch):
    """Supply non-secret JSON/OAuth inputs without relying on deployment credentials."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    entries = {
        "SECRET_KEY": "redis-config-test",
        "Sqalchemy_Database_Location": "database.test",
        "POSTGRESQL_DATABASE": "test_archive",
        "POSTGRESQL_USERNAME": "test_user",
        "POSTGRESQL_PASSWORD": "test_password",
        "POSTGRESQL_PORT": "5432",
        "POSTGRESQL_SSL": "False",
        "REDIS_URL": "redis://json.test:6380/0",
        "ARCHIVE_SEARCH_CACHE_ENABLED": True,
        "CACHE_NAMESPACE": "redis-config:test",
    }
    config_path = tmp_path / "app.json"
    oauth_path = tmp_path / "oauth.json"
    oauth_path.write_text(json.dumps({"web": {"client_id": "test", "client_secret": "test"}}))

    def write_config(overrides=None):
        """Write VALUE/DESCRIPTION entries and return the same selected configuration path."""
        values = entries | (overrides or {})
        config_path.write_text(json.dumps({
            key: {"VALUE": value, "DESCRIPTION": "Test configuration"}
            for key, value in values.items()
        }))
        return config_path

    write_config()
    return config_path, oauth_path, write_config


def test_explicit_url_wins_over_legacy_host_and_port():
    """An explicit URL retains its TLS/auth/options even when legacy fields remain."""
    url = "rediss://test_user:test_password@explicit.test:6380/0?ssl_cert_reqs=required"
    config = {"REDIS_URL": url, "REDIS_LOCATION": "legacy.test", "REDIS_PORT": "6379"}
    assert app_config.resolve_redis_url(config, environ={}) == url


def test_environment_url_wins_for_all_connection_roles():
    """Keep the worker's existing environment override and apply it to web/cache too."""
    config = {"REDIS_URL": "redis://json.test:6380/0"}
    assert app_config.resolve_redis_url(config, environ={"REDIS_URL": "redis://environment.test:6381/0"}) == "redis://environment.test:6381/0"


def test_legacy_host_port_remains_a_fallback():
    """Existing JSON files still produce the same default-database endpoint."""
    assert app_config.resolve_redis_url(
        {"REDIS_LOCATION": "legacy.test", "REDIS_PORT": "6380"}, environ={},
    ) == "redis://legacy.test:6380"


@pytest.mark.parametrize("value", [None, False, 6379, "", " "])
@pytest.mark.parametrize("source", ["environment", "config"])
def test_explicit_invalid_url_does_not_fall_back(value, source):
    """Do not conceal a malformed explicit endpoint by using stale legacy settings."""
    config = {"REDIS_LOCATION": "legacy.test", "REDIS_PORT": "6379"}
    environment = {}
    (environment if source == "environment" else config)["REDIS_URL"] = value
    with pytest.raises(ValueError, match="REDIS_URL must be a nonempty string"):
        app_config.resolve_redis_url(config, environ=environment)


def test_shared_endpoint_is_required():
    """Missing connection configuration is a clear queue/cache configuration error."""
    with pytest.raises(ValueError, match="REDIS_URL is required"):
        app_config.resolve_redis_url({}, environ={})


@pytest.mark.parametrize("environment_url", [None, "redis://environment.test:6381/0"])
def test_worker_app_queue_and_cache_share_resolved_endpoint(configuration_files, monkeypatch, environment_url):
    """Verify the worker entry point and actual app factory share JSON/environment precedence."""
    config_path, oauth_path, write_config = configuration_files
    # A migrated file may retain the obsolete cache URL; it must not select a
    # second service after the shared-endpoint decision.
    write_config({"CACHE_URL": "redis://obsolete-cache.test:6382/0"})
    monkeypatch.setattr(archives_application, "config_json", str(config_path))
    if environment_url is not None:
        monkeypatch.setenv("REDIS_URL", environment_url)
    expected = environment_url or "redis://json.test:6380/0"
    config_class = app_config.json_to_config_factory(str(oauth_path), str(config_path))
    app = create_app(config_class)
    worker = runpy.run_path("worker.py", run_name="worker_configuration_test")
    assert app.config["REDIS_URL"] == worker["redis_url"] == expected
    assert config_class.REDIS_URL == app_config.redis_url_from_config_file(str(config_path)) == expected
    cache = app.extensions["cache"]
    assert isinstance(cache, RedisCache)
    expected_host = "environment.test" if environment_url else "json.test"
    expected_port = 6381 if environment_url else 6380
    for pool in (cache.client.connection_pool, app.q.connection.connection_pool, worker["conn"].connection_pool):
        assert pool.connection_kwargs["host"] == expected_host
        assert pool.connection_kwargs["port"] == expected_port
        assert pool.connection_kwargs["db"] == 0
    assert cache.client.connection_pool is not app.q.connection.connection_pool
    assert cache.client.connection_pool.connection_kwargs["socket_timeout"] == 0.5
    assert "default" in worker["listen"]


def test_json_loader_retains_legacy_compatibility(configuration_files):
    """Resolve old host/port-only JSON consistently in the web and worker loaders."""
    config_path, oauth_path, write_config = configuration_files
    write_config({"REDIS_LOCATION": "legacy.test", "REDIS_PORT": "6380"})
    entries = json.loads(config_path.read_text())
    del entries["REDIS_URL"]
    config_path.write_text(json.dumps(entries))
    config_class = app_config.json_to_config_factory(str(oauth_path), str(config_path))
    assert config_class.REDIS_URL == app_config.redis_url_from_config_file(str(config_path)) == "redis://legacy.test:6380"


def test_app_honors_environment_set_after_configuration_load(configuration_files, monkeypatch):
    """Late process-environment changes cannot leave cache and producer on a frozen URL."""
    config_path, oauth_path, _ = configuration_files
    config_class = app_config.json_to_config_factory(str(oauth_path), str(config_path))
    monkeypatch.setenv("REDIS_URL", "redis://late-environment.test:6383/0")
    app = create_app(config_class)
    assert app.config["REDIS_URL"] == "redis://late-environment.test:6383/0"
    assert app.extensions["cache"].client.connection_pool.connection_kwargs["host"] == "late-environment.test"
    assert app.q.connection.connection_pool.connection_kwargs["host"] == "late-environment.test"
