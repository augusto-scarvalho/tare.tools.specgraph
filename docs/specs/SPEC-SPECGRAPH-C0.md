---
{
  "id": "SPEC-SPECGRAPH-C0",
  "title": "Honest capabilities and a reproducible benchmark",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C0",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C0"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Every advertised capability identifies its implementation, contract, verification method and limits; planning is not proof.",
    "AC-02": "The three Python counterexamples are reproducible without external services and recorded before/after the fix.",
    "AC-03": "The benchmark records version, root, corpus, environment, complete process, p50/p95/p99 and sample count without promoting a target to a measurement.",
    "AC-04": "The configuration reveals the covered spec/code paths; absence of a SPEC is declared, not confused with absence of code."
  }
}
---

# SPEC-SPECGRAPH-C0: Honest capabilities and a reproducible benchmark

## Intent and scope

Versioned catalog served by capabilities and doctor; replace absolute claims and incorrect links. Small local benchmark; heavy profiles use the workstation. Existing tests and counterexamples are distinct evidence.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C0`. Prerequisites: none. Decision: [ADR-SPECGRAPH-005](../adr/ADR-SPECGRAPH-005.md).

Target files: `README.md`, `README.pt-BR.md`, `docs/INDEX.md`, `src/specgraph/capabilities.py`, `tools/bench_queries.py`, `planning/completion-program.json`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_capabilities.py tests/test_python_resolution.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
