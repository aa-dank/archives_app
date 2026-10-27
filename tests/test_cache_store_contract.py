"""Opt-in disposable-key check against an explicitly selected key-value service."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import redis

from archives_application.cache import KeyValueCache


def test_store_atomic_publication_and_independent_ttls():
    """Check a selected real backend with disposable keys, independent expiry, and concurrent atomic writes."""
    url = os.environ.get("ARCHIVES_CACHE_TEST_URL")
    socket = os.environ.get("ARCHIVES_CACHE_TEST_SOCKET")
    if not url and not socket:
        pytest.skip("Set ARCHIVES_CACHE_TEST_URL for a selected disposable Valkey/Redis service")
    adapter = (KeyValueCache.from_url(url, 0.5) if url else
               KeyValueCache(redis.Redis(unix_socket_path=socket, socket_timeout=0.5, socket_connect_timeout=0.5)))
    prefix = f"archives-cache-contract:{uuid4().hex}"
    short, longer, atomic = (prefix + suffix for suffix in (":short", ":long", ":atomic"))
    payloads = [json.dumps({"generation": i, "data": str(i) * 8192}).encode() for i in range(2)]
    try:
        adapter.set(short, b"short", 1)
        adapter.set(longer, b"long", 10)
        adapter.set(atomic, payloads[0], 10)
        def publish():
            """Repeatedly replace one complete value while the reader checks for partial-publication exposure."""
            for index in range(40):
                adapter.set(atomic, payloads[index % 2], 10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(publish)
            for _ in range(40):
                returned = adapter.get(atomic)
                assert returned in payloads
                assert len(json.loads(returned)["data"]) == 8192
            writer.result()
        assert 0 < adapter.client.ttl(short) <= 1
        assert 0 < adapter.client.ttl(longer) <= 10
        time.sleep(1.1)
        assert adapter.get(short) is None
        assert adapter.get(longer) == b"long"
        adapter.delete(longer)
        assert adapter.get(longer) is None
    finally:
        for key in (short, longer, atomic):
            adapter.delete(key)
