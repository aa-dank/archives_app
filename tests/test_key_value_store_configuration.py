"""Verify one connection source for cache, queue producer, and RQ worker."""

import json
import os
import runpy
import subprocess
import sys

import pytest
import redis

import archives_application
from archives_application import app_config, create_app
from archives_application.cache import KeyValueCache
from archives_application.key_value_store import rq_connection_from_url


@pytest.fixture()
def configuration_files(tmp_path, monkeypatch):
    """Supply non-secret JSON/OAuth inputs without relying on deployment credentials."""
    monkeypatch.delenv("KEY_VALUE_STORE_URL", raising=False)
    entries = {
        "SECRET_KEY": "store-config-test",
        "Sqalchemy_Database_Location": "database.test",
        "POSTGRESQL_DATABASE": "test_archive",
        "POSTGRESQL_USERNAME": "test_user",
        "POSTGRESQL_PASSWORD": "test_password",
        "POSTGRESQL_PORT": "5432",
        "POSTGRESQL_SSL": "False",
        "KEY_VALUE_STORE_URL": "redis://json.test:6380/0",
        "ARCHIVE_SEARCH_CACHE_ENABLED": True,
        "CACHE_NAMESPACE": "store-config:test",
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


def test_explicit_url_preserves_connection_options():
    """An explicit URL retains its TLS/auth/options."""
    url = "rediss://test_user:test_password@explicit.test:6380/0?ssl_cert_reqs=required"
    config = {"KEY_VALUE_STORE_URL": url}
    assert app_config.resolve_key_value_store_url(config, environ={}) == url


def test_environment_url_wins_for_all_connection_roles():
    """Keep the worker's existing environment override and apply it to web/cache too."""
    config = {"KEY_VALUE_STORE_URL": "redis://json.test:6380/0"}
    assert app_config.resolve_key_value_store_url(config, environ={"KEY_VALUE_STORE_URL": "redis://environment.test:6381/0"}) == "redis://environment.test:6381/0"


@pytest.mark.parametrize("settings", [
    {"REDIS_URL": "redis://obsolete.test:6379/0"},
    {"REDIS_LOCATION": "obsolete.test", "REDIS_PORT": "6379"},
])
@pytest.mark.parametrize("source", ["environment", "config"])
def test_redis_settings_cannot_supply_the_endpoint(settings, source):
    """Only the key-value store URL can configure the shared connection."""
    config, environment = (settings, {}) if source == "config" else ({}, settings)
    with pytest.raises(ValueError, match="KEY_VALUE_STORE_URL is required"):
        app_config.resolve_key_value_store_url(config, environ=environment)


@pytest.mark.parametrize("value", [None, False, 6379, "", " "])
@pytest.mark.parametrize("source", ["environment", "config"])
def test_explicit_invalid_url_does_not_fall_back(value, source):
    """An invalid environment override cannot fall back to the file's endpoint."""
    config = {"KEY_VALUE_STORE_URL": "redis://json.test:6379/0"}
    environment = {}
    (environment if source == "environment" else config)["KEY_VALUE_STORE_URL"] = value
    with pytest.raises(ValueError, match="KEY_VALUE_STORE_URL must be a nonempty string"):
        app_config.resolve_key_value_store_url(config, environ=environment)


def test_shared_endpoint_is_required():
    """Missing connection configuration is a clear queue/cache configuration error."""
    with pytest.raises(ValueError, match="KEY_VALUE_STORE_URL is required"):
        app_config.resolve_key_value_store_url({}, environ={})


@pytest.mark.parametrize("environment_url", [None, "redis://environment.test:6381/0"])
def test_worker_app_queue_and_cache_share_resolved_endpoint(configuration_files, monkeypatch, environment_url):
    """Verify the worker entry point and actual app factory share JSON/environment precedence."""
    config_path, oauth_path, write_config = configuration_files
    # A migrated file may retain the obsolete cache URL; it must not select a
    # second service after the shared-endpoint decision.
    write_config({"CACHE_URL": "redis://obsolete-cache.test:6382/0"})
    monkeypatch.setenv("REDIS_URL", "redis://obsolete-environment.test:6382/0")
    monkeypatch.setattr(archives_application, "config_json", str(config_path))
    if environment_url:
        monkeypatch.setenv("KEY_VALUE_STORE_URL", environment_url)
    expected = environment_url or "redis://json.test:6380/0"
    config_class = app_config.json_to_config_factory(str(oauth_path), str(config_path))
    app = create_app(config_class)
    worker = runpy.run_path("worker.py", run_name="worker_configuration_test")
    assert app.config["KEY_VALUE_STORE_URL"] == worker["key_value_store_url"] == expected
    assert config_class.KEY_VALUE_STORE_URL == app_config.key_value_store_url_from_config_file(str(config_path)) == expected
    cache = app.extensions["cache"]
    assert isinstance(cache, KeyValueCache)
    expected_host = "environment.test" if environment_url else "json.test"
    expected_port = 6381 if environment_url else 6380
    for pool in (cache.client.connection_pool, app.q.connection.connection_pool, worker["conn"].connection_pool):
        assert pool.connection_kwargs["host"] == expected_host
        assert pool.connection_kwargs["port"] == expected_port
        assert pool.connection_kwargs["db"] == 0
        connection = pool.connection_class(**pool.connection_kwargs)
        assert connection.protocol == 2
        assert connection.encoder.decode_responses is False
        assert connection.retry.get_retries() == 0
    assert cache.client.connection_pool is not app.q.connection.connection_pool
    assert cache.client.connection_pool.connection_kwargs["socket_timeout"] == 0.5
    assert "default" in worker["listen"]


@pytest.mark.parametrize("settings", [
    {"REDIS_URL": "redis://obsolete.test:6379/0"},
    {"REDIS_LOCATION": "obsolete.test", "REDIS_PORT": "6379"},
])
def test_json_loaders_require_key_value_store_url(configuration_files, settings):
    """Web and worker loaders both reject files with only obsolete connection keys."""
    config_path, oauth_path, write_config = configuration_files
    write_config(settings)
    entries = json.loads(config_path.read_text())
    del entries["KEY_VALUE_STORE_URL"]
    config_path.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match="KEY_VALUE_STORE_URL is required"):
        app_config.json_to_config_factory(str(oauth_path), str(config_path))
    with pytest.raises(ValueError, match="KEY_VALUE_STORE_URL is required"):
        app_config.key_value_store_url_from_config_file(str(config_path))


