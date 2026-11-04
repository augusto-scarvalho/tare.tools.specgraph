"""Execution/reference separation and conservative validity (C5 AC-03/04)."""
import copy
import hashlib

import pytest

from specgraph.evidence import AttestationVerdict, evaluate_evidence, evidence_payload_sha256


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def execution():
    record = {"schema": "tare.tools/execution-evidence/1", "evidence_id": "EXEC-1", "executor": "owner-executor",
              "observed_at": "2026-09-08T15:00:00Z", "relation": "EXECUTED_COVERAGE", "outcome": "PASSED",
              "command": ["python", "-m", "pytest", "tests/test_code.py"], "config_sha256": sha("config"),
              "environment_sha256": sha("environment"), "snapshot": {"repository": "repo", "revision_seq": 1, "content_hash": sha("snapshot")},
              "source_hashes": {"repo:docs/SPEC.md": sha("spec")}, "criterion_hashes": {"repo:docs/SPEC.md#AC-01": sha("criterion")},
              "dependencies": {"complete": False, "hashes": {"repo:src/code.py": sha("code"), "repo:tests/test_code.py": sha("test"), "repo:uv.lock": sha("lock")}},
              "artifact_hashes": {"receipt://executor/result": sha("artifact")}, "attestation_ref": "attestation://executor/run-1",
              "coverage": {"method": "executor-observed", "tests": ["repo:tests/test_code.py:test_execute"], "symbols": ["repo:src/code.py:execute"]}}
    current = {"snapshot": copy.deepcopy(record["snapshot"]), "config_sha256": record["config_sha256"], "environment_sha256": record["environment_sha256"],
               "source_hashes": copy.deepcopy(record["source_hashes"]), "criterion_hashes": copy.deepcopy(record["criterion_hashes"]),
               "dependency_hashes": copy.deepcopy(record["dependencies"]["hashes"]), "artifact_hashes": copy.deepcopy(record["artifact_hashes"])}
    return record, current


def verified(record):
    return AttestationVerdict("VERIFIED", evidence_payload_sha256(record), record["attestation_ref"], "explicit-test-verifier")


def test_valid_receipt_preserves_history_without_grade_authority_or_work_completion(execution):
    record, current = execution
    originals = copy.deepcopy((record, current))
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "VALID" and result["supports_success"] is True
    assert result["tier"] is result["evidence_grade"] is None
    assert result["authority"] == "NONE" and result["work_modified"] is False
    assert (record, current) == originals
    record["outcome"] = "FAILED"
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "VALID" and result["supports_success"] is False


@pytest.mark.parametrize("relation", ["DECLARED_VERIFIES", "TEST_REFERENCES_SYMBOL"])
def test_declared_test_or_reference_never_becomes_executed_coverage(execution, relation):
    record, current = execution
    record["relation"] = relation
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "NEEDS_REVALIDATION"
    assert result["reason_codes"] == ["DECLARATION_OR_REFERENCE_IS_NOT_EXECUTION"]
    assert result["supports_success"] is False


def test_junit_hash_and_self_report_without_external_verifier_are_insufficient(execution):
    record, current = execution
    record["junit_passed"] = True  # An extra claim remains part of the signed subject, not proof.
    result = evaluate_evidence(record, current)
    assert result["status"] == "NEEDS_REVALIDATION"
    assert "ATTESTATION_NOT_VERIFIED" in result["reason_codes"]
    def lying_verifier(claim):
        return AttestationVerdict("VERIFIED", "a" * 64, claim["attestation_ref"], "explicit-test-verifier")
    assert evaluate_evidence(record, current, attestation_verifier=lying_verifier)["status"] == "INVALID"


@pytest.mark.parametrize("field", ["config_sha256", "environment_sha256", "source_hashes", "criterion_hashes", "dependency_hashes"])
def test_relevant_change_demands_revalidation(execution, field):
    record, current = execution
    if isinstance(current[field], dict):
        current[field][next(iter(current[field]))] = sha("changed")
    else:
        current[field] = sha("changed")
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "NEEDS_REVALIDATION"
    assert result["historical_execution_preserved"] is True


def test_incomplete_dependencies_require_identical_snapshot_but_complete_scope_can_reuse(execution):
    record, current = execution
    current["snapshot"].update(revision_seq=2, content_hash=sha("unrelated file changed"))
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "NEEDS_REVALIDATION"
    record["dependencies"]["complete"] = True
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "VALID" and result["snapshot_reused_across_irrelevant_changes"]
    current["dependency_hashes"]["repo:src/new_dependency.py"] = sha("new relevant dependency")
    result = evaluate_evidence(record, current, attestation_verifier=verified)
    assert result["status"] == "NEEDS_REVALIDATION"
    assert "DEPENDENCY_CLOSURE_CHANGED_OR_UNKNOWN" in result["reason_codes"]


def test_wrong_repository_missing_artifact_and_rebound_source_criterion_are_rejected(execution):
    record, current = execution
    wrong = copy.deepcopy(current); wrong["snapshot"]["repository"] = "homonym-other-repo"
    assert evaluate_evidence(record, wrong, attestation_verifier=verified)["status"] == "INVALID"
    wrong = copy.deepcopy(current); wrong["artifact_hashes"] = {}
    assert evaluate_evidence(record, wrong, attestation_verifier=verified)["status"] == "INVALID"
    record["criterion_hashes"] = {"other:docs/SPEC.md#AC-01": sha("criterion")}
    assert evaluate_evidence(record, current, attestation_verifier=verified)["status"] == "INVALID"


def test_supersession_is_explicit_and_does_not_erase_historical_failure(execution):
    record, current = execution
    record["outcome"] = "FAILED"
    result = evaluate_evidence(record, current, attestation_verifier=verified, superseded_by="EXEC-2")
    assert result["status"] == "SUPERSEDED" and result["outcome"] == "FAILED"
    assert record["evidence_id"] == "EXEC-1"
    assert evaluate_evidence(record, current, superseded_by="EXEC-1")["status"] == "INVALID"


@pytest.mark.parametrize("field", ["command", "executor", "snapshot", "config_sha256", "environment_sha256", "coverage"])
def test_missing_execution_provenance_is_invalid(execution, field):
    record, current = execution
    del record[field]
    assert evaluate_evidence(record, current, attestation_verifier=verified)["status"] == "INVALID"
