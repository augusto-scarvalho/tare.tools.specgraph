"""Projection of the real Kernel's command receipts, without invented coverage.

An owner-observed check is useful evidence. It is not an independently verified
attestation, complete dependency closure or proof of which symbols ran.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from .federation import _fit


def project_executor_observation(progress: Any, *, commands: Sequence[Any],
                                 executor: str, candidate_sha256: str,
                                 descriptor_sha256: str, max_bytes: int = 8192) -> dict[str, Any]:
    """Validate and expose exact Kernel receipt bytes as advisory observations.

    @spec SPEC-SPECGRAPH-C5
    """
    result = {"schema": "tare.tools/executor-observation/1", "authority": "NONE",
              "status": "INVALID", "supports_success": False, "evidence_grade": None,
              "reason_codes": [], "attestation": {"status": "NOT_VERIFIED"},
              "coverage": {"status": "NOT_OBSERVED"}, "dependency_closure": "UNKNOWN"}
    try:
        raw = progress.evidence_bytes
        if not isinstance(raw, bytes) or not raw or len(raw) > 65536:
            raise ValueError("RECEIPT_UNAVAILABLE_OR_TOO_LARGE")
        if hashlib.sha256(raw).hexdigest() != progress.evidence_sha256:
            raise ValueError("RECEIPT_HASH_MISMATCH")
        for digest in (candidate_sha256, descriptor_sha256, progress.state_sha256):
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("BINDING_DIGEST_INVALID")
        if not isinstance(executor, str) or not executor or len(executor.encode()) > 256:
            raise ValueError("EXECUTOR_ID_INVALID")
        payload = json.loads(raw)
        if (payload.get("schema") != "tare.tools/verification-progress/1"
                or raw != json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()):
            raise ValueError("RECEIPT_CONTRACT_INVALID")
        receipts = payload["receipts"]
        if not 1 <= len(commands) <= 64 or not isinstance(receipts, list) or len(receipts) != len(commands):
            raise ValueError("CHECK_SET_MISMATCH")
        expected = {}
        for command in commands:
            if command.criterion in expected:
                raise ValueError("CHECK_SET_MISMATCH")
            expected[command.criterion] = hashlib.sha256(json.dumps(list(command.argv), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if len({item["criterion"] for item in receipts}) != len(receipts):
            raise ValueError("CHECK_SET_MISMATCH")
        if any(expected.get(item["criterion"]) != item.get("command_sha256") for item in receipts):
            raise ValueError("CHECK_COMMAND_MISMATCH")
        if any(item.get("status") not in {"PASS", "FAIL", "TIMEOUT", "UNAVAILABLE", "OUTPUT_TOO_LARGE"} for item in receipts):
            raise ValueError("CHECK_STATUS_INVALID")
        for item in receipts:
            fields = {"criterion", "command_sha256", "status"}
            if "reused" in item:
                fields.add("reused")
                if item["reused"] is not True:
                    raise ValueError("CHECK_REUSE_INVALID")
            if item["status"] in {"PASS", "FAIL"}:
                fields.update({"exit_code", "stdout_sha256", "stderr_sha256"})
                if type(item.get("exit_code")) is not int or (item["exit_code"] == 0) != (item["status"] == "PASS"):
                    raise ValueError("CHECK_EXIT_STATUS_MISMATCH")
                for key in ("stdout_sha256", "stderr_sha256"):
                    digest = item.get(key)
                    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                        raise ValueError("CHECK_OUTPUT_DIGEST_INVALID")
            if set(item) != fields:
                raise ValueError("CHECK_FIELDS_INVALID")
        passed = sum(item["status"] == "PASS" for item in receipts)
        if passed != progress.verified_evidence_count or len(receipts) - passed != progress.remaining_obligations:
            raise ValueError("CHECK_COUNTS_MISMATCH")
        result.update(status="NEEDS_REVALIDATION", executor=executor,
                      candidate_sha256=candidate_sha256, descriptor_sha256=descriptor_sha256,
                      workspace_state_sha256=progress.state_sha256,
                      receipt_sha256=progress.evidence_sha256, receipts=receipts,
                      observed_checks_passed=passed, remaining_obligations=progress.remaining_obligations,
                      reason_codes=["EXECUTOR_OBSERVED_CHECKS_WITHOUT_EXTERNAL_ATTESTATION",
                                    "SOURCE_CONFIG_ENVIRONMENT_AND_DEPENDENCY_LINEAGE_NOT_QUALIFIED"])
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        result["reason_codes"] = [str(exc) if isinstance(exc, ValueError) and len(str(exc)) < 80 else "RECEIPT_INVALID"]
    return _fit(result, max_bytes)