def test_app_honors_environment_set_after_configuration_load(configuration_files, monkeypatch):
    """Late process-environment changes cannot leave cache and producer on a frozen URL."""
    config_path, oauth_path, _ = configuration_files
    config_class = app_config.json_to_config_factory(str(oauth_path), str(config_path))
    monkeypatch.setenv("KEY_VALUE_STORE_URL", "redis://late-environment.test:6383/0")
    app = create_app(config_class)
    assert app.config["KEY_VALUE_STORE_URL"] == "redis://late-environment.test:6383/0"
    assert app.extensions["cache"].client.connection_pool.connection_kwargs["host"] == "late-environment.test"
    assert app.q.connection.connection_pool.connection_kwargs["host"] == "late-environment.test"


def test_redis_environment_does_not_override_key_value_store_config():
    """An obsolete process variable cannot redirect web/cache/worker connections."""
    url = "redis://configured.test:6379/0"
    assert app_config.resolve_key_value_store_url(
        {"KEY_VALUE_STORE_URL": url}, environ={"REDIS_URL": "redis://obsolete.test:6379/0"},
    ) == url


def test_selected_config_file_applies_to_app_and_worker(configuration_files):
    """Select a WSL-style file before import without changing deployment defaults."""
    config_path, _, _ = configuration_files
    environment = dict(os.environ, ARCHIVES_CONFIG_FILE=str(config_path))
    environment.pop("KEY_VALUE_STORE_URL", None)
    result = subprocess.run([
        sys.executable, "-c",
        "import os, runpy; from archives_application import create_app, config_json; "
        "app = create_app(); worker = runpy.run_path('worker.py', run_name='test_worker'); "
        "assert config_json == os.environ['ARCHIVES_CONFIG_FILE']; "
        "assert app.config['CONFIG_JSON_PATH'] == config_json; "
        "assert app.config['KEY_VALUE_STORE_URL'] == worker['key_value_store_url'] == 'redis://json.test:6380/0'; "
        "assert app.q.connection.connection_pool.connection_kwargs['host'] == 'json.test'; "
        "assert app.extensions['cache'].client.connection_pool.connection_kwargs['host'] == 'json.test'",
    ], env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_queue_commands_are_not_replayed_after_connection_failure():
    """A failed send must reach the caller without redis-py replaying queue writes."""
    attempts = []

    class UnavailableConnection(redis.Connection):
        def connect(self):
            """Simulate a checked-out connection without opening a socket."""

        def can_read(self, timeout=0):
            return False

        def send_command(self, *args, **kwargs):
            attempts.append(args)
            raise redis.ConnectionError("simulated connection loss during write")

    client = rq_connection_from_url("redis://localhost:6379/0")
    client.connection_pool.connection_class = UnavailableConnection
    try:
        with pytest.raises(redis.ConnectionError):
            client.rpush("rq:queue:test", "job-id")
        assert attempts == [("RPUSH", "rq:queue:test", "job-id")]
    finally:
        client.close()


def test_queue_url_options_preserve_auth_tls_and_explicit_timeouts():
    """Keep transport options while enforcing RQ's byte-oriented RESP2 contract."""
    client = rq_connection_from_url(
        "rediss://user:password@store.test:6380/0?socket_timeout=45&socket_connect_timeout=3"
        "&ssl_cert_reqs=required&protocol=3&decode_responses=True",
    )
    pool = client.connection_pool
    connection = pool.connection_class(**pool.connection_kwargs)
    assert isinstance(connection, redis.SSLConnection)
    assert connection.username == "user" and connection.password == "password"
    assert connection.socket_timeout == 45 and connection.socket_connect_timeout == 3
    assert connection.protocol == 2 and connection.encoder.decode_responses is False
    client.close()
