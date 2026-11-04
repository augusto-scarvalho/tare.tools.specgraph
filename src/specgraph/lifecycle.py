"""Durable edit hints and explicit/polling reconciliation, outside query paths.

Events accelerate a full content observation. They never prove that no edit was
missed. A bounded journal and periodic SHA reconciliation work without native
watcher dependencies, including WSL mounts and editors that replace files.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Iterator, Sequence
import uuid

from .serialization import canonical_json
from .storage import index_path, repository_identity

MAX_PATHS = 1024
MAX_RECEIPTS = 1024
LEASE_SECONDS = 30.0


def _utc(timestamp: float | None) -> str | None:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


def _path(root: Path | str, state_dir: Path | str | None) -> Path:
    root = Path(root).resolve()
    path = index_path(root, state_dir=state_dir).with_name("lifecycle.sqlite3").resolve()
    if path.is_relative_to(root):
        raise ValueError("Lifecycle state must be outside the indexed repository")
    return path


def _budget(timeout_ms: int) -> None:
    if type(timeout_ms) is not int or not 0 <= timeout_ms <= 1000:
        raise ValueError("timeout_ms must be an integer between 0 and 1000")


@contextmanager
def _writer(root: Path | str, state_dir: Path | str | None, timeout_ms: int = 20):
    _budget(timeout_ms)
    path = _path(root, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None, timeout=timeout_ms / 1000)
    try:
        if connection.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
            raise ValueError("Incompatible lifecycle journal")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS pending (path TEXT PRIMARY KEY, seq INTEGER NOT NULL)")
        connection.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, digest TEXT NOT NULL, seq INTEGER NOT NULL)")
        connection.execute("PRAGMA user_version=1")
        row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
        state = json.loads(row[0]) if row else {
            "repository_root": repository_identity(root), "journal_id": uuid.uuid4().hex,
            "event_seq": 0, "reconciled_seq": 0, "overflow_seq": 0,
            "last_event_at": None, "last_observation_started_at": None,
            "last_observation_completed_at": None, "last_attempt_at": None,
            "last_reconciled_snapshot": None, "last_result": None,
            "owner": None, "lease_until": 0.0,
        }
        if state["repository_root"] != repository_identity(root):
            raise ValueError("Lifecycle journal belongs to a different repository")
        yield connection, state
        connection.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (canonical_json(state),))
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def record_changes(root: Path | str, paths: Sequence[str], *, source: str = "native-hook",
                   event_id: str | None = None, overflow: bool = False,
                   timeout_ms: int = 20, state_dir: Path | str | None = None) -> dict[str, Any]:
    """Record/coalesce paths without scanning, hashing or parsing project files.

    @spec SPEC-SPECGRAPH-C3 [AC-03]
    """
    _budget(timeout_ms)
    root = Path(root).resolve()
    if isinstance(paths, (str, bytes)) or len(paths) > 4096 or type(overflow) is not bool:
        raise ValueError("paths must be a sequence of at most 4096 paths; overflow must be boolean")
    if not isinstance(source, str) or not 1 <= len(source) <= 128:
        raise ValueError("source must contain 1..128 characters")
    if event_id is not None and (not isinstance(event_id, str) or not 1 <= len(event_id) <= 512):
        raise ValueError("event_id must contain 1..512 characters")
    normalized = set()
    for value in paths:
        if not isinstance(value, str) or not 1 <= len(value) <= 4096 or "\0" in value:
            raise ValueError("Invalid changed path")
        candidate = Path(value)
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            normalized.add(candidate.resolve().relative_to(root).as_posix())
        except ValueError:
            raise ValueError("Changed path escapes selected repository") from None
    values = sorted(normalized)
    if not values and not overflow:
        return {"status": "COALESCED", "reason": "NO_PATHS", "event_seq": None}
    event_digest = hashlib.sha256(canonical_json([source, values, overflow]).encode()).hexdigest()
    try:
        with _writer(root, state_dir, timeout_ms) as (connection, state):
            if event_id is not None:
                row = connection.execute("SELECT digest,seq FROM receipts WHERE id=?", (event_id,)).fetchone()
                if row:
                    if row[0] != event_digest:
                        raise ValueError("Event ID reused for different input")
                    return {"status": "COALESCED", "journal_id": state["journal_id"], "event_seq": row[1]}
            state["event_seq"] += 1
            seq = state["event_seq"]
            state["last_event_at"] = time.time()
            connection.executemany("INSERT OR REPLACE INTO pending VALUES (?, ?)", [(p, seq) for p in values[:MAX_PATHS]])
            count = connection.execute("SELECT count(*) FROM pending").fetchone()[0]
            if overflow or len(values) > MAX_PATHS or count > MAX_PATHS:
                state["overflow_seq"] = seq
                connection.execute("DELETE FROM pending WHERE path NOT IN (SELECT path FROM pending ORDER BY seq DESC,path LIMIT ?)", (MAX_PATHS,))
            if event_id is not None:
                connection.execute("INSERT INTO receipts VALUES (?, ?, ?)", (event_id, event_digest, seq))
                connection.execute("DELETE FROM receipts WHERE id NOT IN (SELECT id FROM receipts ORDER BY seq DESC,id LIMIT ?)", (MAX_RECEIPTS,))
            return {"status": "RECORDED", "journal_id": state["journal_id"], "event_seq": seq,
                    "overflow": state["overflow_seq"] > state["reconciled_seq"]}
    except (sqlite3.DatabaseError, OSError):
        return {"status": "UNAVAILABLE", "reason": "EVENT_NOT_RECORDED; periodic reconciliation required"}


def lifecycle_status(root: Path | str, *, timeout_ms: int = 20,
                     state_dir: Path | str | None = None) -> dict[str, Any]:
    """Read bounded journal metadata only; this does not validate current sources."""
    _budget(timeout_ms)
    base = {"schema": "tare.tools/specgraph-lifecycle/1", "status": "NOT_INITIALIZED",
            "current_worktree": "NOT_CHECKED_ON_READ", "event_coverage": "BEST_EFFORT",
            "method": "PERIODIC_FULL_SHA256_WITH_DEBOUNCED_EVENT_HINTS", "authority": "NONE"}
    try:
        path = _path(root, state_dir)
        if not path.is_file():
            return base
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None,
                                     timeout=timeout_ms / 1000)
        try:
            try:
                connection.execute("PRAGMA query_only=ON")
                connection.execute("BEGIN DEFERRED")
                if connection.execute("PRAGMA user_version").fetchone()[0] != 1:
                    raise ValueError("Incompatible lifecycle journal")
                row = connection.execute("SELECT body FROM state WHERE id=1").fetchone()
                if not row:
                    return base
                state = json.loads(row[0])
                if state["repository_root"] != repository_identity(root):
                    raise ValueError("Lifecycle root mismatch")
                pending_count = connection.execute("SELECT count(*) FROM pending").fetchone()[0]
                paths = [row[0] for row in connection.execute("SELECT path FROM pending ORDER BY path LIMIT 64")]
                now = time.time()
                owner = state.get("owner")
                active = bool(owner and state["lease_until"] > now)
                overflow = state["overflow_seq"] > state["reconciled_seq"]
                dirty = state["event_seq"] > state["reconciled_seq"]
                last = state.get("last_observation_completed_at")
                return base | {"status": "PENDING" if dirty else "OBSERVED" if last else "UNKNOWN",
                    "journal_id": state["journal_id"], "event_seq": state["event_seq"],
                    "reconciled_seq": state["reconciled_seq"], "pending_count": pending_count,
                    "pending_paths": paths, "pending_paths_truncated": pending_count > len(paths),
                    "overflow": overflow, "last_event_at": state.get("last_event_at"),
                    "last_reconciled_snapshot": state.get("last_reconciled_snapshot"),
                    "last_observation_started_at": _utc(state.get("last_observation_started_at")),
                    "last_observation_completed_at": _utc(last),
                    "last_attempt_at": state.get("last_attempt_at"),
                    "observation_age_seconds": max(0.0, now - last) if last else None,
                    "last_result": state.get("last_result"),
                    "worker_status": "RECONCILING" if active else "EXPIRED" if owner else "IDLE",
                    "watcher_status": "ACTIVE" if state.get("watcher_until", 0) > now else "STOPPED_OR_UNOBSERVED"}
            finally:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
        finally:
            connection.close()
    except (sqlite3.DatabaseError, OSError, ValueError, KeyError, TypeError):
        return base | {"status": "UNAVAILABLE"}


def reconcile_repository(root: Path | str, *, state_dir: Path | str | None = None,
                         incremental: bool = True, reason: str = "explicit",
                         reuse_snapshot: bool = False) -> dict[str, Any]:
    """Publish outside the hook; acknowledge only events observed before indexing.

    No lock is held on the journal while scanning/parsing. Events arriving during
    publication remain pending. Crashes leave a lease that can expire; an expired
    worker cannot acknowledge another worker's events.

    @spec SPEC-SPECGRAPH-C3 [AC-03, AC-05]
    """
    from .indexer import index_repository
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("Repository root must exist")
    owner = uuid.uuid4().hex
    started = time.time()
    with _writer(root, state_dir, 1000) as (_, state):
        if state.get("owner") and state["lease_until"] > started:
            return {"status": "BUSY", "reason": "Reconciliation lease already held"}
        cutoff = state["event_seq"]
        journal_id = state["journal_id"]
        state.update(owner=owner, lease_until=started + LEASE_SECONDS, last_attempt_at=started)
    stop = threading.Event()

    def renew():
        while not stop.wait(LEASE_SECONDS / 3):
            try:
                with _writer(root, state_dir, 1000) as (_, state):
                    if state.get("owner") != owner:
                        return
                    state["lease_until"] = time.time() + LEASE_SECONDS
            except (sqlite3.DatabaseError, OSError, ValueError):
                return  # No unsafe acknowledgement if the lease is lost.

    heartbeat = threading.Thread(target=renew, name="specgraph-reconcile-lease", daemon=True)
    heartbeat.start()
    result = None
    failure = None
    try:
        result = index_repository(root, state_dir=state_dir, incremental=incremental,
                                  reuse_snapshot=reuse_snapshot)
    except Exception as exc:
        failure = exc
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    with _writer(root, state_dir, 1000) as (connection, state):
        finished = time.time()
        owned = (state.get("owner") == owner and state["journal_id"] == journal_id
                 and state.get("lease_until", 0.0) > finished)
        if owned:
            state.update(owner=None, lease_until=0.0, last_attempt_at=finished,
                         last_result="ERROR" if failure else result["status"])
            if result and result["status"] == "READY":
                state.update(reconciled_seq=cutoff, last_observation_started_at=started,
                             last_observation_completed_at=finished,
                             last_reconciled_snapshot=result["content_hash"])
                connection.execute("DELETE FROM pending WHERE seq<=?", (cutoff,))
    if failure:
        raise failure
    return result | {"reconciliation": {"reason": reason, "journal_id": journal_id,
                     "events_through": cutoff, "acknowledged": owned and result["status"] == "READY",
                     "observation_started_at": _utc(started), "observation_completed_at": _utc(finished)}}


def watch_repository(root: Path | str, *, state_dir: Path | str | None = None,
                     debounce_seconds: float = .3, interval_seconds: float = 30,
                     stop_event: threading.Event | None = None,
                     max_cycles: int | None = None) -> Iterator[dict[str, Any]]:
    """Foreground worker: debounce delivered hints, reconcile periodically anyway.

    A failed publication backs off instead of looping on the same event. Idle
    ticks only read the small journal. Polling is explicit and cancellable; no
    daemon, scheduler, or native watcher is installed automatically.
    """
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in (debounce_seconds, interval_seconds)):
        raise ValueError("Finite watch intervals required")
    if not 0 <= debounce_seconds <= 60 or not .1 <= interval_seconds <= 3600:
        raise ValueError("debounce must be 0..60s and interval .1..3600s")
    if max_cycles is not None and (type(max_cycles) is not int or max_cycles < 1):
        raise ValueError("max_cycles must be a positive integer")
    stop_event = stop_event or threading.Event()
    cycles = 0
    last_run = 0.0
    last_failure = 0.0
    owner = uuid.uuid4().hex
    with _writer(root, state_dir, 1000) as (_, state):
        busy = state.get("watcher_owner") and state.get("watcher_until", 0) > time.time()
        if not busy:
            state.update(watcher_owner=owner, watcher_until=time.time() + LEASE_SECONDS)
    if busy:
        yield {"status": "BUSY", "reason": "A foreground watcher already owns this repository"}
        return
    heartbeat_stop = threading.Event()

    def renew_watcher():
        while not heartbeat_stop.wait(LEASE_SECONDS / 3):
            try:
                with _writer(root, state_dir, 1000) as (_, state):
                    if state.get("watcher_owner") != owner:
                        stop_event.set()
                        return
                    state["watcher_until"] = time.time() + LEASE_SECONDS
            except (sqlite3.DatabaseError, OSError, ValueError):
                stop_event.set()
                return

    heartbeat = threading.Thread(target=renew_watcher, name="specgraph-watch-lease", daemon=True)
    heartbeat.start()
    try:
        while not stop_event.is_set():
            now = time.time()
            status = lifecycle_status(root, state_dir=state_dir)
            event_due = status.get("event_seq", 0) > status.get("reconciled_seq", 0) and now - (status.get("last_event_at") or now) >= debounce_seconds
            periodic_due = not last_run or now - last_run >= interval_seconds
            if (event_due or periodic_due) and now - last_failure >= min(1, interval_seconds):
                reason = "event" if event_due else "periodic_sha256"
                try:
                    result = reconcile_repository(root, state_dir=state_dir, reason=reason, reuse_snapshot=True)
                except (ValueError, OSError, sqlite3.DatabaseError, RuntimeError) as exc:
                    result = {"status": "ERROR", "reason": type(exc).__name__, "detail": str(exc)[:300]}
                last_run = time.time()
                if result.get("status") != "READY":
                    last_failure = last_run
                cycles += 1
                yield result
                if max_cycles is not None and cycles >= max_cycles:
                    break
            stop_event.wait(min(.1, interval_seconds))
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=2)
        with _writer(root, state_dir, 1000) as (_, state):
            if state.get("watcher_owner") == owner:
                state.update(watcher_owner=None, watcher_until=0)
