# SpecGraph documentation

## Start here

- [README](../README.md): what SpecGraph is, installation, commands and tests
- [Using project intelligence](PROJECT_INTELLIGENCE.md): queries, freshness, languages, role context, federation and qualification

## Contracts and decisions

- [North Star](ADR-001_SPECGRAPH_NORTH_STAR.md): the intended product, not a delivery statement
- [Canonical traceability and context specification](SPEC-SPECGRAPH-001.md)
- [Completion program](../planning/README.md): packages C0–C8 and X1–X5, their specifications, decisions and dependencies
- [ADR-059: curation scope and snapshot isolation](adr/ADR-059_SPECGRAPH_CURATION_SCOPE_AND_SNAPSHOT_ISOLATION.md), superseded by ADR-SPECGRAPH-015
- [Governance specification](specs/SPEC-SPECGRAPH-GOVERNANCE-001.md)
- Lexical search and intent: [contract](specs/SPEC-SPECGRAPH-LEXICAL-INTENT.md) · [decision](adr/ADR-SPECGRAPH-007.md)
- Lexical quality: [contract](specs/SPEC-SPECGRAPH-LEXICAL-QUALITY.md) · [decision](adr/ADR-SPECGRAPH-008.md)
- Host adoption: [contract and limits](specs/SPEC-SPECGRAPH-HOST-ADOPTION.md) · [decision](adr/ADR-SPECGRAPH-009.md)
- TypeScript/Rust aliases: [contracted subset](specs/SPEC-SPECGRAPH-CROSSFILE-ALIASES.md) · [decision](adr/ADR-SPECGRAPH-010.md)
- [Native core for symbol extraction, binding and impact](adr/ADR-SPECGRAPH-015.md) ([crate](../rust/specgraph-core/README.md))

## Retrieval and integration

- [SpecGraph-first recovery pipeline](SPECGRAPH_FIRST_RECOVERY_PIPELINE.md)
- [Search recovery telemetry](SEARCH_RECOVERY_TELEMETRY.md)
- [Library grounding contract](LIBRARY_GROUNDING_CONTRACT.md)
- [Optional context deltas (X3)](guides/CONTEXT_DELTAS.md)
- [Architecture witness (X4)](guides/ARCHITECTURE_WITNESS.md)
- [Ontology](../ontology/README.md)

## Repository maintenance

- [Repository content and validation](guides/REPOSITORY_CONTENT.md)
- [Historical evidence in Git history](evidence/README.md)
- [Experiment storage](EXPERIMENT_STORAGE.md)
- [Changelog policy](CHANGELOG_POLICY.md)

The `specgraph capabilities` catalog describes the implemented state. The North Star, parser coverage, a test declaration and execution evidence are different kinds of information.
