"""Offline X4 falsifiers: seeded violations are not ecosystem defect claims."""
import json
import ast
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3

import pytest

from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import index_repository
from specgraph.query import architecture_witness, project_query, serialize_query
from specgraph.storage import RepositoryIndex
from test_query_coverage import make_project


REFERENCES = {"contract_id": "fixture:forbidden-dependency", "adr_ref": "caller:ADR-057"}


def witness(root, state, source="top", target="leaf", **kwargs):
    return architecture_witness(root, source, target, state_dir=state, **(REFERENCES | kwargs))


@pytest.fixture
def project(tmp_path):
    return make_project(tmp_path, {"a.py": (
        "def leaf():\n    return 1\n"
        "def middle():\n    return leaf()\n"
        "def longer():\n    return middle()\n"
        "def top():\n    return middle() + longer()\n"
        "def direct():\n    return leaf() + top()\n"
        "def cycle():\n    return cycle() + top()\n"
        "def dynamic(callback):\n    return callback()\n")})


@pytest.mark.parametrize("source,expected", [
    ("direct", ["direct", "leaf"]),
    ("top", ["top", "middle", "leaf"]),
    ("cycle", ["cycle", "top", "middle", "leaf"]),
])
@pytest.mark.verifies("SPEC-SPECGRAPH-X4", "AC-01")
def test_supported_chain_matches_existing_path_oracle(project, source, expected):
    root, state, metadata = project
    result = witness(root, state, source, max_hops=5)
    oracle = project_query(root, source, mode="path", target="leaf", direction="out",
                           relation="DEPENDS_ON_STATIC", state_dir=state, max_hops=5)
    assert result["results"] == oracle["results"]
    assert result["architecture_witness"]["status"] == "SUPPORTED"
    assert [node["symbol_id"] for node in result["results"][0]["nodes"]] == [
        "src/a.py:" + name for name in expected]
    assert result["root"] == metadata["repository_root"]
    assert result["content_hash"] == metadata["content_hash"]
    assert result["revision_seq"] == metadata["revision_seq"]
    assert result["architecture_witness"]["adr_ref"] == REFERENCES["adr_ref"]
    assert result["architecture_witness"]["references"] == "CALLER_SUPPLIED"
    assert result["architecture_witness"]["authority"] == "NONE"
    assert result["architecture_witness"]["adr_read"] is False
    if source == "direct":
        nodes = result["results"][0]["nodes"]
        assert [(n["start_line"], n["end_line"]) for n in nodes] == [(9, 10), (1, 2)]
        assert all(n["source"]["sha256"] and Path(n["source"]["root"]) == Path(result["root"]) for n in nodes)
        edge = result["results"][0]["edges"][0]
        assert (edge["source_line"], edge["source_column"]) == (10, 11)
        assert (edge["origin"], edge["status"]) == ("python_ast", "resolved")


@pytest.mark.parametrize("source,target,kwargs", [
    ("leaf", "top", {}), ("top", "absent", {}), ("absent", "leaf", {}),
    ("dynamic", "leaf", {}), ("leaf", "leaf", {}),
    ("top", "leaf", {"max_hops": 1}), ("top", "leaf", {"max_nodes": 1}),
])
@pytest.mark.verifies("SPEC-SPECGRAPH-X4", "AC-02")
def test_no_path_unknown_and_cutoffs_never_mean_clean(project, source, target, kwargs):
    root, state, _ = project
    result = witness(root, state, source, target, **kwargs)
    assert result["architecture_witness"]["status"] == "INCONCLUSIVE"
    assert result["architecture_witness"]["minimality"] == "NOT_CLAIMED"
    assert result["coverage"]["semantic_completeness"] == "NOT_CLAIMED"


def test_homonyms_need_exact_rooted_identity(tmp_path):
    root, state, _ = make_project(tmp_path, {
        "a.py": "def leaf(): return 1\ndef top(): return leaf()\n",
        "b.py": "def leaf(): return 2\ndef top(): return leaf()\n"})
    for source, target in (("top", "leaf"), ("src/a.py:top", "leaf")):
        result = witness(root, state, source, target)
        assert result["status"] == "AMBIGUOUS"
        assert result["architecture_witness"]["status"] == "INCONCLUSIVE"
    result = witness(root, state, "src/b.py:top", "src/b.py:leaf")
    assert result["architecture_witness"]["status"] == "SUPPORTED"
    assert all(n["file_path"] == "src/b.py" for n in result["results"][0]["nodes"])


@pytest.mark.parametrize("damage", ["legacy", "inferred", "ambiguous", "unresolved", "missing_source",
                                     "bad_range", "missing_language", "ambiguous_node", "bad_column"])
