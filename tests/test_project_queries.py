from pathlib import Path
import os

import pytest

from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import index_repository
from specgraph.query import project_query, serialize_query
from specgraph.storage import RepositoryIndex


@pytest.fixture
def indexed_project(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/main.py").write_text('''def leaf():
    """Folha: ação útil."""
    return 1

def middle():
    return leaf()

def top():
    return middle()
''', encoding="utf-8")
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    return root, state, metadata


def test_code_queries_work_without_specs(indexed_project):
    root, state, metadata = indexed_project
    for query in ("leaf", "src/main.py:leaf", "src/main.py"):
        response = project_query(root, query, state_dir=state)
        assert response["results"]
        assert response["revision_seq"] == metadata["revision_seq"]
        assert response["content_hash"] == metadata["content_hash"]
        assert response["results"][0]["source"]["sha256"]
        assert response["results"][0]["linked_specs"] == []
    selected = project_query(root, "leaf", mode="symbol", state_dir=state)
    assert selected["results"][0]["start_line"] == 1
    assert selected["results"][0]["end_line"] == 3
    assert selected["results"][0]["docstring"] == "Folha: ação útil."


def test_refs_and_directed_paths_preserve_consumer_dependency_direction(indexed_project):
    root, state, _ = indexed_project
    refs = project_query(root, "leaf", mode="refs", state_dir=state)
    assert [(item["edge"]["from"], item["edge"]["to"]) for item in refs["results"]] == [
        ("src/main.py:middle", "src/main.py:leaf")]
    path = project_query(root, "top", mode="path", target="leaf", state_dir=state)
    assert [node["symbol_id"] for node in path["results"][0]["nodes"]] == [
        "src/main.py:top", "src/main.py:middle", "src/main.py:leaf"]
    reverse = project_query(root, "leaf", mode="path", target="top", state_dir=state)
    assert reverse["status"] == "UNKNOWN"
    assert not reverse["results"]
    explicit_reverse = project_query(root, "leaf", mode="path", target="top", direction="in", state_dir=state)
    assert explicit_reverse["results"][0]["nodes"][-1]["symbol_id"] == "src/main.py:top"


def test_neighbors_type_and_direction_filters(indexed_project):
    root, state, _ = indexed_project
    incoming = project_query(root, "middle", mode="neighbors", direction="in", state_dir=state)
    assert incoming["results"][0]["edge"]["from"].endswith(":top")
    outgoing = project_query(root, "middle", mode="neighbors", direction="out", state_dir=state)
    assert outgoing["results"][0]["edge"]["to"].endswith(":leaf")
    absent = project_query(root, "middle", mode="neighbors", relation="CODE_IMPLEMENTS_SPEC", state_dir=state)
    assert absent["results"] == []
    assert absent["absence"]["universal"] is False


def test_hot_queries_do_not_load_full_graph_or_current_sources(indexed_project, monkeypatch):
    root, state, _ = indexed_project
    (root / "src/main.py").write_text("def replaced(): pass\n", encoding="utf-8")
    def forbidden(*args, **kwargs):
        raise AssertionError("Hot query performed forbidden source/full-graph access")
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", forbidden)
    monkeypatch.setattr(RepositoryIndex, "snapshot_payload", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    for mode in ("search", "symbol", "refs", "neighbors", "path", "explain", "context"):
        result = project_query(root, "top" if mode == "path" else "leaf", mode=mode,
                               target="leaf" if mode == "path" else None, state_dir=state)
        assert result["results"]
        assert result["freshness"]["current_worktree"] == "NOT_CHECKED_ON_READ"
    context = project_query(root, "leaf", mode="context", state_dir=state)["results"][0]
    assert context["source_snippet"]["current_source_read"] is False
    if context["source_snippet"]["status"] == "AVAILABLE":
        assert "return 1" in context["source_snippet"]["body"]
        assert "replaced" not in context["source_snippet"]["body"]
    else:
        assert context["source_snippet"]["status"] == "NOT_STORED"


def test_byte_budget_counts_actual_utf8_and_omits_complete_optional_units(indexed_project):
    """@verifies SPEC-SPECGRAPH-C4
    """
    root, state, _ = indexed_project
    full = project_query(root, "src/main.py", max_bytes=65536, state_dir=state)
    wire = serialize_query(full)
    assert wire.endswith("\n")
    tight_budget = len(wire.encode("utf-8")) - 20
    bounded = project_query(root, "src/main.py", max_bytes=tight_budget, state_dir=state)
    assert len(serialize_query(bounded).encode("utf-8")) <= tight_budget
    assert bounded["status"] == "PARTIAL"
    assert any(item["reason"] == "max_bytes" for item in bounded["omitted"])
    assert len(bounded["results"]) < len(full["results"])
    assert all(item["start_line"] <= item["end_line"] for item in bounded["results"])


def test_required_context_is_never_truncated(indexed_project):
    """@verifies SPEC-SPECGRAPH-C4
    """
    root, state, _ = indexed_project
    (root / "src/main.py").write_text('def lengthy():\n    """' + "ação " * 1200 + '"""\n    return 1\n', encoding="utf-8")
    index_repository(root, state_dir=state)
    with pytest.raises(ValueError, match="Required context"):
        project_query(root, "lengthy", mode="context", max_bytes=4096, state_dir=state)
    response = project_query(root, "lengthy", mode="context", max_bytes=65536, state_dir=state)
    assert response["results"][0]["docstring"] == "ação " * 1200


def test_hop_node_limits_and_determinism(indexed_project):
    root, state, _ = indexed_project
    limited = project_query(root, "top", mode="path", target="leaf", max_hops=1, state_dir=state)
    assert limited["status"] == "UNKNOWN"
    assert any(item["reason"] == "max_hops" for item in limited["omitted"])
    capped = project_query(root, "top", mode="path", target="leaf", max_nodes=1, state_dir=state)
    assert capped["status"] == "UNKNOWN"
    assert any(item["reason"] == "max_nodes" for item in capped["omitted"])
    first = project_query(root, "top", mode="explain", state_dir=state)
    second = project_query(root, "top", mode="explain", state_dir=state)
    assert serialize_query(first) == serialize_query(second)
    singleton = project_query(root, "middle", mode="context", max_hops=0, state_dir=state)
    assert len(singleton["results"]) == 1


def test_document_lexical_fallback(indexed_project):
    root, state, _ = indexed_project
    (root / "notes").mkdir()
    (root / "notes/map.md").write_text("# Semantic cartography\nUnusual vocabulary used for project documentation.\n", encoding="utf-8")
    (root / "specgraph.yaml").write_text("search_paths: [notes]\n", encoding="utf-8")
    metadata = index_repository(root, state_dir=state)
    if not metadata["capabilities"]["fts5"]:
        pytest.skip("SQLite build has no FTS5")
    response = project_query(root, "cartography", state_dir=state)
    assert response["results"][0]["reason"] == "fts5_document_match"
    assert response["results"][0]["file_path"] == "notes/map.md"


@pytest.mark.parametrize("kwargs", [{"mode": "unknown"}, {"mode": "path"}, {"direction": "sideways"},
                                    {"max_nodes": 0}, {"max_hops": 11}, {"max_bytes": 1023}])
def test_invalid_queries_are_rejected(indexed_project, kwargs):
    root, state, _ = indexed_project
    with pytest.raises(ValueError):
        project_query(root, "leaf", state_dir=state, **kwargs)
