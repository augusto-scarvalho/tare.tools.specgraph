from __future__ import annotations
import json
import time
from pathlib import Path
import pytest

from specgraph.config import SpecGraphConfig
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.cache import IncrementalCacheEngine, CacheManifest, StalenessReport
from specgraph.cli import main

class TestCacheAndStaleness:
    def _setup_project(self, root: Path) -> None:
        (root / "specs").mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "adr").mkdir(parents=True, exist_ok=True)

        # ADR
        (root / "docs" / "adr" / "ADR-001.md").write_text("""---
id: ADR-001
title: Core Architecture
status: ACCEPTED
governed_specs:
  - SPEC-001
---
""", encoding="utf-8")

        # Spec
        (root / "specs" / "SPEC-001.md").write_text("""---
id: SPEC-001
title: Auth Engine
status: ACCEPTED
implements:
  - REQ-01
acceptance_criteria:
  AC-01: Issue token
---
""", encoding="utf-8")

        # Code
        (root / "src" / "auth.py").write_text('''
def generate_token():
    """@spec SPEC-001 [AC-01]"""
    return "tok_123"
''', encoding="utf-8")

        # Test
        (root / "tests" / "test_auth.py").write_text('''
import pytest

@pytest.mark.verifies("SPEC-001", "AC-01")
def test_generate_token():
    assert True
''', encoding="utf-8")

    def test_initial_staleness_on_unindexed_project(self, tmp_path: Path):
        self._setup_project(tmp_path)
        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert report.is_stale is True
        assert len(report.added_files) == 4
        assert report.checked_files_count == 4

    def test_sync_cache_creates_auditable_manifest(self, tmp_path: Path):
        self._setup_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        manifest = IncrementalCacheEngine.sync_cache(matrix, root_dir=tmp_path)

        cache_file = tmp_path / ".specgraph" / "cache.json"
        assert cache_file.exists()

        raw = json.loads(cache_file.read_text(encoding="utf-8"))
        assert raw["schema_version"] == "1.0.0"
        assert len(raw["generation_digest"]) == 64
        assert "specs/SPEC-001.md" in raw["files"]
        assert "src/auth.py" in raw["files"]
        assert "SPEC-001" in raw["files"]["specs/SPEC-001.md"]["extracted_nodes"]

        # Causal reverse deps are tracked
        assert "SPEC-001" in raw["reverse_deps"]

    def test_staleness_check_is_fast_and_fresh_after_sync(self, tmp_path: Path):
        self._setup_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "FRESH"
        assert report.is_stale is False
        assert len(report.modified_files) == 0
        assert len(report.added_files) == 0
        assert len(report.deleted_files) == 0
        assert report.duration_ms < 50.0  # sub-50ms performance guarantee

    def test_detect_modified_file_and_causal_blast_radius(self, tmp_path: Path):
        self._setup_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Mutate spec file
        time.sleep(0.01)
        (tmp_path / "specs" / "SPEC-001.md").write_text("""---
id: SPEC-001
title: Auth Engine Modified
status: ACCEPTED
acceptance_criteria:
  AC-01: Issue token
---
""", encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert report.is_stale is True
        assert "specs/SPEC-001.md" in report.modified_files
        # Blast radius must include SPEC-001 and downstream symbols/tests
        assert "SPEC-001" in report.impacted_nodes

    def test_detect_added_and_deleted_files(self, tmp_path: Path):
        self._setup_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Add new schema
        (tmp_path / "src" / "new_schema.sql").write_text("-- @spec SPEC-001\nCREATE TABLE t (id INT);", encoding="utf-8")

        # Delete auth code
        (tmp_path / "src" / "auth.py").unlink()

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "src/new_schema.sql" in report.added_files
        assert "src/auth.py" in report.deleted_files
        assert "src/auth.py:generate_token" in report.impacted_nodes

    def test_cli_check_stale_and_sync_commands(self, tmp_path: Path, capsys):
        self._setup_project(tmp_path)

        # 1. Stale before sync
        code_stale = main(["--root", str(tmp_path), "check-stale", "--format", "json"])
        assert code_stale == 1
        cap = capsys.readouterr().out
        assert '"status": "STALE"' in cap

        # 2. Sync cache
        code_sync = main(["--root", str(tmp_path), "sync-cache"])
        assert code_sync == 0
        cap_sync = capsys.readouterr().out
        assert "SYNCED" in cap_sync

        # 3. Fresh after sync
        code_fresh = main(["--root", str(tmp_path), "check-stale"])
        assert code_fresh == 0
        cap_fresh = capsys.readouterr().out
        assert "[FRESH]" in cap_fresh


@pytest.mark.verifies("SPEC-SPECGRAPH-C3", "AC-01", "AC-04")
def test_slow_manifest_writer_queues_threads_per_cache(tmp_path, monkeypatch):
    """A local queue must not consume the finite external-lock wait budget."""
    import concurrent.futures
    import threading
    import specgraph.cache as cache

    entered = threading.Event()
    release = threading.Event()
    queued = threading.Event()
    original_fsync = cache.os.fsync
    destination = IncrementalCacheEngine.get_cache_path(tmp_path)

    class ObservedLock:
        def __init__(self):
            self.lock = threading.Lock()

        def __enter__(self):
            if not self.lock.acquire(blocking=False):
                queued.set()
                self.lock.acquire()
            return self

        def __exit__(self, *args):
            self.lock.release()

    local_lock = ObservedLock()
    with cache._manifest_locks_guard:
        cache._manifest_locks[destination] = local_lock

    def slow_fsync(fd):
        if threading.current_thread().name.startswith("slow-writer"):
            entered.set()
            assert release.wait(10), "controller did not release slow writer"
        return original_fsync(fd)

    monkeypatch.setattr(cache.os, "fsync", slow_fsync)
    with concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="slow-writer") as slow:
        with concurrent.futures.ThreadPoolExecutor(2) as others:
            first = slow.submit(IncrementalCacheEngine.save_manifest, CacheManifest(project_name="first"), tmp_path)
            try:
                assert entered.wait(5)
                second = others.submit(IncrementalCacheEngine.save_manifest, CacheManifest(project_name="second"), tmp_path / ".")
                assert queued.wait(5), "same canonical destination did not queue"
                independent = others.submit(IncrementalCacheEngine.save_manifest, CacheManifest(project_name="independent"), tmp_path / "other")
                independent.result(timeout=5)
                # Exceed the unchanged 50 * 20ms external contention window.
                assert not release.wait(1.2)
                assert not second.done()
                assert (destination.parent / "sync.lock").exists()
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)
    assert IncrementalCacheEngine.load_manifest(tmp_path).project_name == "second"
    assert not (destination.parent / "sync.lock").exists()
    assert not list(destination.parent.glob("*.tmp"))


