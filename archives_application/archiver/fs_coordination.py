# archives_application/archiver/fs_coordination.py

import flask
import os
import re
from contextlib import contextmanager
from os.path import commonpath, dirname
from typing import Optional, Set
import redis

# ---- Config (env-driven)
REDIS_URL = flask.current_app.config.get("REDIS_URL", "redis://localhost:6379/0")
FS_LOCK_DEPTH = int(flask.current_app.config.get("FS_LOCK_DEPTH", "4"))          # how coarsely to bucket locks
FS_ALIAS_TTL_SECS = int(flask.current_app.config.get("FS_ALIAS_TTL_SECS", str(7*24*3600)))

r = redis.from_url(REDIS_URL, decode_responses=True)

def _canon(p: str) -> str:
    """
    Canonicalize a filesystem path for stable comparisons and Redis keying.

    The transformation is:
      - `os.path.normpath` to collapse redundant separators and up-level refs.
      - `os.path.normcase` to normalize case on case-insensitive platforms
        (e.g., Windows). On POSIX, this is a no-op.

    This function does **not** resolve symlinks and does not touch UNC/drive
    semantics beyond what `normpath/normcase` do.

    Args:
        p: Any absolute or relative path, possibly with mixed separators.

    Returns:
        A normalized string path suitable for prefix checks and dictionary keys.

    Examples:
        >>> _canon(r"N:\\foo\\..\\BAR\\baz\\")
        'n:\\bar\\baz'     # on Windows
    """
    return os.path.normcase(os.path.normpath(p))


def _rel_first_n(server_root: str, p: str, n: int) -> str:
    """
    Take the first `n` relative path components of `p` with respect to `server_root`.

    Used to define a "bucket" (lock scope) that groups operations by a shared
    ancestor. If `p` is the root or outside of `server_root`, the root is
    returned to avoid accidental cross-root locks.

    Args:
        server_root: Absolute canonical root against which to compute relativity.
        p:           Candidate path (file or directory).
        n:           Number of leading relative components to keep (>= 0).

    Returns:
        An absolute, canonical path representing the bucket ancestor.

    Notes:
        - On Windows, both `server_root` and `p` should be on the same drive to
          produce a meaningful relative path. If they are not, the function
          conservatively returns the canonical `server_root`.
    """
    root = _canon(server_root)
    cp = _canon(p)
    if not (cp == root or cp.startswith(root + os.sep)):
        # Outside of the root → fall back to the root itself
        return root
    rel = os.path.relpath(cp, root)
    parts = re.split(r"[\\/]+", "." if rel == "." else rel)
    if parts == ["."]:
        return root
    return _canon(os.path.join(root, *parts[:n]))


def calc_lock_bucket(server_root: str,
                     old_path: Optional[str],
                     new_path: Optional[str]) -> str:
    """
    Derive a stable lock bucket path for an operation touching `old_path` and/or `new_path`.

    The bucket is computed by:
      1) Mapping each provided path to its first `FS_LOCK_DEPTH` relative parts
         under `server_root` via `_rel_first_n(...)`.
      2) Taking `os.path.commonpath` across those partials to get the tightest
         shared ancestor.
      3) On failure (e.g., different drives), fall back to the canonical root.

    Args:
        server_root: Absolute root of the managed tree (e.g., 'N:\\PPDO\\Records').
        old_path:    Path being read/removed/renamed from (may be None).
        new_path:    Path being created/renamed/moved to (may be None).

    Returns:
        Canonical absolute path representing the lock bucket for this mutation.

    Examples:
        - Moving a directory under `N:\\PPDO\\Records\\6401\\6401\\G20\\...`
          with `FS_LOCK_DEPTH=4` will typically bucket at
          `N:\\PPDO\\Records\\6401\\6401`.
    """
    keys = []
    for p in (old_path, new_path):
        if p:
            keys.append(_rel_first_n(server_root, p, FS_LOCK_DEPTH))
    if not keys:
        return _canon(server_root)
    try:
        return _canon(commonpath(keys))
    except ValueError:
        # e.g., paths on different drives; coarse but safe
        return _canon(server_root)


