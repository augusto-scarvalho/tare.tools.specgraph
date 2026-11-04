"""Hook searches consume the bounded published retrieval used by query/resolve."""
import json
from pathlib import Path

import pytest

from specgraph.hook_context import context_hint
from specgraph.indexer import index_repository
from specgraph.storage import RepositoryIndex


@pytest.fixture
def indexed(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "docs/specs").mkdir(parents=True)
    (root / "src/account.py").write_text(
        'def account_identities():\n    """Keep stable keys. @spec SPEC-ACCOUNT"""\n    return []\n',
        encoding="utf-8")
    (root / "docs/specs/SPEC-ACCOUNT.md").write_text(
        '---\nid: SPEC-ACCOUNT\ntitle: Customer retention\nstatus: ACCEPTED\n'
        'acceptance_criteria:\n  AC-01: Preserve customer retention.\n---\n'
        '# Customer retention\nPreserve customer retention.\n', encoding="utf-8")
    state = tmp_path / "state"
    index_repository(root, state_dir=state)
    return root, state


@pytest.mark.parametrize("role", ["planner", "implementer", "auditor", "explorer"])
def test_role_query_uses_published_lexical_retrieval(indexed, role, monkeypatch):
    """@verifies SPEC-SPECGRAPH-HOST-ADOPTION:AC-06"""
    root, state = indexed
    def forbidden(*args, **kwargs):
        raise AssertionError("Hook query must not use legacy search or source reads")
    monkeypatch.setattr(RepositoryIndex, "search", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    kwargs = dict(role=role, event="pre", query="account identity", work_id="W-1",
                  source_ref="owner:contract", state_dir=state, timeout_ms=1000)
    result = context_hint(root, **kwargs)
    assert result["status"] == "HIT"
    assert result["work_id"] == "W-1" and result["source_ref"] == "owner:contract"
    symbol = result["symbols"][0]
    assert symbol["id"] == "src/account.py:account_identities"
    assert symbol["match"]["expansions"] == {"identity": ["identities"]}
    assert symbol["match"]["identity"] is False
    assert symbol["retrieval_reason"] == "fts5_symbol_match"
    assert len((json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= 4096
    repeat = context_hint(root, previous_digest=result["digest"], **kwargs)
    assert repeat["status"] == "SILENT"
    if role == "auditor":
        assert symbol["evidence"] == "NOT_ATTESTED"


def test_declared_intent_path_is_delivered_whole(indexed):
    """@verifies SPEC-SPECGRAPH-HOST-ADOPTION:AC-06"""
    root, state = indexed
    result = context_hint(root, role="planner", event="pre", query="customer retention",
                          state_dir=state, timeout_ms=1000)
    symbol = result["symbols"][0]
    assert symbol["retrieval_reason"] == "declared_intent_match"
    assert symbol["intent_path"]["document"] == "spec:SPEC-ACCOUNT"
    assert symbol["intent_path"]["edges"][0]["type"] == "CODE_IMPLEMENTS_SPEC"
    assert len(symbol["intent_path"]["source"]["sha256"]) == 64
    small = context_hint(root, role="planner", event="pre", query="customer retention",
                         max_bytes=512, state_dir=state, timeout_ms=1000)
    assert len((json.dumps(small, ensure_ascii=False, separators=(",", ":")) + "\n").encode()) <= 512
    assert not small.get("symbols")  # Never clip a provenance path to fit.


def test_explicit_file_keeps_precedence_over_query(indexed):
    root, state = indexed
    result = context_hint(root, role="explorer", event="pre", files=("src/missing.py",),
                          query="account identity", state_dir=state, timeout_ms=1000)
    assert result["status"] == "MISS"
    assert not result.get("symbols")


def test_document_only_query_is_useful_without_invented_code(indexed):
    root, state = indexed
    (root / "src/account.py").unlink()
    index_repository(root, state_dir=state)
    result = context_hint(root, role="planner", event="pre", query="customer retention",
                          state_dir=state, timeout_ms=1000)
    assert result["status"] == "HIT" and not result.get("symbols")
    assert result["documents"][0]["id"] == "spec:SPEC-ACCOUNT"


def test_explicit_spec_suppresses_unrelated_query_candidates(indexed, monkeypatch):
    """@verifies SPEC-SPECGRAPH-HOST-ADOPTION:AC-06"""
    root, state = indexed
    def forbidden(*args, **kwargs):
        raise AssertionError("An explicitly selected SPEC must not trigger broad retrieval")
    monkeypatch.setattr("specgraph.retrieval.retrieve", forbidden)
    result = context_hint(root, role="planner", event="pre", spec_id="SPEC-ACCOUNT",
                          query="account identity", state_dir=state, timeout_ms=1000)
    assert result["status"] == "HIT"
    assert result["intent"]["acceptance_criteria"] == {"AC-01": "Preserve customer retention."}
    assert not result.get("symbols") and not result.get("documents")
    repeat = context_hint(root, role="planner", event="pre", spec_id="SPEC-ACCOUNT",
                          query="different irrelevant words", previous_digest=result["digest"],
                          state_dir=state, timeout_ms=1000)
    assert repeat["status"] == "SILENT"
