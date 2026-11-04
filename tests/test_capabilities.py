from pathlib import Path
import json

from specgraph.capabilities import capability_catalog, project_doctor
from specgraph.cli import main
from specgraph.config import SpecGraphConfig
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import index_repository
from specgraph.resolve import resolve_repository


def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/example.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
    monkeypatch.setenv("TARE_SPECGRAPH_STATE_DIR", str(tmp_path / "state"))
    return root


def forbid_build(*args, **kwargs):
    raise AssertionError("Metadata/indexed lookup must not build the project")


def test_catalog_does_not_sell_planned_capabilities():
    """@verifies SPEC-SPECGRAPH-C0 [AC-01]"""
    catalog = capability_catalog()
    by_id = {c["id"]: c for c in catalog["capabilities"]}
    assert by_id["typescript_rust"]["status"] in {"PLANNED", "PARTIAL"}
    assert by_id["execution_evidence"]["status"] in {"PLANNED", "PARTIAL"}
    assert all(c["limits"] and c["spec"] and c["work_id"] for c in by_id.values())
    assert catalog["qualification"] == "NOT_A_RELEASE_ATTESTATION"


def test_metadata_doctor_does_not_build_or_claim_governance_pass(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C0 [AC-04]"""
    root = project(tmp_path, monkeypatch)
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", forbid_build)
    report = project_doctor(root)
    assert report["status"] == "NOT_CONFIGURED"
    assert report["mode"] == "metadata"
    assert "snapshot" not in report


def test_cli_search_and_resolve_use_snapshot_without_source_scan(tmp_path, monkeypatch, capsys):
    root = project(tmp_path, monkeypatch)
    indexed = index_repository(root)
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", forbid_build)
    from specgraph.cache import IncrementalCacheEngine
    monkeypatch.setattr(IncrementalCacheEngine, "detect_staleness", forbid_build)
    assert main(["--root", str(root), "search", "answer", "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["source"] == "published_snapshot"
    assert result["results"][0]["symbol_id"] == "src/example.py:answer"
    resolved = resolve_repository(root, "answer")
    assert resolved.source == "specgraph"
    assert resolved.snapshot["content_hash"] == indexed["content_hash"]
    assert resolved.snapshot["freshness"]["current_worktree"] == "NOT_CHECKED_ON_READ"


def test_snapshot_search_does_not_misrepresent_live_source(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    first = index_repository(root)
    (root / "src/example.py").write_text("def renamed():\n    return 43\n", encoding="utf-8")
    report = resolve_repository(root, "answer")
    assert report.snapshot["content_hash"] == first["content_hash"]
    assert "PUBLISHED_SNAPSHOT_NOT_LIVE_VERIFIED" in report.signals


def test_indexed_scope_and_python_roots_roundtrip(tmp_path):
    config = SpecGraphConfig(governed_paths=["src/strict"], indexed_paths=["src", "lib"],
                             python_source_roots=["lib", "src"])
    (tmp_path / "specgraph.yaml").write_text(config.dump_yaml(), encoding="utf-8")
    loaded = SpecGraphConfig.load(tmp_path)
    assert loaded.indexed_paths == ["src", "lib"]
    assert loaded.governed_paths == ["src/strict"]
    assert loaded.python_source_roots == ["lib", "src"]
