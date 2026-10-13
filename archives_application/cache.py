"""Small byte-storage boundary; consumers own keys, codecs, and lifetimes."""

from typing import Protocol
from urllib.parse import urlsplit

import redis
from redis.backoff import NoBackoff
from redis.connection import parse_url
from redis.retry import Retry


class CacheUnavailable(Exception):
    """The configured cache could not complete an operation."""


class ByteCache(Protocol):
    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl_seconds: int) -> None: ...
    def delete(self, key: str) -> None: ...


def positive_integer(value, name: str) -> int:
    """Require integer seconds/bytes; booleans and coercible strings are invalid."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


class RedisCache:
    """Redis-compatible GET/SET EX/DEL with atomic per-key publication."""

    def __init__(self, client):
        self.client = client

    @classmethod
    def from_url(cls, url: str, timeout_seconds: float):
        parts = urlsplit(url)
        if parts.scheme not in {"redis", "rediss"} or not parts.hostname:
            raise ValueError("CACHE_URL must specify a redis:// or rediss:// endpoint")
        # Namespaces provide isolation; this adapter always uses database zero.
        if parts.path not in {"", "/", "/0"}:
            raise ValueError("CACHE_URL must use database zero; use CACHE_NAMESPACE for isolation")
        options = parse_url(url)
        if options.get("db", 0) != 0:
            raise ValueError("CACHE_URL must use database zero; use CACHE_NAMESPACE for isolation")
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
            protocol=2,
        )
        pool = redis.ConnectionPool(**options)
        try:
            # Validate lazy connection options without opening a socket. Bad URL
            # options must disable the cache at startup, not fail a later search.
            pool.connection_class(**pool.connection_kwargs)
        except (redis.RedisError, TypeError, ValueError) as error:
            raise ValueError("Invalid cache connection configuration") from error
        return cls(redis.Redis(connection_pool=pool))

    def get(self, key: str) -> bytes | None:
        try:
            return self.client.get(key)
        except (redis.RedisError, OSError, TypeError, ValueError) as error:
            raise CacheUnavailable("Cache read failed") from error

    def set(self, key: str, value: bytes, ttl_seconds: int) -> None:
        positive_integer(ttl_seconds, "ttl_seconds")
        if not isinstance(value, bytes):
            raise TypeError("Cache values must be bytes")
        try:
            self.client.set(key, value, ex=ttl_seconds)
        except (redis.RedisError, OSError, TypeError, ValueError) as error:
            raise CacheUnavailable("Cache write failed") from error

    def delete(self, key: str) -> None:
        try:
            self.client.delete(key)
        except (redis.RedisError, OSError, TypeError, ValueError) as error:
            raise CacheUnavailable("Cache deletion failed") from error
