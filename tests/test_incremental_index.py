"""Content-bound extraction reuse, full equivalence and publication guards (C3)."""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import sqlite3

import pytest

from specgraph import indexer
from specgraph.graph import CausalTraceabilityMatrix, canonical_json
from specgraph.indexer import SourceChangedError, index_repository
from specgraph.storage import RepositoryIndex, index_path


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    files = {
        "specgraph.yaml": """governed_paths: [src]
indexed_paths: [src]
schema_paths: [schemas]
api_paths: [src]
spec_paths: [specs]
adr_paths: [adrs]
test_paths: [tests]
search_paths: [notes]
""",
        "src/lib.py": "def leaf():\n    return 1\n",
        "src/use.py": "from lib import leaf\ndef caller():\n    return leaf()\n",
        "src/empty.py": "# A parsed negative result is cached too.\n",
        "schemas/data.sql": "CREATE TABLE things (id INTEGER NOT NULL);\n",
        "specs/SPEC-ONE.md": "---\nid: SPEC-ONE\ntitle: One\ndate: 2026-09-08\nacceptance_criteria:\n  AC-01: Return a number\n---\n# Intent\n",
        "adrs/ADR-ONE.md": "---\nid: ADR-ONE\ntitle: One\ngoverned_specs: [SPEC-ONE]\n---\n# Decision\n",
        "tests/test_leaf.py": "def test_leaf():\n    assert True\n",
        "notes/search.md": "# Searchable knowledge\nStable snapshot\n",
    }
    for relative, body in files.items():
        file = root / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(body, encoding="utf-8")
    return root, tmp_path / "state"


def payload(root, state):
    with RepositoryIndex(root, state_dir=state) as reader:
        return reader.snapshot_payload()


def spy_extraction(monkeypatch):
    observed = []
    original = indexer._parse_extraction

    def record(root, config, path, extractor):
        observed.append((path, extractor))
        return original(root, config, path, extractor)

    monkeypatch.setattr(indexer, "_parse_extraction", record)
    return observed


def assert_full_equivalent(root, state, incremental_result):
    incremental_payload = payload(root, state)
    full = index_repository(root, state_dir=state, incremental=False)
    assert full["content_hash"] == incremental_result["content_hash"]
    assert full["publication"] == "UNCHANGED"
    assert payload(root, state) == incremental_payload
    # The independent legacy builder remains an additional semantic oracle.
    expected = json.loads(canonical_json(indexer._payload(CausalTraceabilityMatrix.build(root))))
    assert incremental_payload == expected


def test_noop_reuses_empty_and_nonempty_extractions_without_any_ast(project, monkeypatch):
    root, state = project
    first = index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("No-op indexing must not parse AST or use full builder")

    monkeypatch.setattr(ast, "parse", forbidden)
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", forbidden)
    second = index_repository(root, state_dir=state)
    assert calls == []
    assert second["publication"] == "UNCHANGED"
    assert second["revision_seq"] == first["revision_seq"]
    assert second["index_run"]["parsed_files"] == 0
    assert second["index_run"]["reused_extractions"] == first["index_run"]["parsed_extractions"]
    assert second["index_run"]["source_observation"] == "FULL_SHA256"


