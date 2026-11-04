"""Lost-event recovery, bounded journal and truthful observation metadata (C3)."""
import ast
import os
from pathlib import Path
import threading
import time

import pytest

from specgraph import indexer
from specgraph.hook_context import context_hint
from specgraph.lifecycle import (_writer, lifecycle_status, record_changes,
                                 reconcile_repository, watch_repository)
from specgraph.storage import RepositoryIndex


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/lib.py").write_text("def leaf(): return 1\n", encoding="utf-8")
    (root / "specgraph.yaml").write_text("indexed_paths: [src]\ngoverned_paths: [src]\n", encoding="utf-8")
    return root, tmp_path / "state"


def test_metadata_and_events_never_scan_hash_or_parse_sources(project, monkeypatch):
    root, state = project
    def forbidden(*args, **kwargs):
        raise AssertionError("Journal path accessed source content or discovery")
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    monkeypatch.setattr(ast, "parse", forbidden)
    assert lifecycle_status(root, state_dir=state)["status"] == "NOT_INITIALIZED"
    assert not state.exists()
    receipt = record_changes(root, ["src/lib.py"], event_id="one", state_dir=state)
    assert receipt["status"] == "RECORDED"
    status = lifecycle_status(root, state_dir=state)
    assert status["pending_paths"] == ["src/lib.py"]
    assert status["current_worktree"] == "NOT_CHECKED_ON_READ"
    assert status["event_coverage"] == "BEST_EFFORT"


def test_root_isolation_bounded_coalescing_overflow_and_receipt_replay(project, tmp_path):
    root, state = project
    first = record_changes(root, ["src/lib.py"], event_id="one", state_dir=state)
    assert record_changes(root, ["src/lib.py"], event_id="one", state_dir=state)["event_seq"] == first["event_seq"]
    with pytest.raises(ValueError, match="different input"):
        record_changes(root, ["src/other.py"], event_id="one", state_dir=state)
    record_changes(root, [f"src/{i}.py" for i in range(1100)], state_dir=state)
    status = lifecycle_status(root, state_dir=state)
    assert status["pending_count"] == 1024
    assert status["overflow"] and len(status["pending_paths"]) == 64
    assert lifecycle_status(tmp_path / "other", state_dir=state)["status"] == "NOT_INITIALIZED"
    with pytest.raises(ValueError, match="escapes"):
        record_changes(root, ["../escape"], state_dir=state)
    with pytest.raises(ValueError, match="outside"):
        record_changes(root, ["src/lib.py"], state_dir=root / "state")


