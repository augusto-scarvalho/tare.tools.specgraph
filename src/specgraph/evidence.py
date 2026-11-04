"""Read-only validity projection of executor-owned execution evidence.

This module signs nothing, runs no command and never writes Work status. A
caller supplies current observations and its trusted attestation verifier;
without that verifier an execution claim cannot become valid evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Callable

RELATIONS = {"TEST_REFERENCES_SYMBOL", "DECLARED_VERIFIES", "EXECUTED_COVERAGE"}


def classify_test_relation(relation: str) -> str:
    """Keep static links distinct from observed executor coverage (C5)."""
    if relation == "TEST_REFERENCES_SYMBOL":
        return "STATIC_REFERENCE"
    if relation == "DECLARED_VERIFIES":
        return "DECLARED_ONLY"
    if relation == "EXECUTED_COVERAGE":
        return "EXECUTED_COVERAGE"
    return "UNKNOWN"
_SHA = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AttestationVerdict:
    """Produced by the caller's trusted executor/Kernel verifier, not by XML."""
    status: str
    subject_sha256: str
    attestation_ref: str
    verifier: str


def evidence_payload_sha256(record: dict[str, Any]) -> str:
    """Hash the claim signed externally; exclude its detached attestation locator."""
    claim = {key: value for key, value in record.items() if key != "attestation_ref"}
    raw = json.dumps(claim, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _hashes(value: Any, label: str, *, nonempty: bool = False) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 4096 or (nonempty and not value):
        raise ValueError(f"{label} must be a bounded hash mapping")
    if not all(isinstance(k, str) and 0 < len(k) <= 1024 and isinstance(v, str) and _SHA.fullmatch(v) for k, v in value.items()):
        raise ValueError(f"{label} contains invalid identities or SHA-256 digests")
    return value


def _validate(record: Any) -> None:
    if not isinstance(record, dict) or record.get("schema") != "tare.tools/execution-evidence/1":
        raise ValueError("Unsupported execution evidence schema")
    encoded = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    if len(encoded) > 65536:
        raise ValueError("Execution evidence exceeds 64 KiB")
    for field in ("evidence_id", "executor", "observed_at"):
        if not isinstance(record.get(field), str) or not record[field] or len(record[field]) > 1024:
            raise ValueError(f"Missing bounded {field}")
    if record.get("relation") not in RELATIONS:
        raise ValueError("Unknown evidence relation")
    command = record.get("command")
    if not isinstance(command, list) or not command or len(command) > 128 or not all(isinstance(x, str) and len(x) <= 4096 for x in command):
        raise ValueError("Execution command must be an argv list")
    for field in ("config_sha256", "environment_sha256"):
        if not isinstance(record.get(field), str) or not _SHA.fullmatch(record[field]):
            raise ValueError(f"Missing {field}")
    snapshot = record.get("snapshot")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("repository"), str) or not snapshot["repository"] or len(snapshot["repository"]) > 256:
        raise ValueError("Repository-qualified snapshot required")
    if type(snapshot.get("revision_seq")) is not int or snapshot["revision_seq"] < 1 or not isinstance(snapshot.get("content_hash"), str) or not _SHA.fullmatch(snapshot["content_hash"]):
        raise ValueError("Snapshot revision_seq/content_hash required")
    _hashes(record.get("source_hashes"), "source_hashes", nonempty=True)
    _hashes(record.get("criterion_hashes"), "criterion_hashes", nonempty=True)
    for reference in record["criterion_hashes"]:
        source, separator, criterion = reference.rpartition("#")
        if not separator or not criterion or source not in record["source_hashes"] or ":" not in source:
            raise ValueError("Criteria must use source-qualified-id#criterion with a declared source")
    if any(":" not in reference for reference in record["source_hashes"]):
        raise ValueError("Source identities must be repository-qualified")
    dependencies = record.get("dependencies")
    if not isinstance(dependencies, dict) or type(dependencies.get("complete")) is not bool:
        raise ValueError("Dependency coverage must explicitly declare completeness")
    _hashes(dependencies.get("hashes"), "dependencies.hashes")
    if record["relation"] == "EXECUTED_COVERAGE":
        coverage = record.get("coverage")
        if not isinstance(coverage, dict) or not isinstance(coverage.get("method"), str) or not coverage["method"]:
            raise ValueError("Execution coverage requires the executor's observation method")
        for field in ("tests", "symbols"):
            refs = coverage.get(field)
            if not isinstance(refs, list) or not refs or len(refs) > 256 or not all(isinstance(ref, str) and ":" in ref and len(ref) <= 1024 for ref in refs):
                raise ValueError("Execution coverage requires qualified observed tests and symbols")
        if not dependencies["hashes"]:
            raise ValueError("Execution coverage requires observed source/test dependencies")
    _hashes(record.get("artifact_hashes"), "artifact_hashes", nonempty=True)
    if record.get("outcome") not in {"PASSED", "FAILED", "NOT_EXECUTED"}:
        raise ValueError("Explicit execution outcome required")


