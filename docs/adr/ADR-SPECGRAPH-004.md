---
{
  "id": "ADR-SPECGRAPH-004",
  "title": "Intent federation and execution evidence",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "authorization_scope": "Implementation of the consolidated plan approved in this session",
  "tripartite_ratification_claimed": false,
  "governed_specs": [
    "SPEC-SPECGRAPH-C5",
    "SPEC-SPECGRAPH-X4",
    "SPEC-SPECGRAPH-X5"
  ]
}
---

# ADR-SPECGRAPH-004: Intent federation and execution evidence

## Context

The operator approved the combined Codex/Fable plan and authorized its implementation on 2026-09-08. This ADR records choices contained in that plan, subordinate to the [North Star](../ADR-001_SPECGRAPH_NORTH_STAR.md) and to [ADR-059](ADR-059_SPECGRAPH_CURATION_SCOPE_AND_SNAPSHOT_ISOLATION.md). It records neither a tripartite vote nor a qualification that does not exist.

## Decision

SpecGraph maintains relations and projections; each repository owns its sources; Library provides catalog/pins/curation; Backlog decides Work/status; OS composes; Kernel/executor attests according to its contract. Ground READY remains metadata-only and sources are read from the selected owner/pin.

Separate TEST_REFERENCES_SYMBOL, the verifies declaration and executed coverage. XML/a hash of an excerpt is not Tier 1. Evidence binds executor/command, source/snapshot, dependencies, configuration and environment, a verifiable artifact and attestation. Valid/needs_revalidation/superseded/invalid do not erase the historical execution; unknown dependencies require conservative invalidation.

## Consequences and verification

Homonyms across repos, source/AC mismatch, changed config/lockfile, a transitive dependency and an irrelevant source make up the falsifiers. Architecture contracts offer a witness chain with evidence; a graph cut is a suggestion and does not prove refactoring behavior. Test selection is a qualified recommendation; the mandatory suite is not waived.

Contracts: [SPEC-SPECGRAPH-C5](../specs/SPEC-SPECGRAPH-C5.md), [SPEC-SPECGRAPH-X4](../specs/SPEC-SPECGRAPH-X4.md), [SPEC-SPECGRAPH-X5](../specs/SPEC-SPECGRAPH-X5.md). Dependencies and authorship in the [program](../../planning/completion-program.json).

## Limits

No parallel signer, no Work status changed by indexing, no copy of another owner's payload in the Library and no new control plane. Experiments may be rejected with data; without complete coverage, do not promise a safe minimal set. The frozen harness stays outside the program.
