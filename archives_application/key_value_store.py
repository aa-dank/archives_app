"""Connection policy for the key-value store used by RQ producers and workers."""

import redis
from redis.backoff import NoBackoff
from redis.connection import parse_url
from redis.retry import Retry


def rq_connection_from_url(url: str) -> redis.Redis:
    """Build a lazy byte-oriented RESP2 connection with no command retries.

    Keep the pre-upgrade timeout defaults; RQ sets its worker read timeout to
    accommodate blocking dequeue operations. Explicit URL timeout/TLS/auth
    options still apply. Do not replay queue writes after connection failures.
    """
    options = {
        "socket_timeout": None,
        "socket_connect_timeout": None,
        "socket_keepalive": None,
    }
    options.update(parse_url(url))
    options.update(
        protocol=2,
        decode_responses=False,
        retry=Retry(NoBackoff(), 0),
        retry_on_timeout=False,
        retry_on_error=[],
    )
    return redis.Redis.from_pool(redis.ConnectionPool(**options))
