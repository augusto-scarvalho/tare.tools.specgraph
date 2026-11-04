"""Retrieval witnesses, absence, migration, boundedness and declaration fidelity."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from specgraph import indexer
from specgraph.indexer import index_repository
from specgraph.query import project_query, serialize_query
from specgraph.resolve import resolve_repository
from specgraph.storage import RepositoryIndex, index_path

CASES = json.loads((Path(__file__).parents[1] / "tests/fixtures/historical/lexical-additional-cases.json").read_text(encoding="utf-8"))["cases"]


def project(tmp_path, source, *, intent=False):
    root, state = tmp_path / "repo", tmp_path / "state"
    (root / "src").mkdir(parents=True)
    (root / "src/code.py").write_text(source, encoding="utf-8")
    (root / "specgraph.yaml").write_text("project_name: fixture\nindexed_paths: [src]\nspec_paths: [docs/specs]\nadr_paths: [docs/adr]\n", encoding="utf-8")
    if intent:
        (root / "docs/specs").mkdir(parents=True)
        (root / "docs/adr").mkdir(parents=True)
        (root / "docs/specs/spec.md").write_text('---\nid: SPEC-A\ntitle: Invoice rule\nstatus: ACCEPTED\ntarget_symbols: ["src/code.py:leaf"]\nacceptance_criteria:\n  AC-01: Return forty-one.\n---\nInvoice checksum.\n', encoding="utf-8")
        (root / "docs/adr/adr.md").write_text('---\nid: ADR-A\ntitle: Billing decision\nstatus: ACCEPTED\ngoverned_specs: [SPEC-A]\n---\nIdempotent billing retries.\n', encoding="utf-8")
    metadata = index_repository(root, state_dir=state)
    assert metadata["capabilities"]["fts5"], "Qualification requires SQLite FTS5"
    return root, state


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["id"])
def test_frozen_additional_queries(case, tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-LEXICAL-INTENT:AC-01
    @verifies SPEC-SPECGRAPH-LEXICAL-INTENT:AC-02
    """
    root, state = project(tmp_path, case["source"], intent=case["id"] == "intent")
    monkeypatch.setenv("TARE_SPECGRAPH_STATE_DIR", str(state))
    response = project_query(root, case["query"], state_dir=state)
    definitions = [r for r in response["results"] if "name" in r]
    if case["expected"]:
        assert definitions[0]["name"] == case["expected"]
        assert definitions[0]["match"]["identity"] is False
        resolved = resolve_repository(root, case["query"], fallback_probe=lambda *a: pytest.fail("Unexpected fallback"))
        assert resolved.source == "specgraph" and resolved.results[0]["name"] == case["expected"]
    else:
        assert not response["results"]
        assert response["absence"]["universal"] is False


def test_exact_identity_dominates_docstrings_and_structural_selectors_stay_literal(tmp_path):
    root, state = project(tmp_path, 'def rotate(): return 1\ndef distractor():\n    """rotate rotate rotate credentials"""\n    return 2\n')
    result = project_query(root, "rotate", state_dir=state)
    assert [r["name"] for r in result["results"]] == ["rotate"]
    assert result["results"][0]["reason"] == "exact_name"
    assert not project_query(root, "rotate credentials", mode="symbol", state_dir=state)["results"]
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.search("rot")[0]["name"] == "rotate"  # Legacy literal API.
    assert not project_query(root, "rotateCredentialsAbsent", state_dir=state)["results"]


