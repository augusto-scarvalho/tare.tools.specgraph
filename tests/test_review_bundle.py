"""Git diff selection is checked against one current published snapshot."""
import hashlib
from pathlib import Path
import subprocess

import pytest

from specgraph.indexer import index_repository
from specgraph.review import review_bundle, serialize_review_bundle


def git(root, *args):
    return subprocess.run(["git", "-c", f"core.hooksPath={root / 'empty-hooks'}", "-c", "user.name=SpecGraph test",
                           "-c", "user.email=test@example.invalid", "-C", str(root), *args],
                          check=True, capture_output=True, timeout=10).stdout.decode().strip()


@pytest.fixture
def changed_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True); (root / "specs").mkdir()
    (root / "src/code.py").write_text('def execute():\n    """@spec SPEC-ONE"""\n    return 1\n', newline="\n")
    (root / "specs/SPEC-ONE.md").write_text('---\nid: SPEC-ONE\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-01: preserve whole criteria\n---\n# Required complete intent\n')
    git(root, "init", "-q"); git(root, "add", "."); git(root, "commit", "-qm", "baseline")
    (root / "src/code.py").write_text('def execute():\n    """@spec SPEC-ONE"""\n    return 2\n', newline="\n")
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    return root, state, metadata


def test_review_diff_uses_current_snapshot_and_preserves_intent(changed_repo):
    root, state, metadata = changed_repo
    result = review_bundle(root, "HEAD", state_dir=state, max_bytes=65536)
    assert result["status"] == "READY"
    assert result["content_hash"] == metadata["content_hash"]
    assert result["selector"]["historical_graph"] is False
    assert result["selector"]["commit"] == git(root, "rev-parse", "HEAD")
    assert result["symbols"][0]["source_snippet"]["body"].endswith("return 2\n")
    assert result["documents"][0]["acceptance_criteria"] == {"AC-01": "preserve whole criteria"}
    assert "Required complete intent" in result["documents"][0]["body"]
    assert result["freshness"]["other_files"] == "NOT_CHECKED"


def test_stale_source_is_rejected_before_mixing_snapshot_context(changed_repo):
    root, state, _ = changed_repo
    (root / "src/code.py").write_text("def changed_after_index():\n    return 3\n")
    result = review_bundle(root, "HEAD", state_dir=state)
    assert result["status"] == "SOURCE_MISMATCH"
    assert result["symbols"] == result["documents"] == []
    assert result["mismatches"][0]["reason"] == "CONTENT_DIFFERS_FROM_SNAPSHOT"


def test_budget_refuses_whole_units_and_untracked_is_explicit(changed_repo):
    root, state, _ = changed_repo
    (root / "untracked.txt").write_text("not selected by Git diff")
    result = review_bundle(root, "HEAD", state_dir=state, max_bytes=1024)
    assert len(serialize_review_bundle(result).encode()) <= 1024
    assert result["status"] == "BUDGET_EXCEEDED"
    large = review_bundle(root, "HEAD", state_dir=state, max_bytes=65536)
    assert large["selector"]["untracked_files"] == "NOT_SELECTED"
    assert all(f["path"] != "untracked.txt" for f in large["files"])


def test_deleted_file_requires_reindex_and_does_not_fabricate_historical_ast(changed_repo):
    root, state, _ = changed_repo
    (root / "src/code.py").unlink()
    assert review_bundle(root, "HEAD", state_dir=state)["status"] == "SOURCE_MISMATCH"
    index_repository(root, state_dir=state)
    result = review_bundle(root, "HEAD", state_dir=state)
    assert result["status"] == "PARTIAL"
    assert result["files"][0]["historical_symbols"] == "NOT_AVAILABLE"
    assert result["symbols"] == []


def test_source_changes_during_bundle_are_detected(changed_repo, monkeypatch):
    root, state, _ = changed_repo
    from specgraph import query
    unit = query._unit
    def racing_unit(*args, **kwargs):
        result = unit(*args, **kwargs)
        (root / "src/code.py").write_text("def racing_change():\n    return 99\n")
        return result
    monkeypatch.setattr(query, "_unit", racing_unit)
    result = review_bundle(root, "HEAD", state_dir=state)
    assert result["status"] == "SOURCE_MISMATCH"
    assert result["symbols"] == result["documents"] == []


def test_invalid_ref_and_selection_budget_are_rejected(changed_repo):
    root, state, _ = changed_repo
    with pytest.raises(ValueError):
        review_bundle(root, "--help", state_dir=state)
    with pytest.raises(ValueError):
        review_bundle(root, "missing-ref", state_dir=state)
    with pytest.raises(ValueError):
        review_bundle(root, "HEAD", state_dir=state, max_files=0)
