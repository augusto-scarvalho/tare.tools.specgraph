"""Role selection, silence and bounded snapshot-only hook advice (C7)."""
import json
import hashlib
import os
from pathlib import Path

import pytest

from specgraph.hook_context import context_hint, hook_doctor
from specgraph.indexer import index_repository
from specgraph.storage import RepositoryIndex


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/lib.py").write_text('def leaf(): return 1\ndef caller():\n    """@spec SPEC-ONE"""\n    return leaf()\n')
    (root / "specs").mkdir()
    (root / "specs/SPEC-ONE.md").write_text("---\nid: SPEC-ONE\ntitle: Explicit intent\nacceptance_criteria:\n  AC-01: Return one\n---\n# Intent\n")
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    return root, state


@pytest.mark.parametrize("role", ["planner", "implementer", "auditor", "explorer"])
def test_role_context_has_explicit_identity_and_deduplicates(indexed, role):
    """@verifies SPEC-SPECGRAPH-C7
    """
    root, state = indexed
    result = context_hint(root, role=role, event="pre", files=("src/lib.py",),
                          work_id="EXPLICIT-WORK", source_ref="library:explicit-source", state_dir=state,
                          timeout_ms=1000)
    assert result["status"] == "HIT"
    assert result["work_id"] == "EXPLICIT-WORK"
    assert result["authority"] == "NONE"
    assert result["root"] == str(root)
    assert result["snapshot"]
    assert result["coverage"]["current_worktree"] == "NOT_CHECKED"
    if role in {"planner", "auditor"}:
        assert "declared_specs" in result["symbols"][0]
    else:
        assert "neighbors" in result["symbols"][0]
    if role == "auditor":
        assert result["symbols"][0]["evidence"] == "NOT_ATTESTED"
    again = context_hint(root, role=role, event="pre", files=("src/lib.py",),
                          work_id="EXPLICIT-WORK", source_ref="library:explicit-source",
                          previous_digest=result["digest"], state_dir=state, timeout_ms=1000)
    assert again["status"] == "SILENT"


def test_known_paths_require_no_search_or_source_scan(indexed, monkeypatch):
    root, state = indexed
    def forbidden(*args, **kwargs):
        raise AssertionError("synchronous hook tried source scan or rediscovery")
    monkeypatch.setattr(RepositoryIndex, "search", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(os, "scandir", forbidden)
    result = context_hint(root, role="implementer", event="pre", files=("src/lib.py",),
                          state_dir=state, timeout_ms=1000)
    assert result["status"] == "HIT"
    assert result["work_id"] is None


def test_explorer_digest_identifies_indexed_bytes_even_after_source_changes(indexed):
    root, state = indexed
    source = root / 'src/lib.py'
    indexed_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_bytes(b'def changed(): return 2\n')
    result = context_hint(root, role='explorer', event='pre', files=('src/lib.py',),
                          state_dir=state, timeout_ms=1000)
    assert result['status'] == 'HIT'
    assert all(symbol['source_sha256'] == indexed_sha for symbol in result['symbols'])
    assert indexed_sha != hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['coverage']['current_worktree'] == 'NOT_CHECKED'


def test_selected_criteria_are_complete_or_explicit_budget_refusal(indexed):
    root, state = indexed
    result = context_hint(root, role="planner", event="start", spec_id="SPEC-ONE",
                          state_dir=state, timeout_ms=1000)
    assert result["intent"]["acceptance_criteria"] == {"AC-01": "Return one"}
    spec = root / "specs/SPEC-ONE.md"
    spec.write_text("---\nid: SPEC-LARGE\ntitle: Large\nacceptance_criteria:\n  AC-01: " + "mandatory " * 1000 + "\n---\n")
    index_repository(root, state_dir=state)
    bounded = context_hint(root, role="planner", event="start", spec_id="SPEC-LARGE",
                           max_bytes=1024, state_dir=state, timeout_ms=1000)
    assert bounded["status"] == "BUDGET_REQUIRED"
    assert "intent" not in bounded
    assert len((json.dumps(bounded, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= 1024


def test_missing_snapshot_scope_and_miss_allow_recovery_without_work_inference(indexed, tmp_path):
    root, state = indexed
    absent = context_hint(tmp_path / "unindexed", role="explorer", event="start", state_dir=state)
    assert absent["status"] == "UNAVAILABLE"
    assert absent["work_id"] is None
    missing = context_hint(root, role="explorer", event="pre", query="notfoundatall",
                           state_dir=state, timeout_ms=1000)
    assert missing["status"] == "MISS"
    escaped = context_hint(root, role="implementer", event="pre", files=("../other.py",), state_dir=state)
    assert escaped["status"] == "OUTSIDE_SCOPE"


@pytest.mark.parametrize("event", ["post", "stop", "end"])
def test_notifications_never_open_snapshot_or_create_loop(indexed, monkeypatch, event):
    root, state = indexed
    def forbidden(*args, **kwargs):
        raise AssertionError("notification attempted snapshot work")
    monkeypatch.setattr(RepositoryIndex, "__enter__", forbidden)
    assert context_hint(root, role="auditor", event=event, state_dir=state)["status"] == "SILENT"


def test_dirty_observation_is_visible_and_changes_digest(indexed):
    root, state = indexed
    clean = context_hint(root, role="implementer", event="pre", files=("src/lib.py",), state_dir=state,
                         timeout_ms=1000)
    dirty = context_hint(root, role="implementer", event="pre", files=("src/lib.py",), state_dir=state,
                         previous_digest=clean["digest"], dirty_paths=("src/lib.py",), timeout_ms=1000)
    assert dirty["status"] == "HIT"
    assert dirty["coverage"]["current_worktree"] == "DIRTY_OBSERVED"
    assert dirty["digest"] != clean["digest"]


def test_doctor_does_not_promote_registration_to_execution_or_effectiveness():
    report = hook_doctor()
    assert report["service"] == "IMPLEMENTED"
    assert report["host_delivery"] == "NOT_OBSERVED"
    assert report["effectiveness"] == "NOT_MEASURED"


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf"), 6])
def test_index_reader_timeout_is_finite_and_bounded(indexed, timeout):
    root, state = indexed
    with pytest.raises(ValueError, match="timeout_s"):
        RepositoryIndex(root, state_dir=state, timeout_s=timeout)


@pytest.mark.parametrize("budget, included", [(4096, True), (1536, False)])
def test_complete_long_symbol_uses_remaining_packet_budget(indexed, budget, included):
    """A >40-line, >2048-byte function fits or is omitted whole; intent survives."""
    from specgraph.serialization import canonical_json
    root, state = indexed
    source = root / "src/lib.py"
    body = 'def caller():\n    """@spec SPEC-ONE"""\n' + ('    # ' + 'x' * 32 + '\n') * 54 + '    return 1\n'
    assert len(body.encode("utf-8")) > 2048
    source.write_bytes(body.encode("utf-8"))
    index_repository(root, state_dir=state)
    source.unlink()  # The hook must use the published snapshot, even for long snippets.
    report = context_hint(root, role="implementer", event="pre", files=("src/lib.py",),
                          spec_id="SPEC-ONE", state_dir=state, max_bytes=budget, timeout_ms=1000)
    assert report["status"] == "HIT"
    assert report["intent"]["acceptance_criteria"] == {"AC-01": "Return one"}
    assert len((canonical_json(report) + "\n").encode("utf-8")) <= budget
    if included:
        snippet, = report["snippets"]
        assert snippet["text"] == body
        assert snippet["complete_symbol"] is True
        assert snippet["sha256"] == hashlib.sha256(body.encode("utf-8")).hexdigest()
    else:
        assert not report.get("snippets")
        assert report["omitted_items"] >= 1


@pytest.mark.parametrize("payload", ['\\' * 1800, '漢' * 1400])
def test_snippet_budget_counts_json_escaping_and_utf8(indexed, payload):
    from specgraph.serialization import canonical_json
    root, state = indexed
    body = "def caller():\n    # " + payload + "\n    return 1\n"
    (root / "src/lib.py").write_bytes(body.encode("utf-8"))
    index_repository(root, state_dir=state)
    report = context_hint(root, role="implementer", event="pre", files=("src/lib.py",),
                          state_dir=state, max_bytes=4096, timeout_ms=1000)
    assert report["status"] == "HIT"
    assert not report.get("snippets")
    assert report["omitted_items"] >= 1
    assert len((canonical_json(report) + "\n").encode("utf-8")) <= 4096


@pytest.mark.parametrize("role", ["planner", "implementer", "auditor", "explorer"])
@pytest.mark.parametrize("dynamic", [False, True])
def test_substitute_reports_delivered_static_facts_not_parse_success(tmp_path, role, dynamic):
    """@verifies SPEC-SPECGRAPH-X2:AC-03

    Question: what indexed declaration binds the call at line 3? Not what runs.
    """
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    body = ('def leaf(): return 1\ndef caller(callback):\n    return callback()\n' if dynamic else
            'def leaf(): return 1\ndef caller():\n    return leaf()\n')
    (root / "src/lib.py").write_text(body, encoding="utf-8")
    state = tmp_path / "index"
    metadata = index_repository(root, state_dir=state)
    # Independently specified source/range/target oracle, not assessment output.
    expected_name = "callback" if dynamic else "leaf"
    (root / "src/lib.py").unlink()
    report = context_hint(root, role=role, event="pre", symbol_id="src/lib.py:caller",
                          state_dir=state, max_bytes=8192, timeout_ms=1000)
    assert report["status"] == "HIT"
    assert report["snapshot"] == metadata["content_hash"]
    assert report["coverage"]["status"] == "COMPLETE_WITHIN_SCOPE"
    symbol, = report["symbols"]
    assert symbol["id"] == "src/lib.py:caller" and symbol["range"] == [2, 3]
    call, = [r for r in symbol["references"] if r["kind"] == "call"]
    assert (call["name"], call["line"], call["column"], call["end_line"]) == (expected_name, 3, 11, 3)
    assessment = report["substitute_assessment"]
    assert assessment["execution_complete"] is False
    assert assessment["scope"] == "selected_symbol_static_references"
    if dynamic:
        assert call["status"] != "resolved" and call["target"] is None
        reason, = assessment["reasons"]
        assert reason == {"code": "UNRESOLVED_REFERENCE", "symbol": "src/lib.py:caller",
                          "reference_index": symbol["references"].index(call), "reason": call["reason"]}
        assert assessment["status"] == "RECOVERY_REQUIRED"
    else:
        assert call["status"] == "resolved" and call["target"] == "src/lib.py:leaf"
        assert assessment["status"] == "STATIC_REFERENCES_DELIVERED"
        assert assessment["reasons"] == []
    again = context_hint(root, role=role, event="pre", symbol_id="src/lib.py:caller",
                         state_dir=state, max_bytes=8192, timeout_ms=1000,
                         previous_digest=report["digest"])
    assert again["status"] == "SILENT"


@pytest.mark.parametrize("role", ["planner", "implementer", "auditor", "explorer"])
def test_reference_and_reason_caps_preserve_conservative_recovery(tmp_path, role):
    """@verifies SPEC-SPECGRAPH-X2:AC-03"""
    from specgraph.serialization import canonical_json

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    body = "def caller(callback):\n" + "    callback()\n" * 12
    (root / "src/lib.py").write_bytes(body.encode())
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    report = context_hint(root, role=role, event="pre", symbol_id="src/lib.py:caller",
                          state_dir=state, max_bytes=8192, timeout_ms=1000)
    assert report["status"] == "HIT"
    symbol, = report["symbols"]
    assert len(symbol["references"]) == 8 and symbol["references_omitted"] == 4
    assessment = report["substitute_assessment"]
    assert assessment["status"] == "RECOVERY_REQUIRED"
    assert len(assessment["reasons"]) == 8
    assert assessment["reason_count"] == 9 and assessment["reasons_truncated"]
    assert assessment["reasons"][0] == {"code": "REFERENCES_NOT_DELIVERED",
                                           "symbol": "src/lib.py:caller", "count": 4}
    semantic = {key: value for key, value in report.items() if key != "digest"}
    assert report["digest"] == hashlib.sha256(canonical_json(semantic).encode()).hexdigest()
    semantic["substitute_assessment"] = dict(assessment, reasons=[])
    assert report["digest"] != hashlib.sha256(canonical_json(semantic).encode()).hexdigest()
    assert len((canonical_json(report) + "\n").encode()) <= 8192


def test_assessment_exposes_selection_and_packet_omissions(tmp_path):
    """Selection and delivery gaps cannot become complete reference delivery."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/lib.py").write_bytes(
        "".join(f"def f{i}():\n    # " + ("x" * 9000 if i == 0 else "small")
                + f"\n    return {i}\n" for i in range(18)).encode())
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    report = context_hint(root, role="implementer", event="pre", files=("src/lib.py",),
                          state_dir=state, max_bytes=8192, timeout_ms=1000)
    assert report["status"] == "HIT"
    assert report["selection_truncated"] and report["omitted_items"] > 0
    assessment = report["substitute_assessment"]
    assert assessment["status"] == "RECOVERY_REQUIRED"
    assert assessment["reason_count"] > 0
    assert any(reason["code"] in {"SYMBOL_NOT_DELIVERED", "CONTEXT_OMITTED"}
               for reason in assessment["reasons"])
