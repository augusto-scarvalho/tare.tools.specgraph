---
id: SPEC-SPECGRAPH-001
title: Living causal matrix and substrate admission gate
status: ACCEPTED
version: 1.0.0
acceptance_criteria:
  AC-01: Indexed code symbols and annotations connect to repository-qualified specification identities.
  AC-02: Impact queries reuse a content-bound dependency cache and fail closed when it is stale.
  AC-03: Dispatch is rejected when source identity or acceptance criteria differ from the grounded envelope.
  AC-04: Context is bounded and contains only the causal neighborhood selected for the task.
---

# SPEC-SPECGRAPH-001: Living causal matrix and substrate admission gate

- **Status:** `CANONICAL_SSOT`
- **Canonical repository:** `tare.tools.specgraph`
- **Governing decision:** [ADR-001](ADR-001_SPECGRAPH_NORTH_STAR.md)
- **Version:** 1.0.0
- **Relocated from:** `tare.tools.library@d5473e69:specs/SPEC-SPECGRAPH-001.md`

## Purpose

Define causal traceability from requirements to code and tests, incremental
impact analysis and bounded context envelopes for agents.

## Verifiable acceptance criteria

- **AC-01 — Causal AST traceability:** indexed code symbols and `@spec`
  annotations connect to repository-qualified specification identities.
- **AC-02 — Incremental impact cache:** impact queries reuse a content-bound
  dependency cache and fail closed when it is stale.
- **AC-03 — Substrate admission gate:** dispatch is rejected when the selected
  source identity or acceptance criteria differ from the grounded envelope.
- **AC-04 — Surgical context envelopes:** generated context is bounded and
  contains only the causal neighborhood selected for the task.

## Context and reviewer bundle implementation boundary

`specgraph context SPEC-ID --max-bytes 8192` returns Markdown with the selected
specification's intent, acceptance criteria, linked code and test snippets.
Repeated links do not duplicate snippets; each selected declared criterion link
remains visible. Root and graph generation are included in both renderers.

`specgraph bundle --spec SPEC-ID --max-bytes 8192` selects one specification;
`specgraph bundle --files src/example.py --max-bytes 8192` selects specifications
linked to exact, normalized repository-relative file paths. The selectors are
mutually exclusive. Unmapped files produce an explicitly unmapped bundle, not a
substring search. Both renderers refuse unknown explicit SPECs, a root different
from the graph's root, and snippet sources that escape the root, are unavailable
or are not valid UTF-8. They do not substitute empty snippets or replacement
characters for unusable sources.

The default limit is 8192 UTF-8 bytes, configurable from 1024 to 65536. The CLI
counts the complete serialized output, including the JSON wrapper and final LF.
Oversize output is refused with exit code 1 and no stdout; narrow the selection
or explicitly increase the budget. Criteria and source snippets are not silently
truncated. The SDK raises `ValueError` and applies its budget to the returned
Markdown string. The token estimate is informational, not a tokenizer guarantee.

Shared code is emitted once; each declared test-to-SPEC/criterion relation is
preserved separately. A declared test link is not evidence that the test ran or
proved the criterion. The root and existing graph generation digest identify
the projection inputs; they do not establish a Git commit, an atomic snapshot,
or an execution permit. Callers must use a stable source snapshot when those
stronger guarantees are required.

Regression command for both renderers:
`py -m pytest tests/test_bundle_fidelity.py -q`.
These checks qualify bounded rendering and selected relation preservation, not
active-contract resolution, temporal lineage or the proposed audit program.

