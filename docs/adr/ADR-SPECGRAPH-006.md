---
id: ADR-SPECGRAPH-006
title: Compare retrieval before automating context
status: ACCEPTED
date: 2026-09-08
governed_specs: [SPEC-SPECGRAPH-GRAPHIFY-COMPARISON]
decision_authority: operator_instruction
tripartite_ratification_claimed: false
---

# Compare retrieval before automating context

The earlier results did not measure Graphify and showed increased time
in the micro-tasks with hooks. The next product decision needs evidence
about what the engines retrieve and how much it costs to deliver that context.

## Decision

Separate three questions: does the extractor know the correct relation; does the query deliver
the necessary evidence; can the agent use it to complete a task.
The first delivery measures the first two in a local profile without models.
A later evaluation measures the third with the same model, task and budget.

Pin Graphify by a commit of the `Graphify-Labs/graphify` repository and the
`graphifyy` package, avoiding confusion with homonymous projects. Use the native CLI for
latency, including loading the graph or opening SQLite. Do not compare
isolated SQL with the competitor's full startup. Disable clustering
when measuring retrieval indexing and state that visualization is not part of
the measured cost.

Keep native outputs, sources and an answer key written before the queries. Compare the
presence of evidence by file, symbol and line; report real bytes and
tokens with a pinned tokenizer. Because the units of the native limits differ,
byte overruns are observed budget failures, without silent truncation.
Quality is not inferred from a smaller response size.

Separate search by identifier from natural language. Graph expansion can
help retrieval and can also introduce irrelevant context. Do not use the number
of nodes or edges as a measure of correctness, or the presence of a SPEC as proof
of implementation. Record failures and limitations with the same care as successes.

## Consequences

Results are a diagnosis of pinned corpora and versions, not a universal ranking.
A mode without models does not qualify multimodality or semantic extraction with
a backend. Choose the next implementation from reproduced failures:
first coverage and useful retrieval, then per-role context; vector
search or additional automation must justify its cost.

The experiment does not modify the frozen harness, install global hooks or
promote local evidence to Tier 1. Earlier contracts and receipts remain
unchanged.
