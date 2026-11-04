"""Actual-token accounting refuses missing usage and correctness regressions."""
import importlib.util
import json
from pathlib import Path

from specgraph.capabilities import _effectiveness_observation


def module():
    spec = importlib.util.spec_from_file_location("hook_tasks", Path(__file__).parents[1] / "tools/qualify_hook_tasks.py")
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_provider_tokens_include_cached_input_and_missing_is_not_free():
    """@verifies SPEC-SPECGRAPH-C7:AC-05
    """
    m = module()
    assert m.measure_usage({"usage": {}}) is None
    assert m.measure_usage({"usage": {"input_tokens": True, "output_tokens": 1}}) is None
    value = m.measure_usage({"usage": {"input_tokens": 3, "cache_creation_input_tokens": 5,
                                       "cache_read_input_tokens": 11, "output_tokens": 7}})
    assert value["total_tokens"] == 26


def test_saving_bytes_cannot_override_failed_task_or_slower_agent():
    m = module()
    records = []
    for arm in ("file_context", "hook_context"):
        records.append({"role": "implementer", "arm": arm, "success": True, "retries": 0,
                        "interventions": 0, "wall_ms": 10 if arm == "file_context" else 20,
                        "usage": {"total_tokens": 100 if arm == "file_context" else 50}})
    assert m.summarize(records)["implementer"]["recommendation"].startswith("ON_DEMAND")
    records[1]["wall_ms"] = 5
    assert m.summarize(records)["implementer"]["recommendation"] == "KEEP_SCOPED_ADVISORY"
    records[1]["success"] = False
    assert m.summarize(records)["implementer"]["recommendation"].startswith("ON_DEMAND")


def test_doctor_reports_experiment_without_claiming_production_attestation(tmp_path):
    roles = {role: {"arms": {arm: {"attempts": 1, "successes": 1} for arm in ("file_context", "hook_context")}}
             for role in ("planner", "implementer", "auditor", "explorer")}
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema": "tare.tools/hook-paired-evaluation/1", "status": "OBSERVED", "roles": roles}))
    result = _effectiveness_observation(path)
    assert result["status"] == "REPORTED_EXPERIMENT"
    assert result["production_effectiveness"] == "NOT_ESTABLISHED" and result["attestation"] == "NOT_VERIFIED"
    roles["planner"]["arms"]["file_context"]["successes"] = 2
    path.write_text(json.dumps({"schema": "tare.tools/hook-paired-evaluation/1", "status": "OBSERVED", "roles": roles}))
    assert _effectiveness_observation(path)["status"] == "UNAVAILABLE"
