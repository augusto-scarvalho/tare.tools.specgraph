---
{
  "id": "ADR-SPECGRAPH-003",
  "title": "Bounded retrieval, interfaces and on-demand context",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "authorization_scope": "Implementation of the consolidated plan approved in this session",
  "tripartite_ratification_claimed": false,
  "governed_specs": [
    "SPEC-SPECGRAPH-C4",
    "SPEC-SPECGRAPH-C6",
    "SPEC-SPECGRAPH-C7",
    "SPEC-SPECGRAPH-X1",
    "SPEC-SPECGRAPH-X2",
    "SPEC-SPECGRAPH-X3"
  ]
}
---

# ADR-SPECGRAPH-003: Bounded retrieval, interfaces and on-demand context

## Context

The operator approved the combined Codex/Fable plan and authorized its implementation on 2026-09-08. This ADR records choices contained in that plan, subordinate to the [North Star](../ADR-001_SPECGRAPH_NORTH_STAR.md) and to [ADR-059](ADR-059_SPECGRAPH_CURATION_SCOPE_AND_SNAPSHOT_ISOLATION.md). It records neither a tripartite vote nor a qualification that does not exist.

## Decision

A deterministic router starts with identity/name/path, continues with lexical FTS5 and relation expansion under a budget. The CLI/SDK/MCP/HTML interfaces use the same generation and semantics. A minimal read-only MCP, TS/Rust and offline HTML remain North Star deliveries, without requiring vectors.

Context is selected in complete units with reasons/ranges; selected ACs and snippets are not truncated. Absence is asserted only within the proven scope/constructs/method; dynamic/unresolved/outside-coverage cases return unknown/partial. Learned reuse preserves literal versus structural and does not hide a better current result. Handles and deltas are bounded/expirable; K=1 historical storage does not expand.

## Consequences and verification

Qualify queries without scan/parse/network/LLM; misses/ambiguity and budgets are also measured. The <50ms service target stands without a delivery claim; CLI <=300ms is a proposed profile to be measured, not a silent reduction of the promise. A per-role gain requires a paired task, tokens and total time, not a HIT after a denial.

Contracts: [SPEC-SPECGRAPH-C4](../specs/SPEC-SPECGRAPH-C4.md), [SPEC-SPECGRAPH-C6](../specs/SPEC-SPECGRAPH-C6.md), [SPEC-SPECGRAPH-C7](../specs/SPEC-SPECGRAPH-C7.md), [SPEC-SPECGRAPH-X1](../specs/SPEC-SPECGRAPH-X1.md), [SPEC-SPECGRAPH-X2](../specs/SPEC-SPECGRAPH-X2.md), [SPEC-SPECGRAPH-X3](../specs/SPEC-SPECGRAPH-X3.md). Dependencies and authorship in the [program](../../planning/completion-program.json).

## Limits

No giant prepended reports and no search denier where there is no capable substitute. Advanced scorers/deltas/semantics are promoted by experiment; none of them may block delivery of the simple lookup. The frozen harness stays outside the program.
