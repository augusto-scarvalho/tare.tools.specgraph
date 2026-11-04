# ADR-SPECGRAPH-015 — Native core for symbol extraction, binding and impact

Status: ACCEPTED — operator instruction, 2026-09-27.
Work: SPECGRAPH-COMPLETION-X5.
Narrows, without revoking the rest, the "no Rust core" rule of [ADR-SPECGRAPH-002](ADR-SPECGRAPH-002.md).

X5 measured 26 real changes (26 commits, 16 modules) on Windows and Linux.
Tracing alone missed failures in 8 cases. Static imports per module eliminated
the misses, but selected ~79% of the suite. Per-symbol impact with type-aware
resolution (SCIP via scip-python) also eliminated the misses with a median
of 144 of 751 tests, but took 37 s per indexing and requires Node/pyright. The
existing Python resolver binds only 3.7% of SpecGraph's own references.

Decision: `rust/specgraph-core` implements, in Rust and only on this hot path,
per-file extraction (tree-sitter-python) with a per-file cache, "type-lite" binding
(scopes, imports and reexports, `self`/`cls`, constructors, annotations, instance
attributes, inheritance) and reverse impact up to tests. Conservative rules cover what
the analysis does not prove: a class body reaches all its members; a module-level change
reaches whoever imports the module; a module named in a string (`-m`, `-c`, `tools/x.py`)
counts as a use; a test fixture or helper selects the file, whoever imports it and the
directory of a `conftest.py`; `if __name__ == "__main__"` is not import time.

Python calls the binary over JSON and keeps the per-module policy (Ruff/AST) when
the binary does not exist. The rest of SpecGraph stays Python/SQLite. PyO3/wheels wait
until the API is stable.

Measured acceptance: zero real misses in the 26 cases on both platforms, median of
148 tests (mean 211) of 751, ~0.15 s per query with cache; OS (734 Python
files) in ~0.8 s with cache. The only recorded divergence is
`test_concurrent_save_manifest_threads`, which fails under load in the complete Linux suite,
is not reachable from the change and passed 30/30 in isolation.

Consequence: the answer key (real cases and recorded failures) is the regression test
of the core; any new rule must keep zero misses on it. This does not qualify
selective execution: the fallback to the complete suite stays until the X5 C5 gate.
