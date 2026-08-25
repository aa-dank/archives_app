# fs-coordination-added branch summary

## Purpose

The fs-coordination-added branch was created to make filesystem edits in the archives app safer and more reliable when several operations touch the same archive tree at once.

The core idea was to coordinate rename, move, delete, and batch-edit operations so they do not collide or leave the database and the filesystem out of sync.

## What the branch introduced

### 1. Filesystem coordination layer

A new module was added at:

- archives_application/archiver/fs_coordination.py

This module provides:

- path canonicalization for stable comparisons,
- lock-bucket calculation for grouping related operations,
- a Redis-backed advisory locking mechanism,
- alias tracking for directory moves/renames.

### 2. Collision avoidance

The coordination logic avoids collisions by:

- deriving a shared lock bucket from the relevant source and destination paths,
- serializing filesystem mutations that target the same subtree,
- using Redis locks so concurrent workers do not simultaneously mutate the same area,
- recording aliases after a directory move/rename so later reconciliation steps can follow the new path.

### 3. Server-edit integration

The branch also extends server-side edit handling in:

- archives_application/archiver/server_edit.py

This makes filesystem edits more structured by wrapping them in a server-edit abstraction that can:

- estimate affected files/data,
- enforce limits,
- execute the mutation,
- enqueue database reconciliation tasks.

## Why it mattered

The app manages a large archive filesystem and uses database records to reflect the on-disk layout. Without coordination, concurrent operations could race each other, leading to:

- missing or duplicated database reconciliation,
- stale path lookups after renames/moves,
- failures when one operation expects a path that another already changed.

The branch tries to reduce those risks by making filesystem edits more transactional and path-aware.

## Current assessment

Based on the branch history and implementation shape, this appears to be a meaningful but still mid-stage feature branch:

- the core coordination behavior is present,
- the branch clearly targets a real operational problem,
- but it still looks like it needs further hardening, testing, and cleanup before it would be considered fully mature.

### Implementation review — 2026-08-25

A direct review of `archives_application/archiver/fs_coordination.py` found
that the coordination layer is not ready to protect filesystem edits yet.
The module is currently dormant: `path_lock`, `record_alias`, and
`resolve_alias` have no callers outside the module.  In particular,
`ServerEdit.execute()` still performs rename, move, delete, and create
operations without a coordination lock, and its queued database
reconciliation tasks do not resolve recorded aliases.

The following issues must be addressed before integrating the layer:

1. **Flask configuration is read too early.** The module reads
   `flask.current_app.config` and constructs its Redis client at import time.
   Importing it before an application context is active will raise Flask's
   "working outside of application context" error. Configuration and Redis
   client lookup should instead happen lazily from the active application or
   be injected by the caller.

2. **A cross-subtree move is not mutually exclusive with an edit in either
   subtree.** A move between `root/A` and `root/B` produces the `root` lock
   key, whereas an operation only in `root/A` produces `root/A`. Redis locks
   do not have parent/child semantics, so both locks can be held at the same
   time. The implementation should acquire every affected bucket in a stable
   order, or intentionally use one coarse lock scope for all such edits.

3. **The documented Windows/UNC path behavior fails on Linux/WSL.** The
   module uses host `os.path` behavior for inputs such as `N:\\...`; on a
   Linux worker, backslashes are not separators. The root-prefix check then
   treats the path as outside the root, and `dirname()` cannot walk alias
   ancestors. The app should either pass only normalized mounted POSIX paths
   to this module or choose `ntpath`/POSIX path handling from the input form.
   Case normalization also needs an explicit policy for the case-insensitive
   SMB-backed archive mount.

4. **The lock lease can expire silently during a mutation.** The default
   900-second Redis lease matches `ServerEdit.execute()`'s default task
   timeout. A long copy or deletion can therefore outlive the lock, and the
   suppressed release exception hides that loss of ownership. Use lease
   renewal or a demonstrably larger lease, and log or fail explicitly when
   lock ownership is lost.

5. **Alias records need lifecycle and scope protections.** `record_alias()`
   does not validate that both directories are inside the managed archive
   root. Its seven-day TTL can also redirect a delayed task to an unrelated
   directory if a path is deleted and later recreated. Alias registration and
   consumption should be tied to the known server root and task lifetime.

Recommended next steps are to correct the module, add isolated tests for
POSIX and Windows/UNC paths, alias chains, lease loss, and concurrent
multi-bucket locking, then integrate the lock around each `ServerEdit`
filesystem mutation and ensure reconciliation tasks resolve aliases before
using queued paths.

## Branch history notes

The branch contains three significant commits:

1. added archiver/fs_coordination.py
2. test_fmp_reconciliation and related function upgrades
3. a merge back to the mainline after other changes had landed

That history suggests the work was introduced as a focused feature spike and then integrated with related project tooling and reconciliation work.

## Practical takeaway

If the goal is to understand the branch quickly, the key idea is:

- this was not just a refactor;
- it was an attempt to make archive filesystem mutations safer by introducing coordination, locking, and path-following behavior.
