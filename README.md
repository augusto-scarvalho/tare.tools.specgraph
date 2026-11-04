# tare.tools.specgraph

SpecGraph indexes a repository's code, specifications, architecture decisions (ADRs) and declared test links into a local SQLite graph. People and coding agents query that graph through a CLI, a Python SDK and a read-only MCP server, and get bounded answers that state which snapshot and scope they cover.

Python is analyzed with the standard `ast` module. TypeScript and Rust are supported through optional tree-sitter grammars. The [North Star](docs/ADR-001_SPECGRAPH_NORTH_STAR.md) describes the intended product; it is not a statement that every capability or performance target has shipped. `specgraph capabilities` reports what is implemented and its limits.

[Project intelligence guide](docs/PROJECT_INTELLIGENCE.md) · [Documentation index](docs/INDEX.md) · [Canonical specification](docs/SPEC-SPECGRAPH-001.md) · [Português](README.pt-BR.md)

## Relation to tare.tools.os

SpecGraph is one of the satellite repositories of [`tare.tools.os`](https://github.com/augusto-scarvalho/tare.tools.os). The OS pins it as a Git submodule under `satellites/tare.tools.specgraph`, installs it into the same environment, and calls its Python API and CLI to give agents project context (`specgraph index`, `specgraph ground`, role context for hooks). SpecGraph does not import the OS and can be used on its own. Its optional integration tests use two other satellites: `tare.tools.kernel` (provider and process ports) and `tare.tools.agent-runtime` (the agent host).

## Requirements

- Python 3.10 or newer, and Git.
- Optional: the `multilanguage` extra (tree-sitter grammars for TypeScript and Rust) and the `fast` extra (`ruff`, used for the import graph in test impact).
- Optional: a Rust toolchain to build `rust/specgraph-core`, the native symbol-impact engine used by test selection ([ADR-SPECGRAPH-015](docs/adr/ADR-SPECGRAPH-015.md)). Without it, SpecGraph falls back to import-level impact.

## Install from a clone

```bash
git clone https://github.com/augusto-scarvalho/tare.tools.specgraph.git
cd tare.tools.specgraph
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
specgraph --version
```

Add the optional extras with `python -m pip install -e ".[dev,multilanguage,fast]"`.

## Index a project

```bash
specgraph --root /path/to/project init --name my-project
# Edit specgraph.yaml so it lists the project's real code, test and document paths.
specgraph --root /path/to/project index
specgraph --root /path/to/project query parse_file --format json
specgraph --root /path/to/project symbol 'src/parser.py:parse_file'
specgraph --root /path/to/project refs 'src/parser.py:parse_file'
specgraph --root /path/to/project code-context 'src/parser.py:parse_file' --max-bytes 8192
specgraph --root /path/to/project doctor
specgraph --root /path/to/project serve
```

`init` writes `specgraph.yaml` and a sample specification. Run `index` after edits. Extraction is reused only when source, configuration and parser identities match; `index --full` re-extracts everything. Indexing still discovers and hashes sources and assembles relations globally. Queries read the published snapshot without walking the source tree. Responses identify the revision, content and coverage, and state that the current worktree was not checked on the read path.

`query`, `symbol`, `refs`, `neighbors`, `path`, `explain` and `code-context` require a published index. `search` and `resolve` keep a labeled compatibility path for repositories without one. `doctor` reads metadata; `doctor --deep` runs live governance validation. `sync-cache`, `impact` and `staleness` keep the separate legacy JSON-cache contract, including its source validation costs.

## Keep context useful after edits

`specgraph reconcile` observes source changes and acknowledges delivered edit hints. `specgraph watch` runs the periodic reconciler in the foreground; it is not installed as a service. Hooks journal edits without indexing synchronously. This repository sets `hook_context.automatic_roles: []`, so role context is requested explicitly instead of being repeated for ordinary reads and edits.

```bash
specgraph reconcile --format json
specgraph hook-context --role implementer --symbol 'src/parser.py:parse_file'
specgraph doctor --effectiveness-report path/to/paired-evaluation.json
```

Exact declarative bindings in `intent_mapping_paths` require the owner source, its SHA-256, the acceptance criteria and the exact target ID. They are traceability declarations, not executed coverage. See the [project intelligence guide](docs/PROJECT_INTELLIGENCE.md).

## What the graph means

Python symbols have file and lexical identities. Static resolution preserves aliases and supported local references; ambiguous, external and dynamic occurrences stay explicit. An observed static dependency is not proof of a runtime call. The native AST analysis is not a complete code property graph, data-flow analysis or universal dynamic-dispatch resolver.

Specifications use YAML (or JSON) frontmatter with `id`, `acceptance_criteria` and optional `target_symbols`. Code can declare `@spec SPEC-ID`; tests can declare `@pytest.mark.verifies("SPEC-ID", "AC-01")`. ADRs declare `governed_specs`. These declarations establish traceability, not proof that a test ran or passed.

`indexed_paths` controls retrieval coverage. `governed_paths` controls orphan-code enforcement. Exclusions and supported languages limit negative answers: no result means no match in the reported snapshot and scope, not proof that a behavior cannot exist.

## Bounded context and federation

`context SPEC-ID` and `bundle --spec SPEC-ID` return the complete selected acceptance criteria and source snippets, or refuse when the byte budget is too small. `code-context` retrieves code neighborhoods from persisted source ranges. [The canonical context contract](docs/SPEC-SPECGRAPH-001.md) documents the live renderer.

`ground` projects selected metadata from a sources manifest that the host generates from Git ([contract](docs/LIBRARY_GROUNDING_CONTRACT.md)); metadata alone does not mean source bodies were acquired or that a Work item can run. Repository owners stay authoritative for their documents, and inferred suggestions never replace accepted specifications.

The MCP stdio server implements protocol 2025-06-18 with the read-only tools `query_causal_graph`, `get_context_envelope` and `check_drift`. Source freshness is reported separately from snapshot consistency.

## Run the tests

From the repository root, with the virtual environment active:

```bash
python tools/check_completion_contracts.py
python -m pytest tests tools/tests
```

With only the `dev` extra, the TypeScript/Rust, Kernel and native-core tests are skipped. CI runs the complete suite on Windows and Linux with every extra, the two satellites at the revisions pinned in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) (`KERNEL_REVISION`, `RUNTIME_REVISION`) and the native core built:

