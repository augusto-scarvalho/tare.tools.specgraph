---
{
  "id": "ADR-SPECGRAPH-002",
  "title": "Materialized index and structural precision",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "authorization_scope": "Implementation of the consolidated plan approved in this session",
  "tripartite_ratification_claimed": false,
  "governed_specs": [
    "SPEC-SPECGRAPH-C1",
    "SPEC-SPECGRAPH-C2",
    "SPEC-SPECGRAPH-C3"
  ]
}
---

# ADR-SPECGRAPH-002: Materialized index and structural precision

## Context

The operator approved the combined Codex/Fable plan and authorized its implementation on 2026-09-08. This ADR records choices contained in that plan, subordinate to the [North Star](../ADR-001_SPECGRAPH_NORTH_STAR.md) and to [ADR-059](ADR-059_SPECGRAPH_CURATION_SCOPE_AND_SNAPSHOT_ISOLATION.md). It records neither a tripartite vote nor a qualification that does not exist.

## Decision

Separate construction from reading: SQLite WAL is the read model; JSON remains export/compatibility. A query does not rebuild the AST. Identity uses repo/root, module and lexical scope, with a separate content version. Resolution by basename and the all-imports fallback are replaced by explicit occurrences and resolution by module/source roots.

Publication uses BEGIN IMMEDIATE, a monotonic revision and a deterministic digest; reading uses BEGIN DEFERRED. Retention K=1; identical content does not create a superfluous revision. Governance debt does not invalidate the faithful representation; integrity, coverage and freshness are independent states. Strict impact keeps refusing stale data per AC-02. A delta must be equivalent to a rebuild and declare source instability.

## Consequences and verification

A change in read order, hash or parser cannot leave a partial generation. Minimal cases: homonyms in pkg_a/common.py and pkg_b/common.py, a parent→leaf call, an unused import, lexical collisions, reader/writer, rollback and a same-size edit. Windows/WSL state is separate; do not assume that moving SQLite removes source I/O on /mnt/c.

Contracts: [SPEC-SPECGRAPH-C1](../specs/SPEC-SPECGRAPH-C1.md), [SPEC-SPECGRAPH-C2](../specs/SPEC-SPECGRAPH-C2.md), [SPEC-SPECGRAPH-C3](../specs/SPEC-SPECGRAPH-C3.md). Dependencies and authorship in the [program](../../planning/completion-program.json).

## Limits

Python stdlib and sqlite3 first; no DuckDB/LanceDB/Neo4j and no Rust core before the ADR-059 protocol. The AST does not prove complete dynamic dispatch. A store with a legacy edge does not promote it to resolved. The frozen harness stays outside the program.
