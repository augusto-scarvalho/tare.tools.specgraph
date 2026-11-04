---
id: SPEC-SPECGRAPH-LEXICAL-INTENT
title: Lexical code retrieval and intent projection
status: ACCEPTED
version: 1.0.0
owner: tare.tools.specgraph
work_ids: [SPECGRAPH-SEARCH-LEXICAL-20260908, SPECGRAPH-INTENT-PROJECTION-20260908]
decision_authority: operator_instruction
acceptance_criteria:
  AC-01: Exact ID, name and path take precedence; query and resolve retrieve symbols by decomposed names and docstrings, without sources, models or loading the whole graph.
  AC-02: A persistent index normalizes snake_case, CamelCase and accents; natural queries use relevant terms and explicit ranking, without promoting a lexical match to identity or translation.
  AC-03: Documents may retrieve implementations only through published CODE_IMPLEMENTS_SPEC and SPEC_GOVERNED_BY_ADR, with limits on candidates, hops, edges and results; the definition precedes optional context.
  AC-04: query, symbol and context project links published by docstring, frontmatter and accepted mapping, preserving origin and source; a refused mapping is not reauthorized.
  AC-05: Complete units respect UTF-8 bytes and explicit omissions; absence, unavailable FTS and an old index are distinguished. Updating the projection is atomic and old readers stay coherent.
  AC-06: Run regressions, additional PT/EN queries frozen before implementation, and two rounds of 100 CLI calls per version; report top-5 hits, negatives, bytes, latency and limits without inferring agent success.
---

# Lexical retrieval and intent

Delivery authorized by the operator after the Graphify comparison. It complements C4;
it does not change the contracts or the historical receipts. The two Works above
own the delivery in the OS canonical backlog.

Preserve the literal `search` API and the structural `symbol/path/refs` selectors.
`query` and the published path of `resolve` use lexical retrieval. A
question does not come to uniquely identify a function in the structural modes.

Decision: [ADR-SPECGRAPH-007](../adr/ADR-SPECGRAPH-007.md).
Verification: `python -m pytest tests tools/tests`; additional qualification in
`docs/evidence/2026-09-08-lexical-intent/`. Results and limitations live in that
evidence and in the Work, not in this SPEC's acceptance status.
