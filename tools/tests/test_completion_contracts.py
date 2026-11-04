import json
from pathlib import Path
import shutil

import pytest

from tools.check_completion_contracts import check


@pytest.fixture
def contracts(tmp_path):
    root = Path(__file__).resolve().parents[2]
    for name in ("docs/specs", "docs/adr", "planning"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    for path in (root / "docs/specs").glob("SPEC-SPECGRAPH-*.md"):
        shutil.copyfile(path, tmp_path / "docs/specs" / path.name)
    for path in (root / "docs/adr").glob("ADR-SPECGRAPH-*.md"):
        shutil.copyfile(path, tmp_path / "docs/adr" / path.name)
    shutil.copyfile(root / "planning/completion-program.json", tmp_path / "planning/completion-program.json")
    return tmp_path


def change_program(root, identity, **changes):
    path = root / "planning/completion-program.json"
    data = json.loads(path.read_text())
    next(p for p in data["packages"] if p["id"] == identity).update(changes)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize("identity", ["X1", "X3", "X4", "X5"])
def test_experiment_status_cannot_change_without_program_declaration(contracts, identity):
    assert check(contracts)["status"] == "PASS"
    program = json.loads((contracts / "planning/completion-program.json").read_text(encoding="utf-8"))
    declared = next(p for p in program["packages"] if p["id"] == identity).get("contract_status", "PROPOSED")
    other = "PROPOSED" if declared == "ACCEPTED" else "ACCEPTED"
    path = contracts / f"docs/specs/SPEC-SPECGRAPH-{identity}.md"
    before = path.read_text(encoding="utf-8")
    after = before.replace(f'"status": "{declared}"', f'"status": "{other}"', 1)
    assert after != before, "The falsifier must actually change the current declared status"
    path.write_text(after, encoding="utf-8")
    assert f"Unexpected contract status for {identity}" in check(contracts)["errors"]


def test_program_cannot_silently_downgrade_required_contract(contracts):
    change_program(contracts, "C0", contract_status="PROPOSED")
    path = contracts / "docs/specs/SPEC-SPECGRAPH-C0.md"
    path.write_text(path.read_text().replace('"status": "ACCEPTED"', '"status": "PROPOSED"'))
    assert "Required contract must remain accepted for C0" in check(contracts)["errors"]


def test_unknown_declared_status_is_rejected(contracts):
    change_program(contracts, "X1", contract_status="QUALIFIED")
    assert "Invalid declared contract status for X1" in check(contracts)["errors"]
