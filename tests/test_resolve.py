from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from specgraph.graph import CausalTraceabilityMatrix
from specgraph.resolve import resolve_repository, runtime_directory, telemetry_summary


@pytest.fixture(autouse=True)
def isolated_runtime_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TARE_SPECGRAPH_STATE_DIR", str(tmp_path / ".test-specgraph-state"))


def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "specgraph.resolve.IncrementalCacheEngine.detect_staleness",
        lambda **_: SimpleNamespace(is_stale=False),
    )


def _result(path: str, line: int = 1) -> dict[str, object]:
    return {
        "result_type": "literal_text",
        "file_path": path,
        "line": line,
        "column": 1,
        "excerpt": "Needle",
    }


def test_structural_hit_skips_traditional_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def Needle(): pass\n", encoding="utf-8")

    def forbidden(*_):
        raise AssertionError("fallback must not run on a structural hit")

    report = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: (
            "HIT",
            [{"result_type": "symbol", "file_path": "src/app.py", "name": "Needle", "start_line": 1}],
        ),
        fallback_probe=forbidden,
    )

    assert report.status == "HIT"
    assert report.source == "specgraph"
    assert report.primary_status == "HIT"
    assert report.recovery_action == "NONE"


def test_miss_falls_back_indexes_and_reuses_validated_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("Needle lives here\n", encoding="utf-8")

    first = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: ("MISS", []),
        fallback_probe=lambda *_: ("HIT", [_result("src/app.py")]),
    )
    assert first.source == "traditional"
    assert first.primary_status == "MISS"
    assert first.recovery_action == "INDEXED_FALLBACK_EVIDENCE"

    def forbidden(*_):
        raise AssertionError("validated learned evidence must avoid both probes")

    second = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=forbidden,
        fallback_probe=forbidden,
    )
    assert second.source == "learned"
    assert second.recovery_action == "REUSED_VALIDATED"

    telemetry = (runtime_directory(tmp_path) / "search-events.jsonl").read_text(
        encoding="utf-8"
    )
    assert "Needle" not in telemetry
    assert len(telemetry.splitlines()) == 2


def test_changed_bytes_evict_stale_learning_and_relearn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "app.py"
    source.write_text("Needle old\n", encoding="utf-8")

    resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: ("MISS", []),
        fallback_probe=lambda *_: ("HIT", [_result("src/app.py")]),
    )
    source.write_text("Needle current\n", encoding="utf-8")

    report = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: ("MISS", []),
        fallback_probe=lambda *_: ("HIT", [_result("src/app.py")]),
    )

    assert "STALE_LEARNED_EVIDENCE" in report.signals
    assert report.source == "traditional"
    assert report.recovery_action == "INDEXED_FALLBACK_EVIDENCE"


@pytest.mark.parametrize("failure", ["TIMEOUT", "UNAVAILABLE"])
def test_primary_failure_uses_traditional_recovery(
    failure: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("Needle\n", encoding="utf-8")

    def primary(*_):
        if failure == "TIMEOUT":
            raise TimeoutError
        raise RuntimeError

    report = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=primary,
        fallback_probe=lambda *_: ("HIT", [_result("src/app.py")]),
    )

    assert report.status == "HIT"
    assert report.primary_status == failure
    assert failure in report.signals
    assert report.recovery_action == "INDEXED_FALLBACK_EVIDENCE"


def test_stale_cache_is_signaled_but_current_structural_evidence_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "specgraph.resolve.IncrementalCacheEngine.detect_staleness",
        lambda **_: SimpleNamespace(is_stale=True),
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def Needle(): pass\n", encoding="utf-8")

    report = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: (
            "HIT",
            [{"result_type": "symbol", "file_path": "src/app.py", "name": "Needle", "start_line": 1}],
        ),
    )

    assert report.primary_status == "STALE"
    assert report.signals == ("STALE",)
    # The adapter did not bind its coordinates to source bytes. They can be
    # returned under the adapter contract, but cannot become validated hints.
    assert report.recovery_action == "RETURNED_UNCACHED_EVIDENCE"
    assert not (runtime_directory(tmp_path) / "search-recovery-index.json").exists()


def test_final_miss_is_explicit_and_does_not_create_recovery_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    report = resolve_repository(
        tmp_path,
        "Absent",
        structural_probe=lambda *_: ("MISS", []),
        fallback_probe=lambda *_: ("MISS", []),
    )

    assert report.status == "MISS"
    assert report.source == "none"
    assert report.signals == ("MISS", "FALLBACK_MISS")
    index_path = runtime_directory(tmp_path) / "search-recovery-index.json"
    assert not index_path.exists()


def test_real_literal_fallback_is_bounded_to_configured_project_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("Needle source\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("Needle outside\n", encoding="utf-8")

    report = resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: ("MISS", []),
    )

    assert report.source == "traditional"
    assert [result["file_path"] for result in report.results] == ["src/app.py"]
    index = json.loads(
        (runtime_directory(tmp_path) / "search-recovery-index.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(index["entries"]) == 1


def test_neutral_search_paths_are_retrievable_without_governance_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text(
        "OrdinaryOperatorNeedle\n", encoding="utf-8"
    )
    (tmp_path / "specgraph.yaml").write_text(
        "search_paths:\n  - docs\nspec_paths: []\nadr_paths: []\n",
        encoding="utf-8",
    )

    report = resolve_repository(
        tmp_path,
        "OrdinaryOperatorNeedle",
        structural_probe=lambda *_: ("MISS", []),
    )
    matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)

    assert report.status == "HIT"
    assert report.source == "traditional"
    assert report.results[0]["file_path"] == "docs/guide.md"
    assert matrix.specs == {}
    assert matrix.adrs == {}


def test_neutral_search_path_cannot_escape_repository_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    outside = tmp_path.parent / "outside-search-path.md"
    outside.write_text("EscapingNeedle\n", encoding="utf-8")
    (tmp_path / "specgraph.yaml").write_text(
        "search_paths:\n  - ../outside-search-path.md\n",
        encoding="utf-8",
    )

    report = resolve_repository(
        tmp_path,
        "EscapingNeedle",
        structural_probe=lambda *_: ("MISS", []),
    )

    assert report.status == "MISS"
    assert report.results == ()


def test_telemetry_summary_is_actionable_and_contains_no_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _fresh(monkeypatch)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("Needle\n", encoding="utf-8")
    resolve_repository(
        tmp_path,
        "Needle",
        structural_probe=lambda *_: ("MISS", []),
        fallback_probe=lambda *_: ("HIT", [_result("src/app.py")]),
    )
    resolve_repository(tmp_path, "Needle", structural_probe=lambda *_: ("MISS", []))

    summary = telemetry_summary(tmp_path)

    assert summary["event_count"] == 2
    assert summary["rates_pct"]["traditional_recovery"] == 50.0
    assert summary["rates_pct"]["validated_reuse"] == 50.0
    assert "Needle" not in json.dumps(summary)


def test_default_runtime_state_is_outside_the_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("TARE_SPECGRAPH_STATE_DIR")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    repository = tmp_path / "repository"
    repository.mkdir()

    state = runtime_directory(repository)

    assert repository not in state.parents
    assert state.parent.name == "specgraph"
