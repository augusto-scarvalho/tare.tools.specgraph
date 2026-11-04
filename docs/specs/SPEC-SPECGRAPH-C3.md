---
{
  "id": "SPEC-SPECGRAPH-C3",
  "title": "Incremental extraction and verifiable freshness",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C3",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C3"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "After an edit/delete/rename/config/parser change, the incremental graph is equivalent to a full rebuild on the qualification corpus.",
    "AC-02": "The delta re-extracts changed files and relinks affected consumers; it removes leftover symbols/edges/FTS rows atomically.",
    "AC-03": "Watchers/events are accelerators with reconciliation of lost events; the freshness method, age and coverage are explicit.",
    "AC-04": "Strict impact keeps AC-02 fail-closed for stale data; an advisory query never satisfies a gate.",
    "AC-05": "Unstable sources/parse errors are not published as complete or fresh; the read model can represent debt without hiding it."
  }
}
---

# SPEC-SPECGRAPH-C3: Incremental extraction and verifiable freshness

## Intent and scope

Cache per extractor/file with fingerprints and resolution dependencies; reparse limited to the delta and transitive rebind according to exports. Do not replace strong verification with mtime without declaring the limit.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C3`. Prerequisites: C2. Decision: [ADR-SPECGRAPH-002](../adr/ADR-SPECGRAPH-002.md).

Target files: `src/specgraph/indexer.py`, `src/specgraph/cache.py`, `src/specgraph/staleness.py`, `src/specgraph/impact.py`, `tests/test_incremental_index.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_incremental_index.py tests/test_cache_and_staleness.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
