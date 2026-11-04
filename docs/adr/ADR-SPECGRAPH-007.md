---
id: ADR-SPECGRAPH-007
title: Persistent lexical retrieval with selective intent expansion
status: ACCEPTED
date: 2026-09-08
governed_specs: [SPEC-SPECGRAPH-LEXICAL-INTENT]
decision_authority: operator_instruction
tripartite_ratification_claimed: false
---

# Decision

Use the FTS5 already available to retrieve code by vocabulary. Normalize
fields at publication and version that projection independently of the extractor.
Reading never migrates indexes: an old index stays readable, reports the
missing capability and asks for an explicit publication. Publication updates
index, capabilities and hash in the same transaction.

Keep exact queries on B-tree indexes. For text, generate candidates
from FTS postings, rank by term coverage and field-weighted BM25,
with an explicit cap. Terms of compound identifiers require conjunction to
avoid turning a nonexistent identifier into approximate neighbors.
Removing PT/EN function words does not mean translating or understanding intent.

Expand only declared intent links: document → SPEC → code.
Do not run a generic BFS for every question. Definitions take priority over
documents and optional relations. Relations keep the published payload and hashed
sources; a link does not prove correct implementation or test execution.

Keep the legacy literal search and the structural selectors separate. Existing hooks
gain the published retrieval through resolve; automatic_roles stays empty.
Candidate/edge limits can lose recall and must appear in the response.
Constant FTS latency is not assumed for every corpus; measure the complete CLI,
including startup. Translation, vectors, per-role automation and cross-file TS/Rust
are outside this delivery.
