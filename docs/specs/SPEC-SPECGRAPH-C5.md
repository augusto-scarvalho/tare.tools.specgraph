---
{
  "id": "SPEC-SPECGRAPH-C5",
  "title": "Federated intent and evidence with validity",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C5",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C5"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Declarative mapping and assisted rebind preserve the source-qualified SPEC/ADR; conflicts and candidates are not silently promoted.",
    "AC-02": "A bundle by diff/ref uses a stable snapshot and the owners' explicit sources; READY metadata does not imply that the body was read.",
    "AC-03": "A test reference, a verifies declaration and executed coverage are distinct relations; an isolated JUnit/hash does not become Tier 1.",
    "AC-04": "Evidence records the command/executor/configuration/environment/snapshot and the attestation; relevant changes require conservative revalidation.",
    "AC-05": "Work/status belong to the Backlog; a source/AC mismatch at admission is refused; the Library catalog only receives pins/hashes of real commits."
  }
}
---

# SPEC-SPECGRAPH-C5: Federated intent and evidence with validity

## Intent and scope

Ingest evidence references produced by the executor; do not build a parallel signer. The valid/needs_revalidation/superseded/invalid states preserve history. Without complete observed dependencies, declare the limits and use a conservative fallback.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C5`. Prerequisites: C4. Decision: [ADR-SPECGRAPH-004](../adr/ADR-SPECGRAPH-004.md).

Target files: `src/specgraph/config.py`, `src/specgraph/ground.py`, `src/specgraph/context.py`, `src/specgraph/evidence.py`, `tests/test_evidence_lineage.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_evidence_lineage.py tests/test_ground.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
