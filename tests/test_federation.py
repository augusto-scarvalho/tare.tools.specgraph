"""Pinned owner acquisition and source-qualified intent mapping falsifiers."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from specgraph.federation import (acquire_federated_documents, check_work_source_binding,
                                  read_pinned_document, resolve_intent_mappings, serialize_federation)
from specgraph.ground import ground_repository
from specgraph.indexer import index_repository


def git(root, *args):
    return subprocess.run(["git", "-c", f"core.hooksPath={root / 'empty-hooks'}", "-c", "user.name=SpecGraph test",
                           "-c", "user.email=test@example.invalid", "-C", str(root), *args],
                          check=True, capture_output=True, timeout=10).stdout.decode().strip()


@pytest.fixture
def federation(tmp_path):
    owners, entries = {}, []
    for repo in ("owner-a", "owner-b"):
        root = tmp_path / repo
        (root / "docs").mkdir(parents=True)
        body = f"---\nid: SPEC-SAME\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-01: preserve {repo} identity\n---\n# {repo} body\n"
        document = root / "docs/spec.md"
        document.write_text(body, encoding="utf-8", newline="\n")
        git(root, "init", "-q"); git(root, "add", "docs/spec.md"); git(root, "commit", "-qm", "fixture source")
        revision = git(root, "rev-parse", "HEAD")
        sha = hashlib.sha256(document.read_bytes()).hexdigest()
        owners[repo] = root
        reference = f"{repo}:docs/spec.md"
        locator = f"{repo}@{revision}:docs/spec.md"
        entries.append({"id": sha, "semantic_document_id": reference, "doc_type": "spec", "title": repo,
                        "relative_path": locator, "sha256": sha, "status": "REPOSITORY_OWNED",
                        "authority_state": "DECLARED_ACTIVE", "target_repositories": [repo],
                        "acceptance_criteria": [], "source_paths": [{"relative_path": locator,
                        "semantic_document_id": reference, "authority_state": "DECLARED_ACTIVE", "editorial_status": "REPOSITORY_OWNED"}]})
    manifest = tmp_path / "LIBRARY_MANIFEST.json"
    manifest.write_text(json.dumps({"version": "3.0.0", "authority_vocabulary": "authority_state/v1", "specs": entries}))
    return manifest, owners, entries


def acquire(fixture, owner="owner-a", **kwargs):
    manifest, owners, _ = fixture
    return acquire_federated_documents(manifest, owner, owners, include_ids=[f"{owner}:docs/spec.md"], **kwargs)


def test_metadata_ready_is_not_body_acquisition_and_commit_ignores_dirty_checkout(federation):
    manifest, owners, entries = federation
    grounded = ground_repository(manifest, "owner-a", include_ids=["owner-a:docs/spec.md"])
    assert grounded.status == "READY" and grounded.entries[0].acceptance_criteria == ()
    (owners["owner-a"] / "docs/spec.md").write_text("uncommitted altered intent", encoding="utf-8")
    result = acquire(federation)
    assert result["status"] == "READY" and result["body_read"] is True
    document = result["documents"][0]
    assert document["acceptance_criteria"] == {"AC-01": "preserve owner-a identity"}
    assert document["sha256"] == entries[0]["sha256"]
    assert "uncommitted" not in document["body"]
    other = acquire(federation, "owner-b")["documents"][0]
    assert other["declared_id"] == document["declared_id"] == "SPEC-SAME"
    assert other["semantic_document_id"] != document["semantic_document_id"]


def test_missing_owner_hash_mismatch_and_body_budget_are_explicit(federation):
    manifest, owners, entries = federation
    missing = acquire_federated_documents(manifest, "owner-a", {}, include_ids=["owner-a:docs/spec.md"])
    assert missing["status"] == "UNAVAILABLE" and not missing["body_read"]
    result = acquire(federation, max_bytes=1024)
    assert len(serialize_federation(result).encode()) <= 1024
    if result["status"] == "BUDGET_EXCEEDED":
        assert result["body_delivered"] is False
    original = json.loads(manifest.read_text())
    broken = copy.deepcopy(original)
    broken["specs"][0]["sha256"] = broken["specs"][0]["id"] = "a" * 64
    manifest.write_text(json.dumps(broken))
    result = acquire(federation)
    assert result["status"] == "SOURCE_INVALID" and not result["documents"]


def test_only_full_commit_and_bounded_blob_acquisition(federation):
    _, owners, entries = federation
    root = owners["owner-a"]
    with pytest.raises(ValueError, match="Full commit"):
        read_pinned_document(root, "HEAD", "docs/spec.md", entries[0]["sha256"])
    revision = git(root, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="escapes"):
        read_pinned_document(root, revision, "../outside.md", entries[0]["sha256"])
    blob = git(root, "rev-parse", "HEAD:docs/spec.md")
    with pytest.raises(ValueError, match="not a Git commit"):
        read_pinned_document(root, blob, "docs/spec.md", entries[0]["sha256"])


def test_exact_mapping_vs_assisted_candidate_and_source_ac_mismatch(federation, tmp_path):
    """@verifies SPEC-SPECGRAPH-C5
    """
    document = acquire(federation)["documents"][0]
    root = tmp_path / "implementation"
    (root / "src").mkdir(parents=True)
    (root / "src/code.py").write_text("def execute():\n    return 1\n")
    state = tmp_path / "index-state"
    snapshot = index_repository(root, state_dir=state)
    mapping = {"source_ref": document["semantic_document_id"], "source_sha256": document["sha256"],
               "criteria": ["AC-01"], "symbol_id": "implementation:src/code.py:execute"}
    result = resolve_intent_mappings(root, [mapping], [document], repository="implementation", state_dir=state)
    assert result["status"] == "READY" and result["mappings"][0]["status"] == "BOUND"
    assert result["content_hash"] == snapshot["content_hash"]
    assert result["authority"] == "NONE"
    ambiguous = mapping | {"symbol_id": "execute"}
    result = resolve_intent_mappings(root, [ambiguous], [document], repository="implementation", state_dir=state)
    assert result["mappings"][0]["status"] == "CANDIDATES"
    assert result["mappings"][0]["rebind_applied"] is False
    for changed, status in [(mapping | {"source_ref": "owner-b:docs/spec.md"}, "SOURCE_MISMATCH"),
                            (mapping | {"source_sha256": "b" * 64}, "SOURCE_MISMATCH"),
                            (mapping | {"symbol_id": "other:src/code.py:execute"}, "REPOSITORY_MISMATCH"),
                            (mapping | {"criteria": ["AC-99"]}, "AC_MISMATCH")]:
        result = resolve_intent_mappings(root, [changed], [document], repository="implementation", state_dir=state)
        assert result["mappings"][0]["status"] == status


def test_work_admission_prerequisite_requires_scope_pin_and_exact_ac_without_mutation(federation):
    document = acquire(federation)["documents"][0]
    work = {"id": "WORK-1", "spec_ref": document["semantic_document_id"], "completion": {"status": "NOT_DONE"},
            "repository_scopes": [{"repository": "owner-a", "grounding_refs": [document["semantic_document_id"]]}]}
    original = copy.deepcopy(work)
    assert check_work_source_binding(work, document, ["AC-01"])["status"] == "REFUSED"
    accepted = check_work_source_binding(work, document, ["AC-01"], expected_source_sha256=document["sha256"])
    assert accepted["status"] == "MATCHED" and accepted["authority"] == "NONE"
    assert check_work_source_binding(work, document, ["AC-99"], expected_source_sha256=document["sha256"])["status"] == "REFUSED"
    assert check_work_source_binding(work, document, ["AC-01"], expected_source_sha256="c" * 64)["status"] == "REFUSED"
    assert work == original


def test_review_composes_explicit_pinned_owner_intent_without_losing_criteria(federation, tmp_path):
    from specgraph.review import review_bundle
    _, owners, _ = federation
    root = owners["owner-a"]
    document = acquire(federation)["documents"][0]
    (root / "src").mkdir()
    source = root / "src/code.py"
    source.write_text("def execute():\n    return 1\n", newline="\n")
    git(root, "add", "src/code.py"); git(root, "commit", "-qm", "code baseline")
    source.write_text("def execute():\n    return 2\n", newline="\n")
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    result = review_bundle(root, "HEAD", state_dir=state, federated_documents=[document], max_bytes=65536)
    assert result["status"] == "READY"
    selected = [d for d in result["documents"] if d["document_id"] == "owner-a:docs/spec.md"]
    assert selected[0]["revision"] == document["revision"]
    assert selected[0]["acceptance_criteria"] == document["acceptance_criteria"]
    assert selected[0]["source"] == "CALLER_SELECTED_PINNED_OWNER_INTENT"


def test_explicit_owner_roots_for_body_acquisition(tmp_path):
    """@verifies SPEC-SPECGRAPH-C5 [AC-02]"""
    from specgraph.ontology import OntologyFederationError, parse_owner_roots
    absolute = tmp_path / "absolute"
    assert parse_owner_roots([" first = relative ", f"second={absolute}"], tmp_path) == {
        "first": (tmp_path / "relative").resolve(), "second": absolute.resolve(),
    }
    assert parse_owner_roots([], tmp_path) == {}
    for value in ["missing", "=path", "owner=", " = "]:
        with pytest.raises(OntologyFederationError, match="REPOSITORY=PATH"):
            parse_owner_roots([value], tmp_path)
    with pytest.raises(OntologyFederationError, match="supplied twice"):
        parse_owner_roots(["owner=a", " owner =b"], tmp_path)
