---
id: SPEC-SPECGRAPH-LEXICAL-QUALITY
title: Bounded and explainable lexical quality
status: ACCEPTED
version: 1.0.0
owner: tare.tools.specgraph
work_ids: [SPECGRAPH-LEXICAL-QUALITY-20260908]
decision_authority: operator_instruction
acceptance_criteria:
  AC-01: Natural queries may retrieve regular English plural spellings through bounded alternatives; exact IDs, names and paths keep precedence and composite identifiers receive no expansion.
  AC-02: Ranking distinguishes evidence in the name or title, the docstring or body, and the path; relevant definitions precede path mentions, with a deterministic tie-break.
  AC-03: A match reports the original terms, coverage, fields, relevance and the alternative spellings actually found; lexical retrieval never establishes identity or semantic equivalence.
  AC-04: Up to 32 original terms and two spellings per term are allowed; candidates stay limited to 100 per index, with explicit omissions, without sources, models, prefixes or loading the whole graph on read.
  AC-05: Freeze additional cases before implementation, preserve historical exact matches and negatives, compare quality and the cost of a complete CLI call, and record limits without extrapolating savings in agent tasks.
---

# Contract

The improvement operates on the postings of lexical projection version 1. It does not change the
persisted vocabulary, schema, migration or publication. The exact query still runs
before lexical preparation; structural selectors and the literal API remain
unchanged. Expansion for composite identifiers stays disabled.

Each natural term receives its original spelling and, where applicable, one mechanical
English plural alternative: `invoice/invoices`, `identity/identities` and
`address/addresses`, for example. The rules accept only alphabetic ASCII words
of at least three letters and protect the common singular endings
`ss`, `us` and `is`. These are incomplete spelling rules, with no language identification:
irregular forms, verb inflections, synonyms, translation and semantic understanding are
outside the contract. The alternative spelling appears in the response only if it exists in a
published field of the candidate.

Relevance adds up, per original term, the highest weight of a field where a spelling was
found: name/title 2, docstring/body 1 and path 0.25. The same occurrence does not
add weights from several fields. Tie-breaks use coverage of original terms, weighted BM25
and stable ID. The weights are declared heuristics, not probabilities
or semantic confidence. Coverage does not grow with the number of alternatives.

The candidate ceiling can drop results before reranking and must stay
visible. The `query` API keeps its byte budget; `resolve` remains the API
with a result limit, without assuming its whole envelope fits in 8 KB.

The 12 initial cases of `tests/test_lexical_quality.py` were frozen before the
production changes: SHA256 of the original file
`731148908c3a128dce5fe8216019086707f514566156f7025db2e92bd8141da7`.
Baseline: 11 passed and 1 failed; after the change: 12 passed. Additional tests
added after the implementation check expansion proofs and limits and are not
presented as a frozen set or a blind evaluation. The historical benchmark
keeps its questions and corpus; the final results live in the delivery
evidence and in the canonical Work. Acceptance of the contract does not mean universal
qualification. Decision: [ADR-SPECGRAPH-008](../adr/ADR-SPECGRAPH-008.md).
