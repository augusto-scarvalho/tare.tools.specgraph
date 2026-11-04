from specgraph.indexer import index_repository
from specgraph.query import project_query, serialize_query


def make_project(tmp_path, sources):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    for name, body in sources.items():
        (root / "src" / name).write_text(body, encoding="utf-8")
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    return root, state, metadata


def test_empty_search_is_scoped_to_snapshot_not_universal(tmp_path):
    """@verifies SPEC-SPECGRAPH-X2
    """
    root, state, metadata = make_project(tmp_path, {"a.py": "def known(): return 1\n"})
    response = project_query(root, "nonexistent", state_dir=state)
    assert response["status"] == "NOT_FOUND_IN_SNAPSHOT"
    assert response["absence"]["universal"] is False
    assert response["coverage"]["semantic_completeness"] == "NOT_CLAIMED"
    assert "excluded_paths" in response["coverage"]
    assert response["content_hash"] == metadata["content_hash"]
    assert response["limits"]["max_bytes"] == 8192


def test_parse_failure_cannot_prove_absence(tmp_path):
    root, state, _ = make_project(tmp_path, {"bad.py": "def unknowable(:\n"})
    response = project_query(root, "unknowable", state_dir=state)
    assert response["status"] == "UNKNOWN"
    assert response["coverage"]["status"] == "PARTIAL"
    assert response["coverage"]["parse_error_count"] > 0
    assert response["coverage"]["diagnostics"] == "specgraph doctor --format json"


def test_dynamic_reference_is_visible_without_inventing_edge(tmp_path):
    """@verifies SPEC-SPECGRAPH-X2
    """
    root, state, _ = make_project(tmp_path, {"dynamic.py": "def execute(callback):\n    return callback()\n"})
    response = project_query(root, "execute", mode="context", state_dir=state)
    assert response["status"] == "PARTIAL"
    references = response["results"][0]["references"]
    assert any(ref["name"] == "callback" and ref["status"] != "resolved" for ref in references)
    assert response["coverage"]["unresolved_references"]["count"] >= 1
    neighbors = project_query(root, "execute", mode="neighbors", state_dir=state)
    assert neighbors["status"] == "UNKNOWN"
    assert neighbors["results"] == []


def test_homonyms_require_disambiguation_even_with_limit_one(tmp_path):
    root, state, _ = make_project(tmp_path, {"a.py": "def execute(): return 1\n", "b.py": "def execute(): return 2\n"})
    for limit in (1, 20):
        ambiguous = project_query(root, "execute", mode="symbol", limit=limit, state_dir=state)
        assert ambiguous["status"] == "AMBIGUOUS"
        assert all(item["reason"] == "selector_candidate" for item in ambiguous["results"])
    selected = project_query(root, "src/b.py:execute", mode="symbol", state_dir=state)
    assert selected["results"][0]["file_path"] == "src/b.py"


def test_unindexed_file_selector_is_unknown(tmp_path):
    root, state, _ = make_project(tmp_path, {"a.py": "def execute(): return 1\n"})
    response = project_query(root, "vendor/hidden.py:execute", mode="symbol", state_dir=state)
    assert response["status"] == "UNKNOWN"
    assert response["absence"]["universal"] is False


def test_exact_symbol_identity_wins_over_substring_candidates(tmp_path):
    root, state, _ = make_project(tmp_path, {"a.py": "def f(): return 1\ndef foo(): return 2\n"})
    response = project_query(root, "src/a.py:f", state_dir=state)
    assert len(response["results"]) == 1
    assert response["results"][0]["symbol_id"] == "src/a.py:f"
    assert response["results"][0]["reason"] == "exact_id"


def test_byte_omission_cannot_turn_ambiguity_into_unique_selection(tmp_path):
    root, state, _ = make_project(tmp_path, {"a.py": "def same(): return 1\n", "b.py": "def same(): return 2\n"})
    full = project_query(root, "same", mode="symbol", state_dir=state)
    budget = len(serialize_query(full).encode("utf-8")) - 20
    bounded = project_query(root, "same", mode="symbol", max_bytes=budget, state_dir=state)
    assert bounded["status"] == "AMBIGUOUS"
    assert len(bounded["results"]) < len(full["results"])
    assert any(item["reason"] == "max_bytes" for item in bounded["omitted"])


def test_complete_syntax_does_not_supply_dynamic_target(tmp_path):
    """@verifies SPEC-SPECGRAPH-X2:AC-03"""
    from specgraph.hook_context import context_hint

    root, state, metadata = make_project(tmp_path, {
        "dynamic.py": "def execute(callback):\n    return callback()\n"})
    response = project_query(root, "src/dynamic.py:execute", mode="context", state_dir=state)
    assert response["coverage"]["status"] == "COMPLETE_WITHIN_SCOPE"
    reference, = [r for r in response["results"][0]["references"] if r["kind"] == "call"]
    assert (reference["name"], reference["line"], reference["target"]) == ("callback", 2, None)
    report = context_hint(root, role="implementer", event="pre", query="execute",
                          state_dir=state, timeout_ms=1000, max_bytes=8192)
    assert report["status"] == "HIT" and report["snapshot"] == metadata["content_hash"]
    assert report["substitute_assessment"]["status"] == "RECOVERY_REQUIRED"
    assert report["substitute_assessment"]["reasons"][0]["code"] == "UNRESOLVED_REFERENCE"


def test_completion_contracts_cli_preserves_original_criteria():
    """Owner contract check, not implementation or downstream qualification."""
    import json
    from pathlib import Path
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "tools/check_completion_contracts.py"],
                            cwd=root, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "PASS" and report["errors"] == []
    # Provenance only: no assertion depends on a private candidate object ID.
    import warnings
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=10)
        baseline = head.stdout.strip() if head.returncode == 0 else "UNAVAILABLE"
    except (OSError, subprocess.TimeoutExpired):
        baseline = "UNAVAILABLE"
    warnings.warn("X2 candidate HEAD (provenance, not test oracle): " + baseline, UserWarning)
