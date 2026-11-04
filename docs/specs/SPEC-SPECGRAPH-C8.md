---
{
  "id": "SPEC-SPECGRAPH-C8",
  "title": "Qualification of the complete vision and release",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C8",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C8"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Each ADR-001/ADR-059/SPEC-SPECGRAPH-001 obligation points to its implementation, proof and limitation; no planned step becomes delivered.",
    "AC-02": "Benchmark profiles pin corpus/commit/config/hardware, >=100 samples and two rounds, with misses/outliers/serialization included.",
    "AC-03": "Context savings of >=65% are only advertised if a compatible experiment supports the claim; task success is a condition.",
    "AC-04": "Migration, clean installation, concurrency, mutations and real hosts are qualified; isolated green tests do not qualify integration.",
    "AC-05": "The Graphify comparison uses the same task/corpus/budget/version; without running the comparison, do not claim superiority."
  }
}
---

# SPEC-SPECGRAPH-C8: Qualification of the complete vision and release

## Intent and scope

The catalog and release documents are projections of evidence. Experiments can legitimately be rejected without blocking the core; X2 is a mandatory contract. Publication and pins are explicit steps after qualification.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C8`. Prerequisites: C6, C7, X2. Decision: [ADR-SPECGRAPH-005](../adr/ADR-SPECGRAPH-005.md).

Target files: `planning`, `docs`, `tests`, `tools/bench_queries.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