def evaluate_evidence(record: dict[str, Any], current: dict[str, Any], *,
                      attestation_verifier: Callable[[dict[str, Any]], AttestationVerdict] | None = None,
                      superseded_by: str | None = None) -> dict[str, Any]:
    """Evaluate historical claim validity against independently observed state.

    ``current`` carries snapshot, config/environment hashes, source/criterion,
    dependency and artifact hash mappings. A complete dependency closure permits
    reuse across irrelevant repository changes, subject to the verifier. An
    incomplete closure permits reuse only at the identical content snapshot.
    VALID means the scoped execution receipt still applies, not that the whole
    project passed, all tests ran, or a Work is complete. Grade/Tier are not set.

@spec SPEC-SPECGRAPH-C5
    """
    evidence_id = record.get("evidence_id") if isinstance(record, dict) else None
    if not isinstance(evidence_id, str) or len(evidence_id) > 1024:
        evidence_id = None
    result: dict[str, Any] = {"schema": "tare.tools/evidence-validity/1", "status": "INVALID",
                              "evidence_id": evidence_id,
                              "reason_codes": [], "authority": "NONE", "work_modified": False,
                              "evidence_grade": None, "tier": None, "historical_execution_preserved": True}
    try:
        _validate(record)
        subject = evidence_payload_sha256(record)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        result["reason_codes"] = ["INVALID_EVIDENCE_CONTRACT"]
        return result
    result.update(subject_sha256=subject, relation=record["relation"], outcome=record["outcome"],
                  snapshot={key: record["snapshot"][key] for key in ("repository", "revision_seq", "content_hash")}, supports_success=False)
    if record["relation"] != "EXECUTED_COVERAGE" or record["outcome"] == "NOT_EXECUTED":
        result["status"] = "NEEDS_REVALIDATION"
        result["reason_codes"] = ["DECLARATION_OR_REFERENCE_IS_NOT_EXECUTION"]
        return result
    reasons = []
    if not isinstance(current, dict):
        current = {}
    current_snapshot = current.get("snapshot", {})
    if not isinstance(current_snapshot, dict):
        current_snapshot = {}
    if current_snapshot.get("repository") != record["snapshot"]["repository"]:
        result["reason_codes"] = ["REPOSITORY_MISMATCH"]
        return result
    for field in ("config_sha256", "environment_sha256"):
        if current.get(field) != record[field]:
            reasons.append(field.upper().replace("_SHA256", "") + "_CHANGED_OR_UNKNOWN")
    for field in ("source_hashes", "criterion_hashes"):
        observed = current.get(field, {})
        if not isinstance(observed, dict) or any(observed.get(key) != value for key, value in record[field].items()):
            reasons.append(field.upper() + "_CHANGED_OR_UNKNOWN")
    observed_artifacts = current.get("artifact_hashes", {})
    if not isinstance(observed_artifacts, dict) or any(observed_artifacts.get(k) != v for k, v in record["artifact_hashes"].items()):
        result["reason_codes"] = ["ARTIFACT_MISSING_OR_HASH_MISMATCH"]
        return result
    observed_dependencies = current.get("dependency_hashes", {})
    if not isinstance(observed_dependencies, dict) or any(observed_dependencies.get(k) != v for k, v in record["dependencies"]["hashes"].items()):
        reasons.append("DEPENDENCY_CHANGED_OR_UNKNOWN")
    if record["dependencies"]["complete"] and observed_dependencies != record["dependencies"]["hashes"]:
        reasons.append("DEPENDENCY_CLOSURE_CHANGED_OR_UNKNOWN")
    snapshot_changed = current_snapshot.get("content_hash") != record["snapshot"]["content_hash"]
    if snapshot_changed and not record["dependencies"]["complete"]:
        reasons.append("INCOMPLETE_DEPENDENCY_CLOSURE_REQUIRES_IDENTICAL_SNAPSHOT")
    if not isinstance(current_snapshot.get("content_hash"), str) or not _SHA.fullmatch(current_snapshot["content_hash"]):
        reasons.append("CURRENT_SNAPSHOT_UNKNOWN")
    if type(current_snapshot.get("revision_seq")) is not int or current_snapshot["revision_seq"] < record["snapshot"]["revision_seq"]:
        reasons.append("CURRENT_REVISION_UNKNOWN_OR_OLDER")
    attestation = record.get("attestation_ref")
    if not isinstance(attestation, str) or not attestation or len(attestation) > 2048 or attestation_verifier is None:
        reasons.append("ATTESTATION_NOT_VERIFIED")
    else:
        try:
            verdict = attestation_verifier(json.loads(json.dumps(record)))
        except Exception:
            verdict = None
        if not isinstance(verdict, AttestationVerdict) or verdict.status != "VERIFIED":
            reasons.append("ATTESTATION_NOT_VERIFIED")
        elif (verdict.subject_sha256 != subject or verdict.attestation_ref != attestation
              or not isinstance(verdict.verifier, str) or not verdict.verifier or len(verdict.verifier) > 256):
            result["reason_codes"] = ["ATTESTATION_SUBJECT_MISMATCH"]
            return result
        else:
            result["attestation"] = {"reference": attestation, "verifier": verdict.verifier,
                                     "subject_sha256": subject, "status": "VERIFIED_EXTERNALLY"}
    if superseded_by is not None:
        if not isinstance(superseded_by, str) or not superseded_by or len(superseded_by) > 1024 or superseded_by == record["evidence_id"]:
            result["reason_codes"] = ["INVALID_SUPERSESSION_REFERENCE"]
            return result
        result.update(status="SUPERSEDED", superseded_by=superseded_by,
                      reason_codes=["SUPERSESSION_DECLARED_BY_CALLER"], validity_reasons=reasons)
        return result
    result["status"] = "NEEDS_REVALIDATION" if reasons else "VALID"
    result["reason_codes"] = reasons or ["SCOPED_EXECUTION_AND_ATTESTATION_STILL_APPLY"]
    result["supports_success"] = result["status"] == "VALID" and record["outcome"] == "PASSED"
    result["snapshot_reused_across_irrelevant_changes"] = snapshot_changed and not reasons
    result["dependency_coverage"] = "COMPLETE_DECLARED_AND_ATTESTED" if record["dependencies"]["complete"] and not reasons else "NOT_QUALIFIED_COMPLETE"
    return result
