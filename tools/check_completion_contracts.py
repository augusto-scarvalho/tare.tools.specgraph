"""Validate program-to-SPEC/ADR contracts without granting completion authority."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from specgraph.adr_parser import AdrParser
from specgraph.spec_parser import SpecParser


def check(root: Path) -> dict:
    program = json.loads((root / "planning/completion-program.json").read_text(encoding="utf-8"))
    errors = []
    packages = {p["id"]: p for p in program["packages"]}
    if len(packages) != len(program["packages"]):
        errors.append("Duplicate package identity")
    if not program.get("authorization", {}).get("does_not_attest_completion"):
        errors.append("Program authorization must not attest completion")
    adrs = {}
    visiting, visited = set(), set()

    def walk(pid):
        if pid in visiting:
            errors.append(f"Dependency cycle at {pid}")
            return
        if pid in visited:
            return
        visiting.add(pid)
        for dep in packages[pid]["depends_on"]:
            if dep not in packages:
                errors.append(f"Unknown dependency {dep}")
            else:
                walk(dep)
        visiting.remove(pid)
        visited.add(pid)

    for pid, package in packages.items():
        walk(pid)
        repo, relative = package["spec_ref"].split(":", 1)
        if repo != "tare.tools.specgraph":
            errors.append(f"Unexpected SPEC owner {repo}")
            continue
        nodes, parse_errors = SpecParser.parse_file(root / relative, root)
        errors.extend(parse_errors)
        if len(nodes) != 1:
            errors.append(f"Missing SPEC for {pid}")
            continue
        node = nodes[0]
        if node.id != f"SPEC-SPECGRAPH-{pid}" or node.acceptance_criteria != package["acceptance_criteria"]:
            errors.append(f"SPEC identity/criteria drift for {pid}")
        if node.raw_frontmatter.get("work_id") != package["work_id"]:
            errors.append(f"SPEC Work mismatch for {pid}")
        # An operator can admit a concrete experiment without promoting its
        # result or changing the package's experimental nature.
        expected_status = package.get("contract_status", "PROPOSED" if package["kind"] == "experiment" else "ACCEPTED")
        if expected_status not in {"PROPOSED", "ACCEPTED"}:
            errors.append(f"Invalid declared contract status for {pid}")
        if package["kind"] != "experiment" and expected_status != "ACCEPTED":
            errors.append(f"Required contract must remain accepted for {pid}")
        if node.status != expected_status:
            errors.append(f"Unexpected contract status for {pid}")
        for ref in package["adr_refs"]:
            _, path = ref.split(":", 1)
            if path not in adrs:
                found, issues = AdrParser.parse_file(root / path, root)
                errors.extend(issues)
                if len(found) != 1:
                    errors.append(f"Missing ADR {path}")
                    continue
                adrs[path] = found[0]
            if node.id not in adrs[path].governed_specs:
                errors.append(f"ADR does not govern {node.id}: {path}")
    if "X2" not in packages.get("C8", {}).get("depends_on", []):
        errors.append("Mandatory coverage X2 must gate C8")
    return {"schema": "tare.tools/specgraph-program-check/1", "status": "FAIL" if errors else "PASS",
            "packages": len(packages), "specs": len(packages), "adrs": len(adrs),
            "authority": "NONE", "scope": "Document contracts, not implementation qualification", "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result = check(parser.parse_args().root.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
