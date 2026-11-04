---
{
  "id": "SPEC-SPECGRAPH-X3",
  "title": "Temporal context deltas by handle",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-X3",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "QUALIFIED_BOUNDED_OFFLINE_EXPERIMENT",
  "implements": [
    "REQ-SPECGRAPH-X3"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "The client handle binds repo/Work/snapshot/IDs/digests; the diff includes every changed and withdrawn unit.",
    "AC-02": "Expiration, a wrong root/Work and a delta larger than the envelope return refresh/revalidation; the store's K=1 stays intact.",
    "AC-03": "Measure bytes and the task; exploratory target of a median <=20% of the envelope after a localized edit, reject if the delta is larger in >30% of cases."
  }
}
---

# SPEC-SPECGRAPH-X3: Temporal context deltas by handle

## Intent and scope

Digests in a bounded/TTL handle, not an unbounded history of generations. Compare against a complete recomputation so that no AC or invalidated evidence is omitted.

## Contract and authority

Experiment proposal; the outcome can be a reasoned rejection. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-X3`. Prerequisites: C4. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files: `src/specgraph/context.py`, `tests/test_context_delta.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_context_delta.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
