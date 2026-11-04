---
{
  "id": "ADR-SPECGRAPH-005",
  "title": "Capability claims bound to qualification",
  "status": "ACCEPTED",
  "date": "2026-09-08",
  "scope": "tare.tools.specgraph",
  "decision_authority": "operator_instruction",
  "authorization_scope": "Implementation of the consolidated plan approved in this session",
  "tripartite_ratification_claimed": false,
  "governed_specs": [
    "SPEC-SPECGRAPH-C0",
    "SPEC-SPECGRAPH-C8"
  ]
}
---

# ADR-SPECGRAPH-005: Capability claims bound to qualification

## Context

The operator approved the combined Codex/Fable plan and authorized its implementation on 2026-09-08. This ADR records choices contained in that plan, subordinate to the [North Star](../ADR-001_SPECGRAPH_NORTH_STAR.md) and to [ADR-059](ADR-059_SPECGRAPH_CURATION_SCOPE_AND_SNAPSHOT_ISOLATION.md). It records neither a tripartite vote nor a qualification that does not exist.

## Decision

The catalog distinguishes an accepted contract, a partial implementation and a qualified capability. Documents do not announce a planned phase as present or a certification without a receipt. The operator's instruction authorizes implementing this program; it is neither technical evidence nor a chair's vote.

The canonical Work lives in OS/work-graph.json and uses Backlog Graph transactions with CAS revision. A SPEC defines ACs, an ADR decides the mechanism, evidence demonstrates behavior, Work records progress. X2 is a coverage requirement of the core; X1/X3/X4/X5 are research with rejection as a legitimate outcome. The program is concluded only when C8 gathers proofs of the mandatory deliveries.

## Consequences and verification

Run fixtures, tests and the benchmark with corpus/environment metadata. P95 requires enough samples and two rounds; the number of green tests does not measure complete coverage. The >=65% savings target earns a claim only after paired tasks with success/cost. A Graphify comparison requires a comparable run, not reading a README.

Contracts: [SPEC-SPECGRAPH-C0](../specs/SPEC-SPECGRAPH-C0.md), [SPEC-SPECGRAPH-C8](../specs/SPEC-SPECGRAPH-C8.md). Dependencies and authorship in the [program](../../planning/completion-program.json).

## Limits

No fictitious qualifications, grade-A evidence, hashes or ratifications. No global repair of the legacy backlog to dress up a green doctor. An exported graph is a derived projection, not another authority. The frozen harness stays outside the program.
