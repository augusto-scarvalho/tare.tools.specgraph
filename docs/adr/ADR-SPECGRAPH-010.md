---
id: ADR-SPECGRAPH-010
title: Alias rebinding from published AST facts and explicit scope
status: ACCEPTED
date: 2026-09-08
governed_specs: [SPEC-SPECGRAPH-CROSSFILE-ALIASES]
decision_authority: operator_instruction
tripartite_ratification_claimed: false
---

# Decision

Add cross-file binding for a subset of TypeScript relative named imports
and Rust child modules. Store import and export facts during AST extraction
and resolve them against the current symbol set during graph assembly.
Do not couple calls to source files at query time and do not treat a name
match as proof of an import.

Separate the per-file cache from whole-set resolution: a change to the target must remove
or redo the edge even when the caller is reused. Increment the extractor
version, keeping read defaults compatible. Ambiguous, unexported,
out-of-scope and shadowed targets stay unknown with an explicit reason.

Use declared paths and narrow conventions, without package resolution,
compiler configuration, reexports or file/directory fallback. Those
features need additional contracts and evidence; the presence of parsers does not
constitute general semantic support. In Rust, attributes and macros cause conservative
abstention for cross-file links. Keep existing same-file links
and the Python rules.

Qualify the historical fixture, adversarial negatives, incremental update and
real samples at a pinned revision. Measure the complete cost of publication and rebind;
the query still consumes only the published snapshot. A static edge does not
prove runtime dispatch, implementation correctness or an executed test.