def test_damaged_published_provenance_is_not_supported(project, damage):
    root, state, _ = project
    # Deliberate synthetic snapshot corruption, never a private boundary packet.
    with closing(sqlite3.connect(RepositoryIndex(root, state_dir=state).path)) as connection, connection:
        if damage == "missing_source":
            connection.execute("DELETE FROM sources")
        elif damage in ("bad_range", "missing_language", "ambiguous_node"):
            payload = json.loads(connection.execute(
                "SELECT payload FROM symbols WHERE symbol_id='src/a.py:leaf'").fetchone()[0])
            if damage == "bad_range":
                payload["end_line"] = 9999
            elif damage == "missing_language":
                payload.pop("language")
            else:
                payload["binding_status"] = "ambiguous"
            connection.execute("UPDATE symbols SET payload=? WHERE symbol_id='src/a.py:leaf'", (json.dumps(payload),))
        else:
            rows = connection.execute("SELECT rowid,payload FROM edges WHERE source='src/a.py:direct'").fetchall()
            for rowid, raw in rows:
                edge = json.loads(raw)
                if damage == "legacy":
                    edge.pop("origin")
                elif damage == "inferred":
                    edge["origin"] = "inferred"
                elif damage == "bad_column":
                    edge["source_column"] = 99999
                else:
                    edge["status"] = damage
                connection.execute("UPDATE edges SET payload=? WHERE rowid=?", (json.dumps(edge), rowid))
    assert witness(root, state, "direct")["architecture_witness"]["status"] == "INCONCLUSIVE"


def test_partial_graph_does_not_support_definite_violation(tmp_path):
    root, state, _ = make_project(tmp_path, {
        "a.py": "def leaf(): return 1\ndef top(): return leaf()\n", "bad.py": "def broken(:\n"})
    assert witness(root, state)["architecture_witness"]["status"] == "INCONCLUSIVE"


@pytest.mark.parametrize("key,value", [("contract_id", ""), ("adr_ref", " "), ("adr_ref", None),
                                      ("contract_id", "é" * 129), ("adr_ref", "bad\nref")])
def test_contract_references_are_small_nonempty_strings(project, key, value):
    root, state, _ = project
    with pytest.raises(ValueError, match=key):
        witness(root, state, **{key: value})


@pytest.mark.parametrize("kwargs", [{"max_nodes": 0}, {"max_hops": 11}, {"max_bytes": 1023}])
def test_existing_argument_checks_apply(project, kwargs):
    root, state, _ = project
    with pytest.raises(ValueError):
        witness(root, state, **kwargs)


def test_entire_utf8_response_budget_includes_contract_decoration(project):
    root, state, _ = project
    kwargs = {"contract_id": "é" * 100, "adr_ref": "界" * 80}
    full = witness(root, state, "direct", max_bytes=65536, **kwargs)
    size = len(serialize_query(full).encode("utf-8"))
    assert size > len(serialize_query(full))
    assert len(serialize_query(witness(root, state, "direct", max_bytes=size, **kwargs)).encode("utf-8")) <= size
    with pytest.raises(ValueError, match="max_bytes"):
        witness(root, state, "direct", max_bytes=size - 16, **kwargs)
    with pytest.raises(ValueError):
        witness(root, state, "direct", max_bytes=1024)


def test_read_only_no_sources_rebuild_network_or_model(project, monkeypatch):
    root, state, _ = project
    database = RepositoryIndex(root, state_dir=state).path
    before = database.read_bytes()
    (root / "src/a.py").unlink()  # Published observation survives source removal.
    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected source/rebuild/network/model access")
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", forbidden)
    monkeypatch.setattr(RepositoryIndex, "snapshot_payload", forbidden)
    monkeypatch.setattr("specgraph.indexer.index_repository", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    result = witness(root, state, "direct", adr_ref="nonexistent/private/ADR.md")
    assert result["architecture_witness"]["status"] == "SUPPORTED"
    assert result["freshness"]["current_worktree"] == "NOT_CHECKED_ON_READ"
    monkeypatch.undo()
    assert database.read_bytes() == before


@pytest.mark.verifies("SPEC-SPECGRAPH-X4", "AC-02")
def test_unused_forbidden_import_is_not_a_clean_architecture_claim(tmp_path):
    root, state, _ = make_project(tmp_path, {
        "core.py": "from relay import settle\ndef pure():\n    return 1\n",
        "relay.py": "def settle():\n    return 1\n",
    })
    source = (root / "src/core.py").read_text(encoding="utf-8")
    assert any(isinstance(node, ast.ImportFrom) and node.module == "relay" for node in ast.walk(ast.parse(source)))
    result = witness(root, state, "src/core.py:pure", "src/relay.py:settle")
    assert result["architecture_witness"]["status"] == "INCONCLUSIVE"


@pytest.mark.verifies("SPEC-SPECGRAPH-X4", "AC-01", "AC-02")
def test_publication_change_between_path_and_provenance_reads_is_inconclusive(project, monkeypatch):
    root, state, metadata = project

    def rotate(*args, **kwargs):
        result = project_query(*args, **kwargs)
        path = root / "src/a.py"
        path.write_text(path.read_text(encoding="utf-8") + "\n# Later publication\n", encoding="utf-8")
        current = index_repository(root, state_dir=state)
        assert current["content_hash"] != metadata["content_hash"]
        return result

    monkeypatch.setattr("specgraph.query.project_query", rotate)
    result = witness(root, state, "direct")
    assert result["content_hash"] == metadata["content_hash"]
    assert result["architecture_witness"]["status"] == "INCONCLUSIVE"
