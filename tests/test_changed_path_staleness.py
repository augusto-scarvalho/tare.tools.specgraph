from __future__ import annotations

import json
from pathlib import Path

import pytest

from specgraph.cli import main
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.staleness import assess_changed_paths


def _project(root: Path) -> None:
    (root / "specs").mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "specs" / "SPEC-1.md").write_text(
        "---\nid: SPEC-1\ntitle: S\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-1: ok\n---\n",
        encoding="utf-8",
    )
    (root / "src" / "a.py").write_text(
        'def a():\n    """@spec SPEC-1 [AC-1]"""\n    return 1\n', encoding="utf-8"
    )
    (root / "tests" / "test_a.py").write_text(
        'import pytest\n@pytest.mark.verifies("SPEC-1", "AC-1")\ndef test_a(): assert True\n',
        encoding="utf-8",
    )


def test_changed_path_staleness_requires_cache(tmp_path: Path) -> None:
    _project(tmp_path)
    report = assess_changed_paths(tmp_path, ["src/a.py"])
    assert report.status == "UNAVAILABLE"


def test_changed_path_staleness_is_scoped_and_bounded(tmp_path: Path) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)
    (tmp_path / "src" / "a.py").write_text("def a(): return 2\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("outside configured paths", encoding="utf-8")

    report = assess_changed_paths(tmp_path, ["src/a.py"], max_nodes=1)

    assert report.status == "STALE"
    assert report.observed_changed_paths == ("src/a.py",)
    assert report.total_impacted_nodes >= 1
    assert len(report.impacted_nodes) == 1
    assert report.truncated is (report.total_impacted_nodes > 1)
    assert report.other_changed_paths_count == 0


def test_no_op_path_is_fresh_even_when_another_path_changed(tmp_path: Path) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)
    (tmp_path / "specs" / "SPEC-1.md").write_text(
        "---\nid: SPEC-1\ntitle: changed\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-1: ok\n---\n",
        encoding="utf-8",
    )

    report = assess_changed_paths(tmp_path, ["src/a.py"])

    assert report.status == "FRESH"
    assert report.repository_stale is True
    assert report.other_changed_paths_count == 1


def test_staleness_cli_and_escape(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)
    code = main(
        [
            "--root",
            str(tmp_path),
            "staleness",
            "--changed-path",
            "src/a.py",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema"] == "tare.tools/specgraph-staleness/1"
    assert payload["status"] == "FRESH"

    with pytest.raises(ValueError, match="escapes"):
        assess_changed_paths(tmp_path, ["../outside.py"])
