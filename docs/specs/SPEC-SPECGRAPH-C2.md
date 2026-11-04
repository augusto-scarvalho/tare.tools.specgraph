---
{
  "id": "SPEC-SPECGRAPH-C2",
  "title": "SQLite read model, snapshots and minimal MCP",
  "status": "ACCEPTED",
  "version": "1.0.0",
  "owner": "tare.tools.specgraph",
  "work_id": "SPECGRAPH-COMPLETION-C2",
  "program": "SPECGRAPH-COMPLETION-20260908",
  "decision_authority": "operator_instruction",
  "delivery_status": "NOT_QUALIFIED",
  "implements": [
    "REQ-SPECGRAPH-C2"
  ],
  "target_symbols": [],
  "acceptance_criteria": {
    "AC-01": "The index materializes symbols, relations, documents and metadata in SQLite WAL; a query does not run AST parsing, a full scan or a structural subprocess.",
    "AC-02": "BEGIN IMMEDIATE publication is atomic, content is deterministic and revision_seq is monotonic; an idempotent operation does not pollute generations and K=1 retention remains.",
    "AC-03": "The reader uses a consistent snapshot; a wrong root, corruption or an incompatible schema is refused without replacing sources.",
    "AC-04": "Coverage, snapshot/freshness and governance are separate states; governance debt does not prevent a faithful representation and parse errors are explicit.",
    "AC-05": "The read-only MCP query_causal_graph/get_context_envelope/check_drift uses the same services and includes revision_seq/content_hash; no mutator is exposed."
  }
}
---

# SPEC-SPECGRAPH-C2: SQLite read model, snapshots and minimal MCP

## Intent and scope

stdlib sqlite3, with FTS5 when available and a capability diagnostic when absent. Native state per root with an explicit override; store hashes before/after the build to detect concurrent writes. The strict JSON cache keeps its contract during the migration.

## Contract and authority

Contract accepted for implementation by the operator's explicit instruction on 2026-09-08. Accepting the scope does not attest completion or simulate a tripartite deliberation. ADR-001 and ADR-059 remain in force. The criteria are in the frontmatter; delivery state and evidence belong to the Work.

## Dependencies and design

Work: `SPECGRAPH-COMPLETION-C2`. Prerequisites: C1. Decision: [ADR-SPECGRAPH-002](../adr/ADR-SPECGRAPH-002.md).

Target files: `src/specgraph/storage.py`, `src/specgraph/indexer.py`, `src/specgraph/mcp_server.py`, `src/specgraph/cli.py`, `tests/test_persistent_index.py`, `tests/test_mcp_server.py`. Files that do not exist yet are planned deliverables.

## Verification

Planned qualification command, not an execution receipt:

```bash
python -m pytest tests/test_persistent_index.py tests/test_mcp_server.py -q
```

Record the source/configuration, the result and the criteria exercised. A declared, an executed and an attested test are distinct facts; partial evidence does not satisfy the whole Work. Contract check: `python tools/check_completion_contracts.py`.

## Limits

No resumption of the frozen harness, no promotion of inference to Tier 1 and no universal latency promise. The qualification reports coverage and limitations.
