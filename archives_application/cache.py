"""Small byte-storage boundary; consumers own keys, codecs, and lifetimes."""

from typing import Protocol, Self
from urllib.parse import urlsplit

import redis
from redis.backoff import NoBackoff
from redis.connection import parse_url
from redis.retry import Retry


class CacheUnavailable(Exception):
    """The configured cache could not complete an operation."""


class ByteCache(Protocol):
    """Store disposable bytes with explicit per-entry lifetimes and failure signals."""

    def get(self, key: str) -> bytes | None:
        """Return stored bytes or None without renewing TTL; raise CacheUnavailable on failure."""
        ...

    def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        """Atomically replace one key for positive integer seconds, or raise CacheUnavailable."""
        ...

    def delete(self, key: str) -> None:
        """Remove a key if present; raise CacheUnavailable when deletion cannot complete."""
        ...


def positive_integer(value, name: str) -> int:
    """Require integer seconds/bytes; booleans and coercible strings are invalid."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class KeyValueCache:
    """Adapt Valkey/Redis GET/SET EX/DEL to atomic byte storage using redis-py.

    Connection failures use CacheUnavailable, while invalid write values and
    lifetimes remain caller errors. The adapter never chooses an entry's TTL.
    """

    def __init__(self, client) -> None:
        """Wrap a reusable client; this constructor performs no network I/O."""
        self.client = client

    @classmethod
    def from_url(cls, url: str, timeout_seconds: float) -> Self:
        """Build one lazy, bounded pool from a standalone RESP/TLS endpoint.

        Namespaces replace logical-database selection. URL options cannot
        enable retries or change the byte/timeout contract. Invalid connection
        configuration raises ValueError without connecting to the service.
        """
        parts = urlsplit(url)
        if parts.scheme not in {"redis", "rediss"} or not parts.hostname:
            raise ValueError("Cache endpoint must specify a redis:// or rediss:// URL")
        # Namespaces provide isolation; this adapter always uses database zero.
        if parts.path not in {"", "/", "/0"}:
            raise ValueError("Cache endpoint must use database zero; use CACHE_NAMESPACE for isolation")
        options = parse_url(url)
        if options.get("db", 0) != 0:
            raise ValueError("Cache endpoint must use database zero; use CACHE_NAMESPACE for isolation")
        # URL query options cannot override the bounded, byte-oriented contract.
        options.update(
            db=0,
            socket_timeout=timeout_seconds,
            socket_connect_timeout=timeout_seconds,
            decode_responses=False,
            retry=Retry(NoBackoff(), 0),
            retry_on_timeout=False,
            retry_on_error=[],
            health_check_interval=0,
            # Set RESP2 before pool construction: newer redis-py versions infer
            # optional maintenance-notification support from the chosen protocol.
            protocol=2,
        )
        try:
            pool = redis.ConnectionPool(**options)
            # Validate lazy connection options without opening a socket. Bad URL
            # options must disable the cache at startup, not fail a later search.
            pool.connection_class(**pool.connection_kwargs)
            client = redis.Redis(connection_pool=pool)
        except (redis.RedisError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("Invalid cache connection configuration") from error
        return cls(client)

    def get(self, key: str) -> bytes | None:
        """Read bytes without renewing expiration, translating backend failures."""
        try:
            return self.client.get(key)
        except (redis.RedisError, OSError, TypeError, ValueError, OverflowError) as error:
            raise CacheUnavailable("Cache read failed") from error

    def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        """Validate the value/lifetime, then atomically write bytes with SET EX."""
        positive_integer(ttl_seconds, "ttl_seconds")
        if not isinstance(value, bytes):
            raise TypeError("Cache values must be bytes")
        try:
            self.client.set(key, value, ex=ttl_seconds)
        except (redis.RedisError, OSError, TypeError, ValueError, OverflowError) as error:
            raise CacheUnavailable("Cache write failed") from error

    def delete(self, key: str) -> None:
        """Delete a key idempotently, translating backend failures."""
        try:
            self.client.delete(key)
        except (redis.RedisError, OSError, TypeError, ValueError, OverflowError) as error:
            raise CacheUnavailable("Cache deletion failed") from error
