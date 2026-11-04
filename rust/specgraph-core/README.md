# specgraph-core

Native symbol extraction, binding and test impact for SpecGraph
([ADR-SPECGRAPH-015](../../docs/adr/ADR-SPECGRAPH-015.md)).

```text
cargo build --release
specgraph-core impact --root . --source-root src --source-root tools \
    --test-root tests --test-root tools/tests --cache .specgraph/core-cache \
    --changed src/specgraph/cache.py:238
specgraph-core facts --root . --source-root src src/specgraph/cache.py
```

`impact` prints JSON: reached test functions (`path::Class::test`), whole test files
with the reason each was selected, the mode per change (`symbol`, `class-body`,
`module`, `unknown-file`), counters and timings. Facts are cached per file by size
and modification time; only changed files are parsed again, in parallel.

- `facts.rs` — per-file facts with tree-sitter: definitions with line ranges, imports,
  reference chains, assignments, annotations and string literals.
- `graph.rs` — project binding and reverse impact with the conservative rules.
- `main.rs` — CLI, discovery (`git ls-files`, or a directory walk) and cache.

`specgraph.test_selection.symbol_impact` uses the binary from `$SPECGRAPH_CORE`,
`PATH` or this checkout's release build, and falls back to import-level impact.