def test_hot_lexical_and_intent_reads_use_postings_and_no_sources(tmp_path, monkeypatch):
    root, state = project(tmp_path, 'def leaf():\n    """Invoice checksum."""\n    return 41\n', intent=True)
    statements = []
    enter = RepositoryIndex.__enter__
    def traced(self):
        result = enter(self)
        self.connection.set_trace_callback(statements.append)
        return result
    def forbidden(*args, **kwargs):
        raise AssertionError("Hot path accessed source, legacy scan or entire graph")
    monkeypatch.setattr(RepositoryIndex, "__enter__", traced)
    monkeypatch.setattr(RepositoryIndex, "search", forbidden)
    monkeypatch.setattr(RepositoryIndex, "snapshot_payload", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    for query in ("invoice checksum", "idempotent billing retries", "leaf", "src/code.py"):
        assert project_query(root, query, state_dir=state)["results"][0]["name"] == "leaf"
    assert any("symbol_fts MATCH" in s for s in statements)
    assert not any("instr(" in s or "FROM revisions" in s for s in statements)


@pytest.mark.parametrize("origin", ["frontmatter", "docstring", "mapping", "rejected_mapping"])
def test_published_intent_in_all_projections_preserves_provenance(tmp_path, origin):
    """@verifies SPEC-SPECGRAPH-LEXICAL-INTENT:AC-03
    @verifies SPEC-SPECGRAPH-LEXICAL-INTENT:AC-04
    """
    root, state = project(tmp_path, 'def leaf():\n    return 41\n', intent=True)
    spec = root / "docs/specs/spec.md"
    if origin != "frontmatter":
        spec.write_text(spec.read_text().replace('target_symbols: ["src/code.py:leaf"]', 'target_symbols: []'), encoding="utf-8")
    if origin == "docstring":
        (root / "src/code.py").write_text('def leaf():\n    """@spec SPEC-A"""\n    return 41\n', encoding="utf-8")
    if "mapping" in origin:
        cfg = root / "specgraph.yaml"
        cfg.write_text(cfg.read_text() + 'intent_mapping_paths: [map.json]\n', encoding="utf-8")
        (root / "map.json").write_text(json.dumps({"schema": "tare.tools/traceability-mappings/1", "authority": "NONE", "mappings": [{
            "source_ref": ("foreign" if origin == "rejected_mapping" else "fixture") + ":docs/specs/spec.md",
            "source_sha256": hashlib.sha256(spec.read_bytes()).hexdigest(), "criteria": ["AC-01"],
            "target_kind": "code", "target_id": "fixture:src/code.py:leaf", "rationale": "Exact declaration", "test_refs": []}]}), encoding="utf-8")
    index_repository(root, state_dir=state)
    for mode in ("search", "symbol", "context"):
        result = project_query(root, "leaf", mode=mode, state_dir=state)
        assert result["results"][0]["linked_specs"] == ([] if origin == "rejected_mapping" else ["SPEC-A"])
        paths = [r for r in result["results"] if r.get("kind") == "intent_context"]
        if origin == "rejected_mapping":
            assert not paths
        else:
            assert paths and paths[0]["edges"][0]["to"] == "SPEC-A"
            assert any(e["to"] == "ADR-A" for e in paths[0]["edges"])
            assert all(d["source"]["sha256"] for d in paths[0]["documents"])
            if origin == "mapping":
                assert paths[0]["edges"][0]["source_sha256"] == hashlib.sha256(spec.read_bytes()).hexdigest()
    retrieved = project_query(root, "idempotent billing retries", state_dir=state)
    code = [r for r in retrieved["results"] if "name" in r]
    assert bool(code) is (origin != "rejected_mapping")


def test_expansion_hops_results_and_bytes_are_bounded(tmp_path):
    root, state = project(tmp_path, "def leaf(): return 41\n", intent=True)
    for hops in (0, 1):
        result = project_query(root, "idempotent billing retries", max_hops=hops, state_dir=state)
        assert not any("name" in r for r in result["results"])
    assert project_query(root, "idempotent billing retries", max_hops=2, state_dir=state)["results"][0]["name"] == "leaf"
    full = project_query(root, "leaf", state_dir=state)
    budget = len(serialize_query(full).encode()) - 10
    small = project_query(root, "leaf", state_dir=state, max_bytes=budget)
    assert small["results"][0]["name"] == "leaf"
    assert small["results"][0]["linked_specs"] == ["SPEC-A"]
    assert len(serialize_query(small).encode()) <= budget
    assert any(o["reason"] == "max_bytes" for o in small["omitted"])
    singleton = project_query(root, "leaf", limit=1, max_nodes=1, state_dir=state)
    assert len(singleton["results"]) == 1
    assert any(o["reason"] == "intent_context_limit" for o in singleton["omitted"])
    node_limited = project_query(root, "idempotent billing retries", max_nodes=2, state_dir=state)
    assert not any("name" in r for r in node_limited["results"])
    assert any(o["reason"] == "intent_node_limit" for o in node_limited["omitted"])
    narrow = project_query(root, "idempotent billing retries", max_nodes=3, state_dir=state)
    graph_nodes = set()
    for r in narrow["results"]:
        for edge in r.get("via_edges", []) + r.get("edges", []):
            graph_nodes.update((edge["from"], edge["to"]))
    assert len(graph_nodes) <= 3


def test_projection_upgrade_is_explicit_and_atomic(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-LEXICAL-INTENT:AC-05"""
    root, state = project(tmp_path, 'def renewCredentials():\n    """Renew expired credentials."""\n    return 1\n')
    path = index_path(root, state_dir=state)
    with sqlite3.connect(path) as db:
        metadata = json.loads(db.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()[0])
        metadata.pop("retrieval_projection_version")
        metadata["content_hash"] = "legacy"
        db.execute("UPDATE metadata SET value=? WHERE key='snapshot'", (json.dumps(metadata),))
    legacy = project_query(root, "expired credentials", state_dir=state)
    assert legacy["status"] == "UNKNOWN" and legacy["omitted"][0]["reason"] == "REINDEX_REQUIRED"
    from specgraph.capabilities import project_doctor
    assert project_doctor(root, state_dir=state)["retrieval_projection"]["status"] == "REINDEX_REQUIRED"
    assert project_query(root, "renewCredentials", state_dir=state)["results"]
    publish = indexer._publish
    def fail(*args, **kwargs):
        publish(*args, **kwargs)
        raise RuntimeError("interrupted projection")
    monkeypatch.setattr(indexer, "_publish", fail)
    with pytest.raises(RuntimeError):
        index_repository(root, state_dir=state, reuse_snapshot=True)
    with RepositoryIndex(root, state_dir=state) as old:
        assert old.metadata()["content_hash"] == "legacy"
        monkeypatch.setattr(indexer, "_publish", publish)
        current = index_repository(root, state_dir=state, reuse_snapshot=True)
        assert old.metadata()["content_hash"] == "legacy"
    assert current["publication"] == "PUBLISHED"
    assert project_doctor(root, state_dir=state)["retrieval_projection"]["status"] == "AVAILABLE"
    assert project_query(root, "expired credentials", state_dir=state)["results"][0]["name"] == "renewCredentials"
    assert index_repository(root, state_dir=state, reuse_snapshot=True)["publication"] == "UNCHANGED"


def test_fts_unavailable_and_candidate_truncation_are_explicit(tmp_path, monkeypatch):
    source = "\n".join(f'def f{i}():\n    """Expire session credentials."""\n    return {i}\n' for i in range(110))
    root, state = project(tmp_path, source)
    result = project_query(root, "expire credentials", limit=5, state_dir=state)
    assert len(result["results"]) == 5
    assert any(o["reason"] == "lexical_candidate_limit" for o in result["omitted"])
    initialize = indexer._initialize
    def no_fts(connection):
        initialize(connection)
        return False
    monkeypatch.setattr(indexer, "_initialize", no_fts)
    index_repository(root, state_dir=state)
    unavailable = project_query(root, "expire credentials", state_dir=state)
    assert unavailable["status"] == "UNKNOWN"
    assert unavailable["omitted"][0]["reason"] == "FTS5_UNAVAILABLE"
    assert project_query(root, "f0", state_dir=state)["results"]


def test_normalized_postings_follow_rename_delete_and_docstring_edit(tmp_path):
    root, state = project(tmp_path, 'def oldName():\n    """Renew expired credentials."""\n    return 1\n')
    path = root / "src/code.py"
    path.write_text('def newName():\n    """Archive completed invoices."""\n    return 2\n', encoding="utf-8")
    index_repository(root, state_dir=state)
    assert not project_query(root, "expired credentials", state_dir=state)["results"]
    assert project_query(root, "archive invoices", state_dir=state)["results"][0]["name"] == "newName"
    path.unlink()
    index_repository(root, state_dir=state)
    assert not project_query(root, "archive invoices", state_dir=state)["results"]
