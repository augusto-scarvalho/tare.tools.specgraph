"""Actual Kernel checks remain advisory unless an external owner attests them."""
from dataclasses import replace
import hashlib
import json
import sys

import pytest

from specgraph.executor_evidence import project_executor_observation

kernel = pytest.importorskip("compute_plane.managed_work", reason="Kernel integration requires its explicit installation")


def test_actual_kernel_receipt_projects_without_claiming_coverage_or_attestation(tmp_path):
    """@verifies SPEC-SPECGRAPH-C5:AC-03
    @verifies SPEC-SPECGRAPH-C5:AC-04
    @verifies SPEC-SPECGRAPH-C5:AC-05
    """
    (tmp_path / "value.txt").write_text("observed")
    checks = [kernel.VerificationCommand("AC-01: exact", (sys.executable, "-c", "from pathlib import Path; assert Path('value.txt').read_text() == 'observed'"))]
    progress = kernel.observe_work_progress(workspace=tmp_path, exit_criteria=[checks[0].criterion],
                                            checks=checks, tracked_paths=["value.txt"])
    assert hashlib.sha256(progress.evidence_bytes).hexdigest() == progress.evidence_sha256
    result = project_executor_observation(progress, commands=checks, executor="tare.tools.kernel",
                                         candidate_sha256="a"*64, descriptor_sha256="b"*64)
    assert result["status"] == "NEEDS_REVALIDATION"
    assert result["observed_checks_passed"] == 1 and result["receipts"][0]["exit_code"] == 0
    assert result["evidence_grade"] is None and result["authority"] == "NONE"
    assert result["coverage"]["status"] == "NOT_OBSERVED" and result["attestation"]["status"] == "NOT_VERIFIED"
    assert result["supports_success"] is False
    for broken, commands in [(replace(progress, evidence_sha256="c"*64), checks),
                              (replace(progress, evidence_bytes=b""), checks),
                              (replace(progress, verified_evidence_count=0), checks),
                              (progress, [kernel.VerificationCommand(checks[0].criterion, (sys.executable, "-c", "pass"))])]:
        invalid = project_executor_observation(broken, commands=commands, executor="tare.tools.kernel",
                                               candidate_sha256="a"*64, descriptor_sha256="b"*64)
        assert invalid["status"] == "INVALID" and not invalid["supports_success"]
    bounded = project_executor_observation(progress, commands=checks, executor="tare.tools.kernel",
                                           candidate_sha256="a"*64, descriptor_sha256="b"*64, max_bytes=1024)
    assert len((json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()) <= 1024


def test_actual_failed_check_is_preserved_as_failed_not_success(tmp_path):
    (tmp_path / "file").write_text("x")
    checks = [kernel.VerificationCommand("AC-01", (sys.executable, "-c", "raise SystemExit(7)"))]
    progress = kernel.observe_work_progress(workspace=tmp_path, exit_criteria=["AC-01"], checks=checks, tracked_paths=["file"])
    result = project_executor_observation(progress, commands=checks, executor="kernel", candidate_sha256="a"*64, descriptor_sha256="b"*64)
    assert result["receipts"][0]["status"] == "FAIL" and result["receipts"][0]["exit_code"] == 7
    assert result["remaining_obligations"] == 1 and result["observed_checks_passed"] == 0


@pytest.mark.parametrize("receipts", [[1], [[1]], [{"criterion": []}], [{"criterion": "AC-01", "status": "PASS", "command_sha256": "a"*64}]])
def test_malformed_receipt_elements_are_bounded_invalid(receipts):
    from types import SimpleNamespace
    raw = json.dumps({"schema": "tare.tools/verification-progress/1", "receipts": receipts}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    progress = SimpleNamespace(evidence_bytes=raw, evidence_sha256=hashlib.sha256(raw).hexdigest(),
        state_sha256="a"*64, remaining_obligations=0, verified_evidence_count=1)
    checks = [kernel.VerificationCommand("AC-01", (sys.executable, "-c", "pass"))]
    result = project_executor_observation(progress, commands=checks, executor="kernel", candidate_sha256="a"*64, descriptor_sha256="b"*64)
    assert result["status"] == "INVALID"


@pytest.mark.parametrize("mutation", [{"exit_code": False}, {"exit_code": 7}, {"stdout_sha256": "unverified"},
                                      {"attestation": "self-asserted"}, {"reused": "yes"}])
def test_receipt_cannot_promote_contradictory_or_extra_execution_claims(mutation):
    from types import SimpleNamespace
    checks = [kernel.VerificationCommand("AC-01", (sys.executable, "-c", "pass"))]
    command_sha = hashlib.sha256(json.dumps(list(checks[0].argv), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    receipt = {"criterion": "AC-01", "command_sha256": command_sha, "status": "PASS", "exit_code": 0,
               "stdout_sha256": "a"*64, "stderr_sha256": "b"*64} | mutation
    raw = json.dumps({"schema": "tare.tools/verification-progress/1", "receipts": [receipt]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    progress = SimpleNamespace(evidence_bytes=raw, evidence_sha256=hashlib.sha256(raw).hexdigest(),
        state_sha256="a"*64, remaining_obligations=0, verified_evidence_count=1)
    result = project_executor_observation(progress, commands=checks, executor="kernel", candidate_sha256="a"*64, descriptor_sha256="b"*64)
    assert result["status"] == "INVALID"
