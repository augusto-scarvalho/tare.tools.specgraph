"""Frozen falsifiers for the X1 experimental selector, outside automatic hooks."""
from copy import deepcopy
import json

import pytest

from tools.context_selection_experiment import select_context


def unit(identity, kind="code", size=20, **kwargs):
    return dict(id=identity, kind=kind, payload={"text": "á" * size},
                distance=0, risk=0, requires=[], required=False) | kwargs


def size(packet):
    return len((json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())


def test_complete_mandatory_and_utf8_exact_boundary():
    """@verifies SPEC-SPECGRAPH-X1:AC-01"""
    mandatory = {"work": "W", "criteria": ["á" * 400]}
    units = [unit("target", size=200, required=True)]
    full = select_context(mandatory, units, role="implementer", policy="simple", max_bytes=16384)
    limit = size(full["packet"])
    assert select_context(mandatory, units, role="implementer", policy="utility-v1", max_bytes=limit)["packet"] == full["packet"]
    with pytest.raises(ValueError, match="mandatory"):
        select_context(mandatory, units, role="planner", policy="simple", max_bytes=limit-1)


def test_useful_small_unit_survives_oversized_optional_unit():
    units = [unit("large", size=2000), unit("small", "tests")]
    result = select_context({}, units, role="auditor", policy="utility-v1", max_bytes=512)
    assert [x["id"] for x in result["packet"]["units"]] == ["small"]
    assert result["trace"]["large"]["decision"] == "BUDGET"


@pytest.mark.parametrize("role,winner", [("planner","intent"),("implementer","code"),("auditor","evidence"),("explorer","code")])
def test_roles_have_distinct_declared_priorities(role, winner):
    units = [unit(k, k, size=70) for k in ("intent","code","tests","dependency","evidence")]
    result = select_context({}, units, role=role, policy="utility-v1", max_bytes=512)
    selected = {x["id"] for x in result["packet"]["units"]}
    assert winner in selected and len(selected) < len(units)
    assert result["trace"][winner]["score"] > 0


def test_distance_and_risk_are_explicit_not_inferred():
    units = [unit("far", "code", distance=4), unit("risk", "evidence", distance=4, risk=3)]
    result = select_context({}, units, role="auditor", policy="utility-v1", max_bytes=2048)
    assert [x["id"] for x in result["packet"]["units"]] == ["risk"]
    assert result["trace"]["far"]["decision"] == "BELOW_THRESHOLD"


def test_dependency_closure_is_whole_or_absent():
    units = [unit("source", size=400), unit("snippet", size=10, requires=["source"])]
    result = select_context({}, units, role="implementer", policy="utility-v1", max_bytes=512)
    assert result["packet"]["units"] == []
    units[1]["required"] = True
    with pytest.raises(ValueError, match="mandatory"):
        select_context({}, units, role="implementer", policy="utility-v1", max_bytes=512)


def test_permutations_and_inputs_preserved_for_utility_policy():
    units = [unit("b"), unit("a"), unit("c", "tests")]
    before = deepcopy(units)
    first = select_context({"root":"R"}, units, role="implementer", policy="utility-v1", max_bytes=512)
    second = select_context({"root":"R"}, list(reversed(units)), role="implementer", policy="utility-v1", max_bytes=512)
    assert first == second and units == before


@pytest.mark.parametrize("units", [[unit("x"),unit("x")], [unit("x",requires=["missing"])], [unit("x",requires=["y"]),unit("y",requires=["x"])]])
def test_duplicate_missing_or_cyclic_dependencies_refuse(units):
    with pytest.raises(ValueError):
        select_context({}, units, role="planner", policy="simple", max_bytes=1024)


@pytest.mark.parametrize("change", [{"distance":-1},{"risk":4},{"kind":"invented"},{"required":"yes"}])
def test_invalid_unit_metadata_refuses(change):
    with pytest.raises(ValueError):
        select_context({}, [unit("x",**change)], role="planner", policy="simple", max_bytes=1024)


def test_candidate_limit_is_enforced_before_selection():
    with pytest.raises(ValueError, match="64"):
        select_context({}, [unit(str(i)) for i in range(65)], role="planner", policy="simple", max_bytes=1024)


def test_packet_and_trace_are_separate_and_budget_is_not_token_claim():
    result = select_context({"authority":"NONE"}, [unit("x")], role="implementer", policy="utility-v1", max_bytes=512)
    assert result["bytes"] == size(result["packet"]) <= 512
    assert "trace" not in result["packet"] and "tokens" not in result


def test_simple_policy_does_not_filter_low_utility_context():
    result = select_context({}, [unit("far", "dependency", distance=4)], role="implementer", policy="simple", max_bytes=512)
    assert len(result["packet"]["units"]) == 1
