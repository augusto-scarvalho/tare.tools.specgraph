"""Exact declarations, source identity and full/incremental invalidation."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from specgraph.cache import IncrementalCacheEngine
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import _payload, index_repository
from specgraph.storage import RepositoryIndex


@pytest.fixture
def mapped(tmp_path):
    root = tmp_path / "repo"
    for folder in ("src", "docs", "planning"):
        (root / folder).mkdir(parents=True)
    (root / "specgraph.yaml").write_text("project_name: fixture\nspec_paths: [docs]\nadr_paths: []\nindexed_paths: [src]\ngoverned_paths: [src]\nschema_paths: [src]\napi_paths: []\ntest_paths: []\nintent_mapping_paths: [planning/map.json]\n")
    doc = root / "docs/spec.md"
    doc.write_text("---\nid: SPEC-A\ntitle: Exact declaration\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-01: Preserve identity\n---\n# Exact body\n", newline="\n")
    (root / "src/code.py").write_text("from dataclasses import dataclass\n\n@dataclass\nclass Service:\n    value: int\n    def run(self):\n        return self.value\n\ndef other():\n    return 0\n", newline="\n")
    row = {"source_ref": "fixture:docs/spec.md", "source_sha256": hashlib.sha256(doc.read_bytes()).hexdigest(),
           "criteria": ["AC-01"], "target_kind": "code", "target_id": "fixture:src/code.py:Service.run",
           "rationale": "This method returns the identity value", "test_refs": []}
    payload = {"schema": "tare.tools/traceability-mappings/1", "authority": "NONE", "mappings": [row]}
    mapping = root / "planning/map.json"
    mapping.write_text(json.dumps(payload))
    return root, mapping, payload, tmp_path / "state"


def declared(matrix):
    return [edge for edge in matrix.edges if edge.get("origin") == "declared_exact_mapping"]


def test_source_qualified_exact_link_has_no_name_inheritance_or_execution_claim(mapped):
    root, mapping, payload, _state = mapped
    matrix = CausalTraceabilityMatrix.build(root)
    edges = declared(matrix)
    assert len(edges) == 1 and edges[0]["from"] == "src/code.py:Service.run"
    assert edges[0]["target_criterion"] == "SPEC-A:AC-01"
    assert edges[0]["source_sha256"] == payload["mappings"][0]["source_sha256"]
    assert edges[0]["evidence_kind"] == "DECLARATION_ONLY" and edges[0]["authority"] == "NONE"
    assert not any(edge["type"] == "TEST_VERIFIES_SPEC" for edge in matrix.edges)
    assert "src/code.py:Service" in [item.symbol_id for item in matrix.get_orphan_code()]
    assert "SPEC-A" in [item.id for item in matrix.get_unverified_specs()]
    # A schema receives a separate exact declaration; no class/member inheritance.
    payload["mappings"].append(payload["mappings"][0] | {"target_kind": "schema", "target_id": "fixture:src/code.py:Service"})
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    assert any(e["type"] == "SCHEMA_IMPLEMENTS_SPEC" for e in declared(matrix))
    assert not matrix.get_orphan_schemas()
    assert "src/code.py:Service" in [item.symbol_id for item in matrix.get_orphan_code()]


@pytest.mark.parametrize("change", [
    {"source_ref": "foreign:docs/spec.md"}, {"source_ref": "SPEC-A"}, {"source_sha256": "a"*64},
    {"criteria": ["AC-99"]}, {"criteria": []}, {"target_id": "Service.run"},
    {"target_id": "fixture:src/code.py:*"}, {"target_id": "other:src/code.py:Service.run"},
    {"target_kind": "test"}, {"target_kind": []}, {"test_refs": ["../outside.py"]},
    {"extra_authority": "VERIFIED"}, {"rationale": ""},
])
def test_mismatch_refuses_declaration_and_cannot_publish_complete_coverage(mapped, change):
    root, mapping, payload, state = mapped
    payload["mappings"][0].update(change)
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    assert not declared(matrix) and matrix.parse_errors
    observed = index_repository(root, state_dir=state)
    assert observed["status"] == "PARTIAL" and observed["governance"]["status"] == "FAIL"
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.snapshot_payload()["parse_errors"] == sorted(matrix.parse_errors)


def test_incremental_and_full_mapping_edits_deletion_rename_and_source_change_are_equivalent(mapped):
    root, mapping, payload, state = mapped
    # The legacy cache requires a declared test relation; this fixture test
    # checks the exact identity behavior and does not claim it was executed.
    (root / "tests").mkdir()
    (root / "tests/test_identity.py").write_text('from src.code import Service\n\ndef test_identity():\n    """@verifies SPEC-A:AC-01"""\n    assert Service(1).run() == 1\n')
    config = root / "specgraph.yaml"
    config.write_text(config.read_text().replace("test_paths: []", "test_paths: [tests]"))
    initial = index_repository(root, state_dir=state)
    matrix = CausalTraceabilityMatrix.build(root)
    matrix.sync_cache(root)
    assert not IncrementalCacheEngine.detect_staleness(root).is_stale
    payload["mappings"][0]["target_id"] = "fixture:src/code.py:other"
    mapping.write_text(json.dumps(payload))
    assert IncrementalCacheEngine.detect_staleness(root).is_stale
    second = index_repository(root, state_dir=state)
    assert second["content_hash"] != initial["content_hash"]
    assert second["index_run"]["parsed_extractions"] == 0
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.snapshot_payload() == _payload(CausalTraceabilityMatrix.build(root))
    full = index_repository(root, state_dir=state, incremental=False)
    assert full["content_hash"] == second["content_hash"] and full["revision_seq"] == second["revision_seq"]
    mapping.rename(root / "planning/renamed.json")
    renamed = index_repository(root, state_dir=state)
    assert renamed["status"] == "PARTIAL" and renamed["content_hash"] != second["content_hash"]
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.snapshot_payload() == _payload(CausalTraceabilityMatrix.build(root))
        assert not any(e.get("origin") == "declared_exact_mapping" for e in index.snapshot_payload()["edges"])
    (root / "planning/renamed.json").rename(mapping)
    doc = root / "docs/spec.md"
    doc.write_bytes(doc.read_bytes() + b"changed source\n")
    stale = index_repository(root, state_dir=state)
    assert stale["status"] == "PARTIAL"
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.snapshot_payload() == _payload(CausalTraceabilityMatrix.build(root))
    # Removing the explicit selection does not retain old edges or old cache state.
    config = root / "specgraph.yaml"
    config.write_text(config.read_text().replace("intent_mapping_paths: [planning/map.json]", "intent_mapping_paths: []"))
    unselected = index_repository(root, state_dir=state)
    assert unselected["status"] == "READY"
    with RepositoryIndex(root, state_dir=state) as index:
        assert not any(e.get("origin") == "declared_exact_mapping" for e in index.snapshot_payload()["edges"])


def test_distinct_sources_share_exact_target_but_true_duplicate_is_rejected(mapped):
    """@verifies SPEC-SPECGRAPH-C5:AC-01"""
    root, mapping, payload, _state = mapped
    second_doc = root / "docs/second.md"
    second_doc.write_text(
        "---\nid: SPEC-B\ntitle: Second real source\nstatus: ACCEPTED\n"
        "acceptance_criteria:\n  AC-02: Return the local value\n---\n# Second body\n",
        newline="\n",
    )
    second = payload["mappings"][0] | {
        "source_ref": "fixture:docs/second.md",
        "source_sha256": hashlib.sha256(second_doc.read_bytes()).hexdigest(),
        "criteria": ["AC-02"],
        "rationale": "The same exact method returns the local value",
    }
    payload["mappings"].append(second)
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    assert not matrix.parse_errors
    edges = declared(matrix)
    assert len(edges) == 2
    assert {edge["target_ref"] for edge in edges} == {second["target_id"]}
    assert {(edge["source_ref"], edge["target_criterion"]) for edge in edges} == {
        ("fixture:docs/spec.md", "SPEC-A:AC-01"),
        ("fixture:docs/second.md", "SPEC-B:AC-02"),
    }
    assert all(edge["authority"] == "NONE" and edge["evidence_kind"] == "DECLARATION_ONLY" for edge in edges)
    payload["mappings"].append(copy.deepcopy(second))
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    assert len(declared(matrix)) == 2
    assert len(matrix.parse_errors) == 1
    assert "duplicate exact mapping" in matrix.parse_errors[0]


def test_bad_json_duplicate_keys_duplicates_and_escape_are_diagnostic(mapped):
    root, mapping, payload, state = mapped
    for raw in [b'{"schema":', b'{"schema":"x","schema":"y"}', b'[]']:
        mapping.write_bytes(raw)
        matrix = CausalTraceabilityMatrix.build(root)
        assert matrix.parse_errors and not declared(matrix)
    payload["mappings"].append(copy.deepcopy(payload["mappings"][0]))
    mapping.write_text(json.dumps(payload))
    assert any("duplicate" in error for error in CausalTraceabilityMatrix.build(root).parse_errors)
    config = root / "specgraph.yaml"
    config.write_text(config.read_text().replace("planning/map.json", "../outside.json"))
    matrix = CausalTraceabilityMatrix.build(root)
    assert matrix.parse_errors and not declared(matrix)
    assert index_repository(root, state_dir=state)["status"] == "PARTIAL"


def test_mapping_changes_during_capture_retain_previous_revision(mapped, monkeypatch):
    root, mapping, payload, state = mapped
    first = index_repository(root, state_dir=state)
    from specgraph import indexer
    original = indexer._extract_matrix
    def changed(*args, **kwargs):
        mapping.write_bytes(mapping.read_bytes() + b" ")
        return original(*args, **kwargs)
    monkeypatch.setattr(indexer, "_extract_matrix", changed)
    with pytest.raises(indexer.SourceChangedError):
        index_repository(root, state_dir=state)
    with RepositoryIndex(root, state_dir=state) as index:
        assert index.metadata()["content_hash"] == first["content_hash"]


def test_existing_test_outside_selected_scope_is_rejected_with_diagnostic(mapped):
    """@verifies SPEC-SPECGRAPH-C5:AC-01"""
    root, mapping, payload, _state = mapped
    (root / "tests").mkdir()
    (root / "tests/test_selected.py").write_text("def test_selected():\n    assert True\n")
    # A long, real path also prevents the diagnostic from being replaced by
    # the generic shape error merely because the message exceeds 160 chars.
    outside = "tools/tests/test_" + "outside_" * 16 + "scope.py"
    rejected = root / outside
    rejected.parent.mkdir(parents=True)
    rejected.write_text("def test_outside():\n    raise AssertionError('must not execute')\n")
    assert rejected.is_file()
    config = root / "specgraph.yaml"
    config.write_text(config.read_text().replace("test_paths: []", "test_paths: [tests]"))
    payload["mappings"][0]["test_refs"] = [outside]
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    assert not declared(matrix)
    assert not any(edge["type"] == "TEST_VERIFIES_SPEC" for edge in matrix.edges)
    assert len(matrix.parse_errors) == 1
    diagnostic = matrix.parse_errors[0]
    assert "test reference absent from indexed test scope" in diagnostic
    assert outside in diagnostic and "test_paths=['tests']" in diagnostic
    assert all(test.file_path != outside for test in matrix.tests.values())
    # The refused code declaration leaves this spec orphan, rather than
    # implemented-but-unverified. Neither category is execution evidence.
    assert "SPEC-A" in [item.id for item in matrix.get_orphan_specs()]
    from specgraph.traceability import apply_declared_mappings
    matrix.config.test_paths = ["tests", "\u6d4b" * 4000]
    edges, errors = apply_declared_mappings(matrix)
    assert not edges and len(errors) == 1
    assert outside in errors[0] and "test_paths=" in errors[0]
    assert errors[0].endswith(" [diagnostic truncated]")
    assert len(errors[0].encode("utf-8")) <= 2048


def test_exact_test_mapping_is_a_declared_criterion_link_not_execution(mapped):
    root, mapping, payload, _state = mapped
    (root / "tests").mkdir()
    (root / "tests/test_code.py").write_text('def test_identity():\n    assert 1 == 1\n')
    config = root / "specgraph.yaml"
    config.write_text(config.read_text().replace("test_paths: []", "test_paths: [tests]"))
    payload["mappings"] = [payload["mappings"][0] | {"target_kind": "test", "target_id": "fixture:tests/test_code.py:test_identity", "test_refs": ["tests/test_code.py"]}]
    mapping.write_text(json.dumps(payload))
    matrix = CausalTraceabilityMatrix.build(root)
    edge = declared(matrix)[0]
    assert edge["type"] == "TEST_VERIFIES_SPEC" and edge["verified_criterion"] == "SPEC-A:AC-01"
    assert edge["evidence_kind"] == "DECLARED_TEST_LINK" and edge["authority"] == "NONE"
    assert "executed" not in edge and "attestation" not in edge and "evidence_grade" not in edge
    assert len(matrix.get_orphan_code()) > 0  # Test declarations do not bind code.
    payload["mappings"][0]["test_refs"] = []
    mapping.write_text(json.dumps(payload))
    assert not declared(CausalTraceabilityMatrix.build(root))
