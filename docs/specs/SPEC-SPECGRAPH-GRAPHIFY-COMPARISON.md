---
id: SPEC-SPECGRAPH-GRAPHIFY-COMPARISON
title: Reproducible comparison of local retrieval with Graphify
status: ACCEPTED
version: 1.0.0
owner: tare.tools.specgraph
work_id: SPECGRAPH-GRAPHIFY-COMPARISON-20260908
implements: [REQ-SPECGRAPH-C8]
target_symbols: []
acceptance_criteria:
  AC-01: Pin commits, dependencies, corpus bytes and verifiable answers before querying the engines.
  AC-02: Run the same queries on both engines, with a ceiling of zero model calls and a declared output budget; retain failures and budget overruns.
  AC-03: Measure two rounds of at least 100 complete CLI calls per engine, including startup, serialization, misses and outliers.
  AC-04: Observe directed relations, homonyms, language limits and updates after a change; distinguish index facts from the answers actually delivered.
  AC-05: Publish a reproducible run, its limits and a backlog prioritized by the observed gaps; keep retrieval separate from agent success and from universal qualification.
---

# Local comparison with Graphify

Authorization: the operator accepted the proposed sequence and asked for it to run on
2026-09-08. This contract makes concrete a first experimental comparison under
SPEC-SPECGRAPH-C8 AC-05. It does not change the historical C0–C8 contract or attest every
mode of either product.

Decision: [ADR-SPECGRAPH-006](../adr/ADR-SPECGRAPH-006.md).

Three corpora: SpecGraph's code and contracts, the OS operational code in
`relay/` and `tools/` without historical files, and a controlled Python/TS/Rust case.
The exact versions and selections live in the evidence protocol and manifests.
The measurement copies are isolated; they do not execute the corpora's operational code.

The answer key checks the definition by file, name and line. Presence in the retrieved
context is not the same as a correct agent answer; this is not an exhaustive
precision measurement. Directed facts have a separate answer key with explicit positive
and negative relations. Ambiguous queries do not justify inventing
relations.

The profiles use the same host and input bytes. Graphify's local mode excludes
the semantic pass over documents. That difference must appear in the report,
without generalizing an absence in this profile into an incapacity of the whole product.
Output tokens are counted with a pinned tokenizer and labeled as a proxy; total tokens
and agent success are left to the later paired evaluation.

Artifacts: `tools/compare_graphify.py`, evaluator tests, protocol, manifests,
native outputs, samples and report in `docs/evidence/2026-09-08-graphify/`.
The canonical Work stays in the OS, changed by a Backlog Graph CAS transaction.
