from __future__ import annotations

import json
from pathlib import Path

import pytest

from specgraph.cache import IncrementalCacheEngine
from specgraph.cli import main
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.impact import preview_impact


def _project(root: Path) -> None:
    (root / "specs").mkdir()
    (root / "src").mkdir()
    (root / "tests").mkdir()
    (root / "specs" / "SPEC-001.md").write_text(
        """---
id: SPEC-001
title: Impact
status: ACCEPTED
acceptance_criteria:
  AC-01: Preserve impact
---
""",
        encoding="utf-8",
    )
    (root / "src" / "subject.py").write_text(
        'def subject():\n    """@spec SPEC-001 [AC-01]"""\n    return 1\n',
        encoding="utf-8",
    )
    (root / "tests" / "test_subject.py").write_text(
        'import pytest\n\n@pytest.mark.verifies("SPEC-001", "AC-01")\ndef test_subject():\n    assert True\n',
        encoding="utf-8",
    )


def test_impact_requires_initialized_fresh_cache(tmp_path: Path) -> None:
    _project(tmp_path)
    unavailable = preview_impact(tmp_path, "src/subject.py")
    assert unavailable.status == "UNAVAILABLE"

    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)
    (tmp_path / "src" / "subject.py").write_text("def subject(): return 2\n", encoding="utf-8")
    stale = preview_impact(tmp_path, "src/subject.py")
    assert stale.status == "STALE"
    assert stale.stale_files == ("src/subject.py",)


def test_path_and_symbol_impact_are_exact_and_bounded(tmp_path: Path) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)

    path_report = preview_impact(tmp_path, "src/subject.py", max_nodes=2)
    assert path_report.status == "HIT"
    assert path_report.source_kind == "path"
    assert "src/subject.py:subject" in path_report.source_nodes
    assert path_report.total_impacted_nodes >= 2
    assert len(path_report.impacted_nodes) == 2
    assert path_report.truncated is (path_report.total_impacted_nodes > 2)

    symbol_report = preview_impact(tmp_path, "symbol:src/subject.py:subject")
    assert symbol_report.status == "HIT"
    assert symbol_report.source_nodes == ("src/subject.py:subject",)
    assert "src/subject.py:subject" in symbol_report.impacted_nodes

    miss = preview_impact(tmp_path, "src/missing.py")
    assert miss.status == "MISS"


def test_impact_rejects_escape_and_cli_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)

    with pytest.raises(ValueError, match="escapes"):
        preview_impact(tmp_path, "../outside.py")

    code = main(
        ["--root", str(tmp_path), "impact", "src/subject.py", "--format", "json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["schema"] == "tare.tools/specgraph-impact/1"
    assert payload["status"] == "HIT"


def test_impact_output_does_not_mutate_cache(tmp_path: Path) -> None:
    _project(tmp_path)
    CausalTraceabilityMatrix.build(tmp_path, auto_cache=True)
    cache_path = IncrementalCacheEngine.get_cache_path(tmp_path)
    before = cache_path.read_bytes()

    preview_impact(tmp_path, "src/subject.py")

    assert cache_path.read_bytes() == before
