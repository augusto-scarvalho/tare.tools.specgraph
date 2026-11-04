---
{
  "id": "SPEC-SPECGRAPH-C7",
  "title": "Context services for hooks and doctor",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C7",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C7"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "Read-only services expose root/capability/coverage/snapshot with a budget to existing adapters.",
    "AC-02": "Role/event selects useful, deduplicable context; no Work is chosen and no state is changed by hook inference.",
    "AC-03": "MISS/STALE/TIMEOUT and uncovered scope allow bounded recovery without a loop; a known path does not require rediscovery.",
    "AC-04": "Doctor distinguishes installation, coverage, index, governance, evidence and effectiveness; a hook registration does not prove execution/delivery.",
    "AC-05": "Usefulness is evaluated with paired tasks and the total agent cost; zero LLM/network/full scan in the synchronous handler."
  }
}
---

# SPEC-SPECGRAPH-C7: Context services for hooks and doctor

## Intent and scope

SpecGraph provides services; the OS composes lifecycle/doctor and the Kernel normalizes events/policy. The detailed operational contract is SPEC-OS-SPECGRAPH-HOOKS-001 in the OS; reuse existing handlers.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C7`. Prerequisites: C5. Decision: [ADR-SPECGRAPH-003](../adr/ADR-SPECGRAPH-003.md).

Target files: `src/specgraph/cli.py`, `src/specgraph/capabilities.py`, `src/specgraph/query.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_capabilities.py tests/test_project_queries.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
