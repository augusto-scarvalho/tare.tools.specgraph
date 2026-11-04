"""Run persisted-state compatibility checks against the imported distribution.

Artifacts are constructed protocol fixtures, not pretend installations of old
releases. This runner also works under ``python -I`` from a clean wheel venv.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile


def qualify(directory):
    from specgraph.cache import CacheManifest, IncrementalCacheEngine
    from specgraph.indexer import index_repository
    from specgraph.resolve import RECOVERY_INDEX, resolve_repository, runtime_directory
    from specgraph.storage import IndexUnavailableError, RepositoryIndex, index_path
    root, state = directory / "repository", directory / "state"
    (root / "src").mkdir(parents=True)
    source = root / "src/main.py"
    source.write_text("def original(): return 1\n", encoding="utf-8")
    note = root / "src/note.txt"
    note.write_text("MigrationNeedle original\n", encoding="utf-8")
    cache = IncrementalCacheEngine.save_manifest(CacheManifest(project_name="migration-protocol-fixture"), root)
    legacy_bytes = cache.read_bytes()
    previous = os.environ.get("TARE_SPECGRAPH_STATE_DIR")
    os.environ["TARE_SPECGRAPH_STATE_DIR"] = str(state)
    try:
        initial = index_repository(root)
        assert cache.read_bytes() == legacy_bytes
        assert IncrementalCacheEngine.load_manifest(root).schema_version == "1.0.0"
        assert index_repository(root)["revision_seq"] == initial["revision_seq"]
        # Old override location remains intact. Its unbound evidence is ignored.
        shared = state / RECOVERY_INDEX
        shared.write_text(json.dumps({"version": 1, "entries": {"untrusted": {"evidence": []}}}), encoding="utf-8")
        shared_bytes = shared.read_bytes()
        namespaced = runtime_directory(root) / RECOVERY_INDEX
        namespaced.parent.mkdir(parents=True, exist_ok=True)
        namespaced.write_text(shared.read_text(), encoding="utf-8")
        first = resolve_repository(root, "MigrationNeedle")
        assert first.source == "traditional"
        v2 = json.loads(namespaced.read_text(encoding="utf-8"))
        assert v2["version"] == 2 and v2["repository_root"] == os.path.normcase(str(root.resolve()))
        assert shared.read_bytes() == shared_bytes
        second = resolve_repository(root, "MigrationNeedle")
        assert second.source == "learned" and second.results[0]["result_type"] == "literal_text"
        assert second.results[0]["evidence_sha256"] == hashlib.sha256(note.read_bytes()).hexdigest()
        with RepositoryIndex(root) as old_reader:
            source.write_text("def replacement(): return 2\n", encoding="utf-8")
            with ThreadPoolExecutor(max_workers=1) as pool:
                published = pool.submit(index_repository, root).result(timeout=30)
            assert old_reader.search("original") and not old_reader.search("replacement")
            assert old_reader.metadata()["content_hash"] == initial["content_hash"]
        assert published["revision_seq"] == initial["revision_seq"] + 1
        assert resolve_repository(root, "MigrationNeedle").source == "traditional"
        with RepositoryIndex(root) as reader:
            assert reader.search("replacement") and not reader.search("original")
        # A format we do not implement is rejected, never silently stamped current.
        other = directory / "other"
        other.mkdir()
        foreign = index_path(other)
        foreign.parent.mkdir(parents=True)
        with closing(sqlite3.connect(index_path(root))) as src, closing(sqlite3.connect(foreign)) as dst:
            src.backup(dst)
            dst.execute("PRAGMA user_version=2")
        before = foreign.read_bytes()
        try:
            with RepositoryIndex(other):
                raise AssertionError("Unsupported schema unexpectedly opened")
        except IndexUnavailableError:
            pass
        assert foreign.read_bytes() == before
        with closing(sqlite3.connect(foreign)) as connection:
            connection.execute("PRAGMA user_version=1")
        try:
            with RepositoryIndex(other):
                raise AssertionError("Foreign repository unexpectedly opened")
        except IndexUnavailableError as error:
            assert "different repository" in str(error)
        assert cache.read_bytes() == legacy_bytes
        return {"status": "PASS", "artifact_authority": "constructed historical-format protocol fixtures",
                "checks": ["legacy cache JSON schema 1.0.0 readable and byte-preserved",
                           "recovery v1 shared location preserved and untrusted hints ignored",
                           "recovery v1 namespaced artifact replaced by validated v2",
                           "recovery v2 source type and digest preserved on reuse",
                           "new generation invalidates recovery v2",
                           "current SQLite schema 1 idempotent publication",
                           "concurrent publication preserves pinned reader and advances next reader",
                           "unsupported SQLite schema 2 rejected without rewriting database",
                           "foreign root current SQLite copy rejected"],
                "initial_revision": initial["revision_seq"], "current_revision": published["revision_seq"],
                "legacy_sha256": hashlib.sha256(legacy_bytes).hexdigest(),
                "limits": ["Not an upgrade from an installed historical release",
                           "SQLite schema 2 is deliberately unsupported, not a claimed migration target",
                           "No IDE/agent host reload is inferred from these checks"]}
    finally:
        if previous is None:
            os.environ.pop("TARE_SPECGRAPH_STATE_DIR", None)
        else:
            os.environ["TARE_SPECGRAPH_STATE_DIR"] = previous


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="specgraph-migration-qualification-") as directory:
        print(json.dumps(qualify(Path(directory)), indent=2))
