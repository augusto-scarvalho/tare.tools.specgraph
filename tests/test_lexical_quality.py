"""Frozen before retrieval edits: morphology, field relevance and negative IDs.

Protocol: fixed fixtures and expectations below were written before production
changes. They are developer-authored regression witnesses, not a blind benchmark.
The historical 21-query corpus is measured separately without changing its text.
"""
from pathlib import Path

import pytest

from specgraph.query import project_query
from specgraph.indexer import index_repository

CASES = [
    ("plural-to-singular", "archive invoice records", 'def archive_invoice():\n    """Store a record."""\n', "archive_invoice"),
    ("singular-to-plural", "account identity", 'def account_identities():\n    """Preserve stable keys."""\n', "account_identities"),
    ("sibilant-plural", "match stored addresses", 'def match_address():\n    """Compare a stored value."""\n', "match_address"),
    ("name-over-doc-noise", "session credentials", 'def session_credentials():\n    """Manage login state."""\ndef broad():\n    """Session credentials session credentials unrelated documentation."""\n', "session_credentials"),
    ("missing-composite", "account_identity_absent", 'def account_identities():\n    """Preserve account identity."""\n', None),
    ("composite-no-morphology", "account_identity", 'def account_identities():\n    """Preserve stable keys."""\n', None),
    ("camel-no-morphology", "accountIdentity", 'def account_identities():\n    """Preserve stable keys."""\n', None),
    ("no-prefix-fuzz", "credit ledger", 'def credit_ledgering():\n    """Persist values."""\n', None),
    ("no-semantic-synonyms", "cancel purchase", 'def abort_order():\n    """Stop a transaction."""\n', None),
    ("singular-suffix-guard", "status report", 'def statu_report():\n    """Record a value."""\n', None),
    ("no-translation", "consultar faturas", 'def query_invoices():\n    """Read invoice data."""\n', None),
    ("accented-literal", "sessão expirada", 'def sessão_expirada():\n    """Renovar acesso."""\n', "sessão_expirada"),
]


def project(tmp_path, source):
    root, state = tmp_path / "repo", tmp_path / "state"
    (root / "src").mkdir(parents=True)
    (root / "src/code.py").write_text(source, encoding="utf-8")
    (root / "specgraph.yaml").write_text("project_name: quality\nindexed_paths: [src]\n", encoding="utf-8")
    index_repository(root, state_dir=state)
    return root, state


@pytest.mark.parametrize("case,query,source,expected", CASES, ids=[c[0] for c in CASES])
def test_frozen_quality_witnesses(tmp_path, case, query, source, expected):
    """@verifies SPEC-SPECGRAPH-LEXICAL-QUALITY:AC-01
    @verifies SPEC-SPECGRAPH-LEXICAL-QUALITY:AC-02
    """
    root, state = project(tmp_path, source)
    response = project_query(root, query, state_dir=state)
    definitions = [r for r in response["results"] if "name" in r]
    if expected:
        assert definitions[0]["name"] == expected
        assert definitions[0]["match"]["identity"] is False
    else:
        assert not definitions


def test_expansion_is_explicit_lexical_evidence_and_counts_original_terms(tmp_path):
    """@verifies SPEC-SPECGRAPH-LEXICAL-QUALITY:AC-03"""
    root, state = project(tmp_path, 'def account_identities():\n    """Preserve keys."""\n')
    result = project_query(root, "account identity", state_dir=state)["results"][0]
    assert result["match"]["terms"] == ["account", "identity"]
    assert result["match"]["coverage"] == 1.0
    assert result["match"]["expansions"] == {"identity": ["identities"]}
    assert result["match"]["fields"] == ["name"]
    assert result["match"]["identity"] is False
    assert result["match"]["field_relevance"] == 4
    exact = project_query(root, "account_identities", state_dir=state)["results"][0]
    assert exact["reason"] == "exact_name"
    assert "match" not in exact


def test_variant_budget_and_literal_identifiers():
    """@verifies SPEC-SPECGRAPH-LEXICAL-QUALITY:AC-04"""
    from specgraph.lexical import MAX_TERMS, MAX_VARIANTS, grouped_expression, query_groups, query_terms
    terms = query_terms(" ".join("token" + str(i) for i in range(MAX_TERMS)))
    groups = query_groups(terms)
    assert len(groups) == MAX_TERMS
    assert sum(map(len, groups)) <= MAX_TERMS * MAX_VARIANTS
    with pytest.raises(ValueError, match="32 distinct"):
        query_terms(" ".join("token" + str(i) for i in range(MAX_TERMS + 1)))
    assert query_groups(["identity", "module"], identifier=True) == [["identity"], ["module"]]
    assert grouped_expression([["identity"], ["module"]], identifier=True) == '("identity") AND ("module")'


def test_singular_s_endings_do_not_drop_s():
    from specgraph.lexical import term_variants
    for term in ("status", "class", "analysis", "address", "process"):
        assert term[:-1] not in term_variants(term)
    assert term_variants("address") == ["address", "addresses"]
    assert term_variants("addresses") == ["addresses", "address"]
    assert term_variants("invoice") == ["invoice", "invoices"]
    assert term_variants("invoices") == ["invoices", "invoice"]


def test_path_only_mentions_do_not_outweigh_definition_name(tmp_path):
    """@verifies SPEC-SPECGRAPH-LEXICAL-QUALITY:AC-02"""
    root, state = project(tmp_path, 'def archive_invoice():\n    """Persist a record."""\n')
    (root / "src/archive_invoice.py").write_text('def unrelated():\n    """Do something else."""\n', encoding="utf-8")
    index_repository(root, state_dir=state)
    results = project_query(root, "archive invoice", state_dir=state)["results"]
    assert results[0]["name"] == "archive_invoice"
    unrelated = next(r for r in results if r.get("name") == "unrelated")
    assert unrelated["match"]["field_relevance"] == 0.5
