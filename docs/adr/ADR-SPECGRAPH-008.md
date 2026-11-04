---
id: ADR-SPECGRAPH-008
title: Plural alternatives in the query and per-field relevance
status: ACCEPTED
date: 2026-09-08
governed_specs: [SPEC-SPECGRAPH-LEXICAL-QUALITY]
decision_authority: operator_instruction
tripartite_ratification_claimed: false
---

# Decision

Improve natural retrieval with bounded alternatives for regular English plurals
in the FTS5 expression itself and per-field relevance in reranking. The published
projection stays at version 1: original spellings are already available and no new
data needs to be extracted. Readers keep the same published transaction and still
do not consult source files, models or the full graph.

Analysis of the historical cases showed mismatches between `identity/identities`
and `module/modules`, plus coverage that gave the same value to names,
docstrings and paths. Generalizing these failure classes is preferable to introducing
synonyms specific to the questions, modifying the source texts or teaching
function names to the ranker. The rules do not translate and do not handle natural
language in general.

Keep the original spelling, add at most one alternative and count
coverage per original term. Report only expansions supported by the published
payload. Do not expand absent compound identifiers; their literal conjunction
protects against approximate neighbors. Exact identities keep the B-tree path.

Relevance uses the highest weight per term: name/title 2, docstring/body 1, path
0.25. Coverage, BM25 and stable ID break ties. This choice favors declarations
over mentions and keeps common paths from dominating the ranking, but it can favor
a short name over a long description. The weights are heuristic and must
remain subject to adversarial evaluations and more varied corpora.

Keep the existing limits on candidates, results, context and bytes, and make
truncations visible. Widening postings and the proofs in the response has a cost:
qualification must measure the complete CLI and the output size. Improvement on a
lexical bench does not prove total agent savings, success on real tasks or
general superiority over Graphify. Do not add vectors, prefixes, synonyms,
an LLM on the hot path or external dependencies in this delivery.
