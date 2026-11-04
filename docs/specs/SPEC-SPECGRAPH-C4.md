---
{
  "id": "SPEC-SPECGRAPH-C4",
  "title": "Budgeted code search and context",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C4",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C4"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Exact ID/path/name and text retrieve results with ranges, reasons and the source snapshot; lexical/graph retrieval works without a SPEC or an LLM.",
    "AC-02": "refs/neighbors/path/explain respect direction, types, ambiguities, hop/node/byte limits and deterministic ordering.",
    "AC-03": "Context selects complete units; mandatory ACs/snippets are never truncated; an excess is refused with a smaller suggested selection.",
    "AC-04": "Learned reuse preserves the literal/structural origin and does not hide a better result from the current generation.",
    "AC-05": "P95 includes the complete cost per profile; the sub-50ms service and <=300ms CLI targets remain qualification proposals, not a universal guarantee."
  }
}
---

# SPEC-SPECGRAPH-C4: Budgeted code search and context

## Intent and scope

Deterministic router exact→lexical→bounded expansion. FTS5 BM25, no mandatory vectors. Handles with repo/snapshot/budget, the existing strict renderer and compact JSON. Return UNKNOWN/PARTIAL when coverage does not allow an absence claim.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C4`. Prerequisites: C3. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files: `src/specgraph/query.py`, `src/specgraph/search.py`, `src/specgraph/resolve.py`, `src/specgraph/context.py`, `src/specgraph/cli.py`, `tests/test_project_queries.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_project_queries.py tests/test_resolve.py tests/test_bundle_fidelity.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
