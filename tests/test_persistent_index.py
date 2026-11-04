"""Publication/snapshot invariants, including counterexamples to stat-only freshness."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import shutil
import sqlite3

import pytest

from specgraph import indexer
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import SourceChangedError, index_repository
from specgraph.storage import IndexUnavailableError, RepositoryIndex, index_path


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/example.py").write_text('def alpha():\n    """Example symbol."""\n    return 1\n', encoding="utf-8")
    (root / "specgraph.yaml").write_text("strict_orphan_check: true\nsearch_paths: [notes]\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes/overview.md").write_text("# Project map\nTransactional snapshots protect concurrent readers.\n", encoding="utf-8")
    return root, tmp_path / "state"


def test_publication_search_and_governance_are_separate(repository):
    """@verifies SPEC-SPECGRAPH-C2
    """
    root, state = repository
    published = index_repository(root, state_dir=state)
    assert published["revision_seq"] == 1
    assert published["status"] == "READY"
    assert published["coverage"]["status"] == "COMPLETE_WITHIN_SCOPE"
    assert published["governance"]["status"] == "FAIL"  # Orphan code remains searchable.
    assert published["freshness"]["current_worktree"] == "NOT_CHECKED_ON_READ"
    with RepositoryIndex(root, state_dir=state) as reader:
        result = reader.search("alpha")[0]
        assert result == {"symbol_id": "src/example.py:alpha", "name": "alpha", "kind": "function",
                          "file_path": "src/example.py", "start_line": 1, "end_line": 3,
                          "linked_specs": [], "score": 400}
        assert reader.search("example.py")[0]["score"] == 100
        assert reader.metadata()["content_hash"] == published["content_hash"]
        assert reader.snapshot_payload()["symbols"][0]["name"] == "alpha"
        assert reader.neighbors(result["symbol_id"]) == []
        if published["capabilities"]["fts5"]:
            assert reader.search_documents("transactional")[0]["file_path"] == "notes/overview.md"
        assert reader.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            reader.connection.execute("DELETE FROM symbols")
    assert not (root / ".specgraph/cache.json").exists()


def test_queries_do_not_build_or_read_source(repository, monkeypatch):
    root, state = repository
    index_repository(root, state_dir=state)
    shutil.rmtree(root / "src")
    def prohibited(*args, **kwargs):
        raise AssertionError("Query attempted source access or graph rebuilding")
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", prohibited)
    monkeypatch.setattr(Path, "read_bytes", prohibited)
    monkeypatch.setattr(Path, "read_text", prohibited)
    monkeypatch.setattr(os, "scandir", prohibited)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.search("alpha")
        assert reader.snapshot_payload()["symbols"]
        assert reader.metadata()["freshness"]["current_worktree"] == "NOT_CHECKED_ON_READ"


def test_identical_content_is_idempotent_but_same_size_edit_changes_hash(repository):
    root, state = repository
    original = index_repository(root, state_dir=state)
    source = root / "src/example.py"
    original_stat = source.stat()
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000))
    unchanged = index_repository(root, state_dir=state)
    assert unchanged["publication"] == "UNCHANGED"
    assert unchanged["revision_seq"] == original["revision_seq"]
    source.write_text(source.read_text().replace("return 1", "return 2"), encoding="utf-8")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    changed = index_repository(root, state_dir=state)
    assert changed["revision_seq"] == original["revision_seq"] + 1
    assert changed["content_hash"] != original["content_hash"]
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.connection.execute("SELECT count(*) FROM revisions").fetchone()[0] == 1


def test_reader_snapshot_survives_concurrent_publication(repository):
    """@verifies SPEC-SPECGRAPH-C2
    """
    root, state = repository
    old = index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as reader:
        (root / "src/example.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        with ThreadPoolExecutor(max_workers=1) as pool:
            new = pool.submit(index_repository, root, state_dir=state).result(timeout=10)
        assert new["revision_seq"] == old["revision_seq"] + 1
        assert reader.metadata()["revision_seq"] == old["revision_seq"]
        assert reader.search("alpha") and not reader.search("beta")
        with RepositoryIndex(root, state_dir=state) as newer_reader:
            assert newer_reader.metadata()["revision_seq"] == new["revision_seq"]
            assert newer_reader.search("beta") and not newer_reader.search("alpha")


def test_failure_after_table_replacement_rolls_back(repository, monkeypatch):
    root, state = repository
    old = index_repository(root, state_dir=state)
    (root / "src/example.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    publish = indexer._publish
    def fail_after_write(*args, **kwargs):
        publish(*args, **kwargs)
        raise RuntimeError("simulated disk/publication failure")
    monkeypatch.setattr(indexer, "_publish", fail_after_write)
    with pytest.raises(RuntimeError, match="simulated"):
        index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.metadata()["content_hash"] == old["content_hash"]
        assert reader.search("alpha") and not reader.search("beta")


def test_parse_failure_publishes_partial_without_reusing_old_symbol(repository):
    root, state = repository
    old = index_repository(root, state_dir=state)
    (root / "src/example.py").write_text("def broken(:\n", encoding="utf-8")
    new = index_repository(root, state_dir=state)
    assert new["revision_seq"] == old["revision_seq"] + 1
    assert new["status"] == "PARTIAL"
    assert new["coverage"]["errors"]
    with RepositoryIndex(root, state_dir=state) as reader:
        assert not reader.search("alpha")


def test_source_mutation_during_build_does_not_publish(repository, monkeypatch):
    root, state = repository
    old = index_repository(root, state_dir=state)
    build = indexer._extract_matrix
    def mutate_after_build(*args, **kwargs):
        matrix = build(*args, **kwargs)
        (root / "src/example.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
        return matrix
    monkeypatch.setattr(indexer, "_extract_matrix", mutate_after_build)
    with pytest.raises(SourceChangedError):
        index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.metadata()["revision_seq"] == old["revision_seq"]
        assert reader.search("alpha")


def test_root_namespace_and_identity_guard(repository):
    root, state = repository
    index_repository(root, state_dir=state)
    other = root.parent / "other"
    other.mkdir()
    assert index_path(root, state_dir=state) != index_path(other, state_dir=state)
    wrong_path = index_path(other, state_dir=state)
    wrong_path.parent.mkdir(parents=True)
    shutil.copy2(index_path(root, state_dir=state), wrong_path)
    with pytest.raises(IndexUnavailableError, match="different repository"):
        with RepositoryIndex(other, state_dir=state):
            pass
    with pytest.raises(IndexUnavailableError, match="different repository"):
        index_repository(other, state_dir=state)


def test_missing_index_does_not_create_state(repository):
    root, state = repository
    with pytest.raises(IndexUnavailableError, match="No published index"):
        with RepositoryIndex(root, state_dir=state):
            pass
    assert not state.exists()


def test_config_bytes_affect_snapshot_hash(repository):
    root, state = repository
    old = index_repository(root, state_dir=state)
    config = root / "specgraph.yaml"
    config.write_text(config.read_text() + "# observation identity includes source bytes\n", encoding="utf-8")
    new = index_repository(root, state_dir=state)
    assert new["content_hash"] != old["content_hash"]


def test_fts_unavailable_is_reported_and_literal_search_still_works(repository, monkeypatch):
    root, state = repository
    initialize = indexer._initialize
    def initialize_without_fts(connection):
        initialize(connection)
        return False
    monkeypatch.setattr(indexer, "_initialize", initialize_without_fts)
    published = index_repository(root, state_dir=state)
    assert published["capabilities"]["document_search"] == "UNAVAILABLE_FTS5"
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.search("alpha")
        with pytest.raises(IndexUnavailableError, match="FTS5"):
            reader.search_documents("transactional")


def test_literal_search_handles_sql_and_fts_punctuation(repository):
    root, state = repository
    index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.search("%' OR 1=1 --") == []
        assert reader.search("src/example.py:alpha")
        for query, limit in (("", 20), ("x" * 513, 20), ("alpha", 0), ("alpha", 101)):
            with pytest.raises(ValueError):
                reader.search(query, limit=limit)


def test_bad_document_is_reported_without_dropping_good_documents(repository):
    root, state = repository
    (root / "notes/bad.md").write_bytes(b"\xffinvalid UTF8")
    result = index_repository(root, state_dir=state)
    assert result["status"] == "PARTIAL"
    assert any("notes/bad.md" in error for error in result["coverage"]["errors"])
    with RepositoryIndex(root, state_dir=state) as reader:
        if result["capabilities"]["fts5"]:
            assert reader.search_documents("transactional")


def test_concurrent_identical_writers_publish_one_revision(repository):
    root, state = repository
    index_repository(root, state_dir=state)
    (root / "src/example.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(index_repository, root, state_dir=state) for _ in range(2)]
        results = [future.result(timeout=10) for future in pending]
    assert {item["revision_seq"] for item in results} == {2}
    assert {item["publication"] for item in results} == {"UNCHANGED", "PUBLISHED"}
