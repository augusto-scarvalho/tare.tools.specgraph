"""X1 experiment: pure bounded selection; deliberately outside automatic hooks.

Caller supplies source-qualified units. Scores are heuristics, never execution
evidence. A trace is returned separately so reasoning does not bloat the prompt.
"""
from __future__ import annotations

import json
from typing import Any

WEIGHTS = {
    "planner": {"intent": 100, "code": 30, "tests": 30, "dependency": 70, "evidence": 20},
    "implementer": {"intent": 70, "code": 100, "tests": 80, "dependency": 60, "evidence": 20},
    "auditor": {"intent": 80, "code": 60, "tests": 90, "dependency": 30, "evidence": 100},
    "explorer": {"intent": 50, "code": 100, "tests": 20, "dependency": 90, "evidence": 20},
}
THRESHOLD = 40


def wire(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def select_context(mandatory: dict, units: list[dict], *, role: str,
                   policy: str = "simple", max_bytes: int = 8192) -> dict:
    """Select complete units with mandatory and transitive dependency closure.

    @spec SPEC-SPECGRAPH-X1
    """
    if role not in WEIGHTS or policy not in {"simple", "utility-v1"}:
        raise ValueError("unsupported role or experimental policy")
    if type(max_bytes) is not int or not 512 <= max_bytes <= 16384:
        raise ValueError("max_bytes must be an integer between 512 and 16384")
    if not isinstance(mandatory, dict) or not isinstance(units, list) or len(units) > 64:
        raise ValueError("mandatory object and at most 64 units required")
    if len(wire([mandatory, units])) > 1048576:
        raise ValueError("input exceeds 1 MiB experimental budget")
    # Round-trip gives the result independent ownership and rejects non-JSON input.
    mandatory, units = json.loads(wire([mandatory, units]))
    by_id = {}
    for unit in units:
        identity = unit.get("id") if isinstance(unit, dict) else None
        if not isinstance(identity, str) or not 1 <= len(identity) <= 256 or identity in by_id:
            raise ValueError("unit IDs must be unique bounded strings")
        if unit.get("kind") not in WEIGHTS[role] or not isinstance(unit.get("payload"), dict):
            raise ValueError("unit kind and payload required")
        for name, maximum in (("distance", 4), ("risk", 3)):
            if type(unit.get(name)) is not int or not 0 <= unit[name] <= maximum:
                raise ValueError("invalid unit " + name)
        if type(unit.get("required")) is not bool:
            raise ValueError("required must be boolean")
        requirements = unit.get("requires")
        if not isinstance(requirements, list) or len(requirements) > 64 or any(not isinstance(x, str) for x in requirements):
            raise ValueError("bounded requires list required")
        by_id[identity] = unit
    closures = {}

    def closure(identity, visiting):
        if identity not in by_id or identity in visiting:
            raise ValueError("missing or cyclic unit dependency")
        if identity not in closures:
            found = {identity}
            for dependency in by_id[identity]["requires"]:
                found.update(closure(dependency, visiting | {identity}))
            closures[identity] = found
        return closures[identity]

    for identity in by_id:
        closure(identity, set())

    def packet(ids):
        return {"mandatory": mandatory, "units": [
            {"id": identity, "kind": by_id[identity]["kind"], "payload": by_id[identity]["payload"]}
            for identity in sorted(ids)]}

    selected = set()
    for identity, unit in by_id.items():
        if unit["required"]:
            selected.update(closures[identity])
    if len(wire(packet(selected))) > max_bytes:
        raise ValueError("mandatory context exceeds byte budget")
    traces = {}
    for identity, unit in by_id.items():
        risk_weight = (20 if role == "auditor" else 10) * unit["risk"]
        score = WEIGHTS[role][unit["kind"]] // (unit["distance"] + 1) + risk_weight
        traces[identity] = {"score": score, "role_weight": WEIGHTS[role][unit["kind"]],
                            "distance": unit["distance"], "risk_weight": risk_weight,
                            "decision": "REQUIRED" if identity in selected else "PENDING"}
    order = list(by_id) if policy == "simple" else sorted(by_id, key=lambda x: (-traces[x]["score"], x))
    for identity in order:
        if identity in selected:
            continue
        if policy == "utility-v1" and traces[identity]["score"] < THRESHOLD:
            traces[identity]["decision"] = "BELOW_THRESHOLD"
            continue
        proposed = selected | closures[identity]
        if len(wire(packet(proposed))) <= max_bytes:
            for added in proposed - selected:
                traces[added]["decision"] = "SELECTED" if added == identity else "DEPENDENCY"
            selected = proposed
        else:
            traces[identity]["decision"] = "BUDGET"
    output = packet(selected)
    return {"packet": output, "bytes": len(wire(output)), "trace": traces,
            "policy": policy, "role": role, "authority": "NONE"}
