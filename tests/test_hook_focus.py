"""Exact selection, atomic context units and noise reduction for C7."""
import json

import pytest

from specgraph.hook_context import context_hint
from specgraph.indexer import index_repository
from specgraph.lifecycle import record_changes


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "specs").mkdir()
    (root / "tests").mkdir()
    (root / "src/lib.py").write_text('def leaf():\n    """@spec SPEC-ONE:AC-01"""\n    return 1\ndef caller():\n    """@spec SPEC-ONE:AC-01"""\n    return leaf() + leaf()\n')
    (root / "specs/SPEC-ONE.md").write_text("---\nid: SPEC-ONE\ntitle: Exact intent\nacceptance_criteria:\n  AC-01: Return one without mutation\n---\n")
    (root / "tests/test_leaf.py").write_text('def test_leaf():\n    """@verifies SPEC-ONE:AC-01"""\n    assert True\n')
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    return root, state


def hint(project, **kwargs):
    root, state = project
    return context_hint(root, role="implementer", event="pre", state_dir=state, timeout_ms=1000, **kwargs)


def test_exact_symbol_deduplicates_call_neighbors_and_declares_test_evidence(project):
    """@verifies SPEC-SPECGRAPH-C7:AC-02
    """
    result = hint(project, symbol_id="src/lib.py:caller")
    assert len(result["symbols"]) == 1
    assert result["symbols"][0]["neighbors"] == [{"id": "src/lib.py:leaf", "relation": "DEPENDS_ON_STATIC", "direction": "out"}]
    tests = result["test_references"]
    assert len(tests) == 1 and tests[0]["ids"] == ["tests/test_leaf.py:test_leaf"]
    assert tests[0]["evidence"] == "DECLARATION_ONLY_NOT_EXECUTED_COVERAGE"
    assert result["snippets"][0]["text"].splitlines()[-1] == "    return leaf() + leaf()"
    assert result["snippets"][0]["complete_symbol"] is True


def test_missing_exact_symbol_and_conflicting_file_do_not_select_candidates(project):
    assert hint(project, symbol_id="caller")["status"] == "MISS"
    assert hint(project, symbol_id="src/lib.py:caller", files=("src/other.py",))["status"] == "OUTSIDE_SCOPE"


def test_planner_receives_whole_declared_intent_without_inventing_work(project):
    root, state = project
    result = context_hint(root, role="planner", event="pre", symbol_id="src/lib.py:leaf", state_dir=state, timeout_ms=1000)
    assert result["work_id"] is None
    assert result["declared_intents"][0]["acceptance_criteria"] == {"AC-01": "Return one without mutation"}
    assert result["declared_intents"][0]["origin"] == "DECLARED_BINDING_NOT_WORK_SELECTION"
    assert "snippets" not in result


def test_unrelated_event_churn_silences_but_selected_file_edit_does_not(project):
    root, state = project
    record_changes(root, ["src/other.py"], state_dir=state)
    first = hint(project, files=("src/lib.py",))
    assert first["dirty_paths"] == []
    record_changes(root, ["src/other-again.py"], state_dir=state)
    assert hint(project, files=("src/lib.py",), previous_digest=first["digest"])["status"] == "SILENT"
    record_changes(root, ["src/lib.py"], state_dir=state)
    assert hint(project, files=("src/lib.py",), previous_digest=first["digest"])["status"] == "HIT"


def test_automatic_units_are_omitted_whole_when_budget_is_small(project):
    result = hint(project, symbol_id="src/lib.py:caller", max_bytes=1024)
    assert len((json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()) <= 1024
    if "snippets" in result:
        assert result["snippets"][0]["text"].splitlines()[-1] == "    return leaf() + leaf()"
    else:
        assert result.get("omitted_items", 0) > 0 or result["status"] == "BUDGET_REQUIRED"
