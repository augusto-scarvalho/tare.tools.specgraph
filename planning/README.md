# SpecGraph completion program

The operator authorized these contracts on 2026-09-08. Recording them in the backlog does not attest implementation or a tripartite ratification.

Structured source: [completion-program.json](completion-program.json). The canonical Work graph is `tare.tools.os/work-graph.json`, program `SPECGRAPH-COMPLETION-20260908`. Exact code-to-criterion declarations are in [traceability-mappings.json](traceability-mappings.json), read through `intent_mapping_paths` in `specgraph.yaml`.

| Package | SPEC | Depends on |
|---|---|---|
| C0 | [SPEC-SPECGRAPH-C0.md](../docs/specs/SPEC-SPECGRAPH-C0.md) | — |
| C1 | [SPEC-SPECGRAPH-C1.md](../docs/specs/SPEC-SPECGRAPH-C1.md) | C0 |
| C2 | [SPEC-SPECGRAPH-C2.md](../docs/specs/SPEC-SPECGRAPH-C2.md) | C1 |
| C3 | [SPEC-SPECGRAPH-C3.md](../docs/specs/SPEC-SPECGRAPH-C3.md) | C2 |
| C4 | [SPEC-SPECGRAPH-C4.md](../docs/specs/SPEC-SPECGRAPH-C4.md) | C3 |
| C5 | [SPEC-SPECGRAPH-C5.md](../docs/specs/SPEC-SPECGRAPH-C5.md) | C4 |
| C6 | [SPEC-SPECGRAPH-C6.md](../docs/specs/SPEC-SPECGRAPH-C6.md) | C5 |
| C7 | [SPEC-SPECGRAPH-C7.md](../docs/specs/SPEC-SPECGRAPH-C7.md) | C5 |
| C8 | [SPEC-SPECGRAPH-C8.md](../docs/specs/SPEC-SPECGRAPH-C8.md) | C6, C7, X2 |
| X1 | [SPEC-SPECGRAPH-X1.md](../docs/specs/SPEC-SPECGRAPH-X1.md) | C4 |
| X2 | [SPEC-SPECGRAPH-X2.md](../docs/specs/SPEC-SPECGRAPH-X2.md) | C4 |
| X3 | [SPEC-SPECGRAPH-X3.md](../docs/specs/SPEC-SPECGRAPH-X3.md) | C4 |
| X4 | [SPEC-SPECGRAPH-X4.md](../docs/specs/SPEC-SPECGRAPH-X4.md) | C5 |
| X5 | [SPEC-SPECGRAPH-X5.md](../docs/specs/SPEC-SPECGRAPH-X5.md) | C5 |

## Decisions

- [Identity, extraction and persistence](../docs/adr/ADR-SPECGRAPH-002.md)
- [Queries and context budget](../docs/adr/ADR-SPECGRAPH-003.md)
- [Federation and evidence validity](../docs/adr/ADR-SPECGRAPH-004.md)
- [Capabilities and qualification](../docs/adr/ADR-SPECGRAPH-005.md)

C0–C8 are the product program. X2 is a required contract for negative bounds. X1, X3, X4 and X5 are experiments with their own criteria; they do not promote inference to authority.

Check the contracts' consistency with `python tools/check_completion_contracts.py`. The [delivery evidence](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08/README.md) records the results actually obtained.

C3 → C4 → C5 continuation: [historical delivery and limits](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-c3-c5/README.md).

C7/C8 qualification: [delivery and proofs](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-c7-c8/README.md). The Work records **10/10 required contracts qualified within the supported local scope**, with the optional experiments planned separately. Closing the program does not claim universal capability, green governance, production effectiveness, Tier 1 or a release. See the [obligation and limits matrix](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-c7-c8/OBLIGATION_MATRIX.md).

Two follow-ups came after the program. `SPECGRAPH-FOLLOWUP-GOVERNANCE-20260908` addresses residual governance (orphan symbols and schemas) under the [governance specification](../docs/specs/SPEC-SPECGRAPH-GOVERNANCE-001.md). `SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908` addresses native host delivery and end-to-end usefulness under the [host adoption contract](../docs/specs/SPEC-SPECGRAPH-HOST-ADOPTION.md), with [partial evidence](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-host-adoption/README.md); automatic hooks and complete tasks remain open.

## Experimental comparison with Graphify

[Executed result and limits](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-graphify/README.md): 400 CLI calls over pinned corpora ([contract](../docs/specs/SPEC-SPECGRAPH-GRAPHIFY-COMPARISON.md)). The follow-ups it produced each have their own contract: [lexical search and intent projection](../docs/specs/SPEC-SPECGRAPH-LEXICAL-INTENT.md), [lexical quality](../docs/specs/SPEC-SPECGRAPH-LEXICAL-QUALITY.md) and [TypeScript/Rust aliases](../docs/specs/SPEC-SPECGRAPH-CROSSFILE-ALIASES.md). The canonical state of each continuation is in `tare.tools.os/work-graph.json`.

## X3: experiment concluded on 2026-10-18

The operator activated the handle-based delta experiment. The offline
comparison preserved context and query answers in eight cases, but the median
of 43.19% of the complete envelope missed the exploratory target of 20%.
The decision is to keep complete context by default; the explicit API stays
experimental. [Contract, reproducible measurement and limits](../docs/guides/CONTEXT_DELTAS.md).

## X4: optional diagnostic concluded on 2026-10-19

The current import protection already meets the ADR-057 contract. X4 only adds
on-demand explanation on top of the existing path search, with an explicit
reference to the rule and its provenance. Ten offline cases produced two
supported paths, seven inconclusive cases and one refusal for budget. There is
no solver, new required gate or automatic repair.
[API, evidence and limits](../docs/guides/ARCHITECTURE_WITNESS.md).