def test_same_size_same_mtime_edit_reparses_only_changed_file_and_matches_full(project, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C3
    """
    root, state = project
    first = index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)
    source = root / "src/lib.py"
    old = source.stat()
    source.write_text(source.read_text().replace("return 1", "return 2"), encoding="utf-8")
    os.utime(source, ns=(old.st_atime_ns, old.st_mtime_ns))
    assert source.stat().st_size == old.st_size
    updated = index_repository(root, state_dir=state)
    assert set(calls) == {("src/lib.py", "code"), ("src/lib.py", "api")}
    assert updated["index_run"]["parsed_files"] == 1
    assert updated["revision_seq"] == first["revision_seq"] + 1
    assert_full_equivalent(root, state, updated)


@pytest.mark.parametrize("mutation", ["delete", "rename", "add", "change_export"])
def test_changed_exports_rebind_unchanged_consumers_without_reparse(project, monkeypatch, mutation):
    root, state = project
    if mutation == "add":
        (root / "src/lib.py").unlink()
    index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)
    if mutation == "delete":
        (root / "src/lib.py").unlink()
    elif mutation == "rename":
        (root / "src/lib.py").rename(root / "src/renamed.py")
    elif mutation == "add":
        (root / "src/lib.py").write_text("def leaf(): return 2\n", encoding="utf-8")
    else:
        (root / "src/lib.py").write_text("def renamed(): return 2\n", encoding="utf-8")
    updated = index_repository(root, state_dir=state)
    assert not any(path == "src/use.py" for path, _ in calls)
    graph = payload(root, state)
    edges = [e for e in graph["edges"] if e["from"] == "src/use.py:caller"]
    assert bool(edges) == (mutation == "add")
    reference = next(s for s in graph["symbols"] if s["name"] == "caller")["references"][0]
    assert reference["status"] == ("resolved" if mutation == "add" else "unresolved")
    with RepositoryIndex(root, state_dir=state) as reader:
        stale = reader.connection.execute("SELECT file_path FROM extractions WHERE file_path='src/lib.py'").fetchall()
        if mutation in ("delete", "rename"):
            assert stale == []
            assert not any(s["symbol_id"] == "src/lib.py:leaf" for s in reader.search("leaf"))
    assert_full_equivalent(root, state, updated)


def test_config_and_extractor_version_invalidate_cached_extractions(project, monkeypatch):
    root, state = project
    first = index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)
    config = root / "specgraph.yaml"
    config.write_text(config.read_text() + "python_source_roots: ['.']\n", encoding="utf-8")
    changed = index_repository(root, state_dir=state)
    assert changed["index_run"]["reused_extractions"] == 0
    assert len(calls) == first["index_run"]["parsed_extractions"]
    assert_full_equivalent(root, state, changed)
    calls.clear()
    monkeypatch.setattr(indexer, "EXTRACTOR_VERSION", indexer.EXTRACTOR_VERSION + "-new")
    upgraded = index_repository(root, state_dir=state)
    assert upgraded["index_run"]["reused_extractions"] == 0
    assert len(calls) == first["index_run"]["parsed_extractions"]
    assert upgraded["content_hash"] != changed["content_hash"]
    assert_full_equivalent(root, state, upgraded)


def test_document_changes_do_not_reparse_code_and_remove_fts_residue(project, monkeypatch):
    root, state = project
    index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)
    (root / "notes/search.md").unlink()
    (root / "specs/SPEC-ONE.md").write_text("---\nid: SPEC-TWO\ntitle: Replacement\n---\nChanged intent\n", encoding="utf-8")
    updated = index_repository(root, state_dir=state)
    assert calls == [("specs/SPEC-ONE.md", "spec")]
    with RepositoryIndex(root, state_dir=state) as reader:
        assert not reader.connection.execute("SELECT 1 FROM documents WHERE file_path='notes/search.md'").fetchone()
        if updated["capabilities"]["fts5"]:
            assert not reader.search_documents("snapshot")
            assert reader.search_documents("replacement")
    assert_full_equivalent(root, state, updated)


def test_parse_errors_are_partial_retried_and_never_keep_previous_symbols(project, monkeypatch):
    root, state = project
    index_repository(root, state_dir=state)
    (root / "src/lib.py").write_text("def broken(:\n", encoding="utf-8")
    broken = index_repository(root, state_dir=state)
    assert broken["status"] == "PARTIAL"
    assert not any(s["name"] == "leaf" for s in payload(root, state)["symbols"])
    calls = spy_extraction(monkeypatch)
    retry = index_repository(root, state_dir=state)
    assert set(calls) == {("src/lib.py", "api"), ("src/lib.py", "code")}
    assert retry["publication"] == "UNCHANGED"
    assert retry["status"] == "PARTIAL"
    assert_full_equivalent(root, state, retry)


def test_corrupt_cached_payload_is_reextracted_without_losing_healthy_results(project, monkeypatch):
    root, state = project
    old = index_repository(root, state_dir=state)
    with sqlite3.connect(index_path(root, state_dir=state)) as connection:
        connection.execute("UPDATE extractions SET payload='not json' WHERE file_path='src/lib.py' AND extractor='code'")
    calls = spy_extraction(monkeypatch)
    repaired = index_repository(root, state_dir=state)
    assert calls == [("src/lib.py", "code")]
    assert repaired["index_run"]["invalid_cache_entries"] == 1
    assert repaired["content_hash"] == old["content_hash"]
    assert repaired["publication"] == "UNCHANGED"


def test_source_race_rolls_back_graph_and_extraction_cache(project, monkeypatch):
    root, state = project
    old = index_repository(root, state_dir=state)
    source = root / "src/lib.py"
    source.write_text("def leaf(): return 2\n", encoding="utf-8")
    original = indexer._parse_extraction

    def racing(root, config, path, extractor):
        value = original(root, config, path, extractor)
        if path == "src/lib.py" and extractor == "code":
            source.write_text("def racing(): return 3\n", encoding="utf-8")
        return value

    monkeypatch.setattr(indexer, "_parse_extraction", racing)
    with pytest.raises(SourceChangedError):
        index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.metadata()["content_hash"] == old["content_hash"]
        assert reader.search("leaf")
        stored_sha = reader.connection.execute("SELECT sha256 FROM files WHERE file_path='src/lib.py'").fetchone()[0]
        cache_sha = reader.connection.execute("SELECT source_sha256 FROM extractions WHERE file_path='src/lib.py' AND extractor='code'").fetchone()[0]
        assert stored_sha == cache_sha


def test_full_mode_bypasses_cache_without_changing_snapshot_identity(project, monkeypatch):
    root, state = project
    old = index_repository(root, state_dir=state)
    calls = spy_extraction(monkeypatch)
    full = index_repository(root, state_dir=state, incremental=False)
    assert len(calls) == old["index_run"]["parsed_extractions"]
    assert full["index_run"]["reused_files"] == 0
    assert full["index_run"]["strategy"] == "FULL"
    assert full["content_hash"] == old["content_hash"]
    assert full["revision_seq"] == old["revision_seq"]


def test_legacy_strict_cache_remains_independent(project):
    root, state = project
    from specgraph.cache import IncrementalCacheEngine
    matrix = CausalTraceabilityMatrix.build(root)
    matrix.sync_cache(root)
    legacy = root / ".specgraph/cache.json"
    original = legacy.read_bytes()
    (root / "src/lib.py").write_text("def leaf(): return 99\n", encoding="utf-8")
    index_repository(root, state_dir=state)
    assert legacy.read_bytes() == original
    assert IncrementalCacheEngine.detect_staleness(root).is_stale


def test_transient_aba_edit_cannot_bind_wrong_ast_to_observed_content(project, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C3
    """
    root, state = project
    from specgraph.code_parser import CodeParser
    source = root / "src/lib.py"
    expected = source.read_bytes()
    parse = CodeParser.parse_file
    observed_paths = []

    def mutate_original_while_parser_reads_staged(path, root_dir=".", **kwargs):
        observed_paths.append(Path(path))
        if Path(path).name == "lib.py":
            source.write_text("def transient_wrong_symbol(): return 99\n", encoding="utf-8")
            try:
                return parse(path, root_dir, **kwargs)
            finally:
                source.write_bytes(expected)
        return parse(path, root_dir, **kwargs)

    monkeypatch.setattr(CodeParser, "parse_file", mutate_original_while_parser_reads_staged)
    indexed = index_repository(root, state_dir=state)
    assert indexed["status"] == "READY"
    assert all(not path.is_relative_to(root) for path in observed_paths)
    with RepositoryIndex(root, state_dir=state) as reader:
        assert reader.search("leaf")
        assert not reader.search("transient_wrong_symbol")
        captured = reader.connection.execute("SELECT body FROM sources WHERE file_path='src/lib.py'").fetchone()[0]
        assert captured.encode("utf-8") == expected
