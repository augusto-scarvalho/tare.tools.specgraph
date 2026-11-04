---
{
  "id": "SPEC-SPECGRAPH-X4",
  "title": "Witness chain for an architectural violation",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-X4",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "QUALIFIED_BOUNDED_ADVISORY_DIAGNOSTIC",
  "implements": [
    "REQ-SPECGRAPH-X4"
  ],
  "target_symbols": [
    "src/specgraph/query.py:architecture_witness"
  ],
  "acceptance_criteria": {
    "AC-01": "A real import/layer contract returns the minimal supported violating chain, with source/range and ADR.",
    "AC-02": "An ambiguous/inferred relation does not support a strict block; proposed cuts stay advisory and simulation is distinguished from a correct refactoring.",
    "AC-03": "Without a real contract or measurable value, record a rejection; do not build a mandatory global repair solver."
  }
}
---

# SPEC-SPECGRAPH-X4: Witness chain for an architectural violation

## Intent and scope

Inspired by import-linter and H-19/H-31; first explain the violation with a path, then evaluate small suggestions.

## Contract and authority

Experiment proposal; the outcome can be a reasoned rejection. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-X4`. Prerequisites: C5. Decision: [ADR-SPECGRAPH-004](../adr/ADR-SPECGRAPH-004.md).

Target files: `src/specgraph/query.py`, `tests/test_architecture_contracts.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_architecture_contracts.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
