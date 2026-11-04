---
id: SPEC-SPECGRAPH-CROSSFILE-ALIASES
title: Static cross-file aliases in TypeScript and Rust
status: ACCEPTED
version: 1.0.0
owner: tare.tools.specgraph
work_ids: [SPECGRAPH-CROSSFILE-ALIASES-20260908]
decision_authority: operator_instruction
acceptance_criteria:
  AC-01: The historical fixture resolves the two imported TypeScript and Rust calls, preserving the Python calls and keeping the four forbidden edges absent.
  AC-02: Only explicit imports and exports in the documented subset produce edges; shadowing, ambiguity, reexports, unsupported configuration and metaprogramming keep the reference unknown.
  AC-03: AST import and export facts are persisted per file; global rebinding happens at publication without reparsing the caller and removes an obsolete target after a rename, delete or export change.
  AC-04: References and edges preserve origin, call site and resolution reason; queries read the snapshot without opening sources or invoking compilers or models.
  AC-05: Compare incremental and full indexing, test adversarial cases and a real versioned corpus, and measure the rebinding cost without claiming general language support.
---

# Subset contracted before implementation

TypeScript: a named import, optionally aliased, from a literal relative path
of a directly exported top-level function. A path without an extension names only
a `.ts` file; an explicit `.ts` or `.tsx` extension is accepted. There is no fallback to
`index.ts`, packages, `.js`, tsconfig aliases, default exports, type imports,
reexports or type resolution. This is a static binding within the declared
subset, not an execution of the compiler's resolver.

Rust: a top-level `mod name;` followed by `use name::function as alias;` or an
unaliased import, of a top-level `pub fn` function in the child `.rs` file. The location
follows the module convention of the calling file: next to `main.rs`, `lib.rs` and
`mod.rs`; under the directory named after the stem for other files. There is no fallback
to `name/mod.rs`, globs, grouped use, reexports, crate/super paths, cfg, attributes,
macros or restricted visibility. A file with attributes or a macro invocation does not
admit cross-file binding, even if the attribute apparently does not affect the
call. This conservative abstention avoids presupposing expansion or configuration.

Only calls with a simple identifier, global scope visibility and no observed
shadowing receive imported candidates. A target requires a top-level function,
an explicit export and an unambiguous identity. The reference keeps the import
declaration; rebinding clears the previous resolution and consults the facts of all
current files. It does not look up a target by basename or by name in any other module.

New fields have defaults compatible with earlier payloads; the extractor version
invalidates the old extraction cache. The lexical projection stays at v1.
Lack of support is preserved in the reference's reason/status and in the capability
limitations; it never becomes proof that the symbol does not exist.