def test_noop_ack_observation_age_and_context_semantic_dedup(project, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C3 [AC-03]
    """
    root, state = project
    first = reconcile_repository(root, state_dir=state)
    observed = lifecycle_status(root, state_dir=state)
    record_changes(root, ["src/lib.py"], event_id="editor-saved-same-bytes", state_dir=state)
    dirty = context_hint(root, role="implementer", event="pre", files=("src/lib.py",), state_dir=state, timeout_ms=1000)
    assert dirty["coverage"]["current_worktree"] == "DIRTY_OBSERVED"
    def forbidden(*args, **kwargs):
        raise AssertionError("SHA-identical polling must not assemble or extract")
    monkeypatch.setattr(indexer, "_extract_matrix", forbidden)
    second = reconcile_repository(root, state_dir=state, reuse_snapshot=True)
    assert second["revision_seq"] == first["revision_seq"]
    assert second["index_run"]["strategy"] == "SOURCE_VERIFIED_NOOP"
    current = lifecycle_status(root, state_dir=state)
    assert current["status"] == "OBSERVED" and current["pending_count"] == 0
    assert current["last_observation_completed_at"] >= observed["last_observation_completed_at"]
    clean = context_hint(root, role="implementer", event="pre", files=("src/lib.py",), state_dir=state, timeout_ms=1000)
    assert clean["digest"] != dirty["digest"]
    assert clean["coverage"]["current_worktree"] == "NOT_CHECKED"
    reconcile_repository(root, state_dir=state, reuse_snapshot=True)
    again = context_hint(root, role="implementer", event="pre", files=("src/lib.py",), state_dir=state,
                         timeout_ms=1000, previous_digest=clean["digest"])
    assert again["status"] == "SILENT"


def test_concurrent_event_same_path_survives_old_cutoff(project, monkeypatch):
    root, state = project
    record_changes(root, ["src/lib.py"], event_id="before", state_dir=state)
    actual = indexer.index_repository
    def concurrent(*args, **kwargs):
        result = actual(*args, **kwargs)
        (root / "src/lib.py").write_text("def later(): return 2\n")
        record_changes(root, ["src/lib.py"], event_id="during", state_dir=state)
        return result
    monkeypatch.setattr(indexer, "index_repository", concurrent)
    receipt = reconcile_repository(root, state_dir=state)
    assert receipt["reconciliation"]["events_through"] == 1
    status = lifecycle_status(root, state_dir=state)
    assert status["reconciled_seq"] == 1 and status["event_seq"] == 2
    assert status["pending_paths"] == ["src/lib.py"]
    monkeypatch.setattr(indexer, "index_repository", actual)
    reconcile_repository(root, state_dir=state)
    assert lifecycle_status(root, state_dir=state)["status"] == "OBSERVED"


def test_failure_and_partial_keep_pending_and_previous_observation(project, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C3 [AC-05]
    """
    root, state = project
    reconcile_repository(root, state_dir=state)
    previous = lifecycle_status(root, state_dir=state)
    record_changes(root, ["src/lib.py"], state_dir=state)
    actual = indexer.index_repository
    def fail(*args, **kwargs):
        raise indexer.SourceChangedError("unstable")
    monkeypatch.setattr(indexer, "index_repository", fail)
    with pytest.raises(indexer.SourceChangedError):
        reconcile_repository(root, state_dir=state)
    assert lifecycle_status(root, state_dir=state)["last_result"] == "ERROR"
    monkeypatch.setattr(indexer, "index_repository", actual)
    (root / "src/lib.py").write_text("def invalid(\n")
    assert reconcile_repository(root, state_dir=state)["status"] == "PARTIAL"
    failed = lifecycle_status(root, state_dir=state)
    assert failed["pending_count"] == 1
    assert failed["last_observation_completed_at"] == previous["last_observation_completed_at"]
    (root / "src/lib.py").write_text("def repaired(): return 1\n")
    assert reconcile_repository(root, state_dir=state)["reconciliation"]["acknowledged"]


def test_expired_lease_recovers_but_stale_owner_cannot_ack(project, monkeypatch):
    root, state = project
    record_changes(root, ["src/lib.py"], state_dir=state)
    with _writer(root, state) as (_, values):
        values.update(owner="dead-process", lease_until=time.time() - 1)
    actual = indexer.index_repository
    def replaced(*args, **kwargs):
        result = actual(*args, **kwargs)
        with _writer(root, state) as (_, values):
            values.update(owner="new-process", lease_until=time.time() + 30)
        return result
    monkeypatch.setattr(indexer, "index_repository", replaced)
    result = reconcile_repository(root, state_dir=state)
    assert not result["reconciliation"]["acknowledged"]
    assert lifecycle_status(root, state_dir=state)["pending_count"] == 1
    assert reconcile_repository(root, state_dir=state)["status"] == "BUSY"


def test_periodic_worker_recovers_missing_events_and_singleton_closes(project):
    """@verifies SPEC-SPECGRAPH-C3 [AC-03]
    """
    root, state = project
    stop = threading.Event()
    worker = watch_repository(root, state_dir=state, interval_seconds=.1, stop_event=stop)
    try:
        first = next(worker)
        assert lifecycle_status(root, state_dir=state)["watcher_status"] == "ACTIVE"
        other = watch_repository(root, state_dir=state, interval_seconds=.1)
        assert next(other)["status"] == "BUSY"
        other.close()
        assert lifecycle_status(root, state_dir=state)["watcher_status"] == "ACTIVE"
        (root / "src/lib.py").rename(root / "src/renamed.py")
        second = next(worker)
        assert second["revision_seq"] == first["revision_seq"] + 1
        assert second["reconciliation"]["reason"] == "periodic_sha256"
        with RepositoryIndex(root, state_dir=state) as reader:
            assert reader.search("leaf")[0]["file_path"] == "src/renamed.py"
        (root / "src/renamed.py").unlink()
        assert next(worker)["coverage"]["symbol_count"] == 0
        stop.set()
        assert list(worker) == []
    finally:
        worker.close()
    assert lifecycle_status(root, state_dir=state)["watcher_status"] == "STOPPED_OR_UNOBSERVED"


def test_watch_finite_failure_backoff_and_input_validation(project, monkeypatch):
    root, state = project
    import specgraph.lifecycle as lifecycle
    calls = []
    def fail(*args, **kwargs):
        calls.append(time.monotonic())
        raise RuntimeError("temporarily unavailable")
    monkeypatch.setattr(lifecycle, "reconcile_repository", fail)
    attempts = list(watch_repository(root, state_dir=state, interval_seconds=.1, max_cycles=2))
    assert [result["status"] for result in attempts] == ["ERROR", "ERROR"]
    assert calls[1] - calls[0] >= .09
    for kwargs in ({"interval_seconds": float("nan")}, {"interval_seconds": 0}, {"max_cycles": 0}):
        with pytest.raises(ValueError):
            list(watch_repository(root, state_dir=state, **kwargs))


def test_noop_shortcut_still_rechecks_configuration_and_parser(project, monkeypatch):
    root, state = project
    first = reconcile_repository(root, state_dir=state)
    monkeypatch.setattr(indexer, "EXTRACTOR_VERSION", "new-parser-version")
    changed = reconcile_repository(root, state_dir=state, reuse_snapshot=True)
    assert changed["publication"] == "PUBLISHED" and changed["index_run"]["parsed_files"] > 0
    (root / "specgraph.yaml").write_text("indexed_paths: []\ngoverned_paths: []\n")
    changed = reconcile_repository(root, state_dir=state, reuse_snapshot=True)
    assert changed["coverage"]["symbol_count"] == 0
    assert changed["revision_seq"] > first["revision_seq"]