@pytest.mark.parametrize("operation", ["write", "fsync", "replace"])
def test_manifest_write_error_releases_resources(tmp_path, monkeypatch, operation):
    import os
    import specgraph.cache as cache

    destination = IncrementalCacheEngine.save_manifest(CacheManifest(project_name="original"), tmp_path)
    original_bytes = destination.read_bytes()
    original = getattr(cache.os, operation)
    descriptors = []

    def fail(*args, **kwargs):
        if operation == "write":
            descriptors.append(args[0])
        raise OSError("injected manifest failure")

    with monkeypatch.context() as patch:
        patch.setattr(cache.os, operation, fail)
        with pytest.raises(OSError, match="injected manifest failure"):
            IncrementalCacheEngine.save_manifest(CacheManifest(project_name="failed"), tmp_path)
    assert destination.read_bytes() == original_bytes
    assert not (destination.parent / "sync.lock").exists()
    assert not list(destination.parent.glob("*.tmp"))
    for fd in descriptors:
        with pytest.raises(OSError):
            os.fstat(fd)
    # Another thread also proves the local mutex was released on failure.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        pool.submit(IncrementalCacheEngine.save_manifest, CacheManifest(project_name="recovered"), tmp_path).result(timeout=5)
    assert IncrementalCacheEngine.load_manifest(tmp_path).project_name == "recovered"


def test_manifest_refuses_live_external_lock(tmp_path):
    import subprocess
    import sys

    cache_dir = tmp_path / ".specgraph"
    cache_dir.mkdir()
    lock = cache_dir / "sync.lock"
    source = "import os, pathlib, sys; p=pathlib.Path(sys.argv[1]); fd=os.open(p, os.O_CREAT|os.O_EXCL|os.O_RDWR); os.write(fd, str(os.getpid()).encode()); os.close(fd); print('ready', flush=True); sys.stdin.read(); p.unlink()"
    compile(source, "external-lock-holder", "exec")
    process = subprocess.Popen([sys.executable, "-u", "-c", source, str(lock)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        owner = lock.read_bytes()
        with pytest.raises(TimeoutError, match="Could not acquire sync.lock"):
            IncrementalCacheEngine.save_manifest(CacheManifest(), tmp_path)
        assert process.poll() is None
        assert lock.read_bytes() == owner
    finally:
        stdout, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    assert not lock.exists()
    IncrementalCacheEngine.save_manifest(CacheManifest(), tmp_path)