@contextmanager
def path_lock(server_root: str,
              old_path: Optional[str] = None,
              new_path: Optional[str] = None,
              hold_secs: int = 900,
              wait_secs: int = 120):
    """
    Acquire a Redis-backed, path-scoped mutex for a filesystem critical section.

    The lock key is derived from `calc_lock_bucket(...)` so that operations that
    might collide (e.g., two edits under the same project subtree) serialize.

    Args:
        server_root: Root of the managed tree (absolute).
        old_path:    Source path (or ancestor) involved in the mutation.
        new_path:    Destination path (or ancestor) involved in the mutation.
        hold_secs:   Lock auto-expiration in Redis (seconds). Should exceed the
                     worst-case duration of the critical section to avoid early
                     expiry; but keep bounded to prevent deadlock if a worker dies.
        wait_secs:   Max time to wait to acquire the lock (blocking timeout).

    Yields:
        None. Use with a `with` statement to wrap the mutation.

    Raises:
        TimeoutError: If the lock cannot be acquired within `wait_secs`.

    Notes:
        - This lock is **advisory**; all code paths that mutate the FS must
          adopt it to be effective.
        - Keep non-essential work (heavy counting, logging, etc.) *outside*
          the lock to reduce contention; recheck critical predicates inside.
    """
    bucket = calc_lock_bucket(server_root, old_path, new_path)
    lock = r.lock(f"fslock:{bucket}", timeout=hold_secs, blocking_timeout=wait_secs)
    if not lock.acquire(blocking=True):
        raise TimeoutError(f"Couldn’t acquire FS lock for {bucket}")
    try:
        yield
    finally:
        try:
            lock.release()
        except Exception:
            # Safeguard: release may fail if lock auto-expired; ignore.
            pass


def resolve_alias(p: str) -> str:
    """
    Rewrite a path to follow recorded directory MOVE/RENAME aliases.

    The algorithm walks upward from `p`, checking for an exact ancestor alias
    key `fs:alias:<ABS_OLD_DIR>`. If found, it rewrites the prefix to the
    recorded `<ABS_NEW_DIR>`, preserves the suffix, and repeats (supporting
    chained moves like A→B→C). The walk continues until the root is reached
    or no further alias applies.

    Args:
        p: Absolute or relative path (file or directory). Relative paths are
           normalized; use absolute paths for best predictability.

    Returns:
        Canonical absolute/normalized path after applying zero or more alias
        rewrites. If no alias applies, returns the canonicalized input.

    Guarantees:
        - Idempotent: applying `resolve_alias` multiple times yields the same
          result.
        - Cycle-safe: maintains a `seen` set to avoid infinite loops if a bad
          alias is present.

    Examples:
        Suppose we stored:
            fs:alias:"N:\\A\\B" -> "N:\\X\\Y"
        Then:
            resolve_alias("N:\\A\\B\\C\\file.txt")
            → "n:\\x\\y\\c\\file.txt"  (on Windows)

    Implementation detail:
        Keys are individual strings `fs:alias:<old_dir>` with a TTL; we do not
        materialize a global map to keep lookups O(depth) and memory bounded.
    """
    cp = _canon(p)
    cur = cp
    seen: Set[str] = set()
    while True:
        nxt = r.get(f"fs:alias:{cur}")
        if nxt:
            if cur in seen:
                return cp  # defensive: break potential alias loops
            seen.add(cur)
            base = _canon(nxt)
            suffix = cp[len(cur):]
            cp = _canon(base + suffix)
            cur = cp
            continue
        parent = dirname(cur)
        if parent == cur:
            return cp
        cur = parent


def record_alias(old_dir: str, new_dir: str):
    """
    Record that a directory previously at `old_dir` now lives at `new_dir`.

    This is meant to be called **after** a successful MOVE/RENAME of a
    directory. Consumers should call `resolve_alias(...)` before existence
    checks to ensure queued tasks "follow" the new location.

    Args:
        old_dir: Absolute (or relative) path of the *directory* before the move.
        new_dir: Absolute (or relative) path of the *directory* after the move.

    Behavior:
        - Both inputs are canonicalized.
        - A Redis key `fs:alias:<old_dir>` is set to `<new_dir>` with TTL
          `FS_ALIAS_TTL_SECS`. Each alias stands alone so TTLs are independent.

    Notes:
        - **Directories only**: do not record aliases for files; file moves
          are expected to be referenced by their new full path in subsequent
          tasks.
        - Idempotent: repeated calls overwrite the same key, refreshing TTL.
    """
    r.set(f"fs:alias:{_canon(old_dir)}", _canon(new_dir), ex=FS_ALIAS_TTL_SECS)