```bash
python -m pip install -e ".[dev,multilanguage,fast]" pydantic \
  "git+https://github.com/augusto-scarvalho/tare.tools.kernel@<KERNEL_REVISION>" \
  "git+https://github.com/augusto-scarvalho/tare.tools.agent-runtime@<RUNTIME_REVISION>"
cargo test --release --locked --manifest-path rust/specgraph-core/Cargo.toml
cargo build --release --locked --manifest-path rust/specgraph-core/Cargo.toml
python -m pytest tests tools/tests
```

Changes to this repository add a bullet under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md); `python tools/install_git_hooks.py` installs the pre-push check ([policy](docs/CHANGELOG_POLICY.md)).

## Measure

```bash
specgraph --root /path/to/project bench parse_file RepositoryIndex --samples 100 --rounds 2
```

The benchmark measures complete SDK query calls, including opening SQLite and serializing output, and reports its repository and environment. Interpreter startup, indexing, MCP transport and agent reasoning are separate costs. Sub-50 ms retrieval and 65% context savings remain targets until measured on an agreed workload.

The [completion program](planning/README.md) lists the contracts (C0–C8, X1–X5), their specifications and decisions. Earlier qualification records are no longer in the tree; [docs/evidence](docs/evidence/README.md) explains how to read them from Git history.

## License

Apache-2.0, as declared in [pyproject.toml](pyproject.toml).
