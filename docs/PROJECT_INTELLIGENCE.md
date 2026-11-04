# Using project intelligence

Start from the published index: `specgraph query name --format json`. Use the exact ID it returns with `symbol`, `refs`, `neighbors`, `path` or `code-context`. The response identifies the generation and the scope; missing paths and relations do not prove that something does not exist outside them.

`query` and `resolve` also accept the code's vocabulary, for example
`specgraph query "invoice checksum" --limit 5 --max-bytes 8192 --format json`.
snake_case/CamelCase names, scopes and docstrings are normalized at publication
and searched with FTS5. The response reports the matching terms and fields;
`match.identity: false` separates lexical relevance from exact identity.
Portuguese and English function words and accents are normalized, without
translation or embeddings. Exact identifiers keep priority; `search` keeps the
literal API.

Natural-language queries may add one regular English plural spelling per term,
such as `identity/identities`. `match.expansions` lists the alternatives that
actually matched. Ranking weighs name/title, docstring/body and path
separately; the weights are heuristic, not semantic confidence. A query has at
most 32 terms and two spellings per term. Compound identifiers are not expanded.
There is no translation, no synonyms, no verb inflection and no complete plural
handling.

Documents can lead to the implementation through the published relations
`CODE_IMPLEMENTS_SPEC` and `SPEC_GOVERNED_BY_ADR`, up to two hops. Definitions
come before documents and the optional intent context. `--max-hops 0` turns
this expansion off. `linked_specs` derives from the published edges, including
frontmatter and accepted mappings; the origin of each relation and the document
hash appear in the intent context. None of this attests execution or correctness.

Ranking examines at most 100 candidates per index; search expansion takes at
most ten input documents and 128 edges, and the optional context at most three
definitions and four ADRs per SPEC. The node, result and byte limits still
apply. Omitted candidates and context are reported explicitly; whole units are
removed to fit in UTF-8, without cutting criteria or snippets. This ceiling
bounds the work after selection by postings; it does not guarantee a constant
FTS cost for any corpus size.

After upgrading from an earlier version, run `specgraph index` or
`specgraph reconcile`. `doctor` reports `retrieval_projection`; queries on older
projections keep compatible reads and report `REINDEX_REQUIRED` when they need
lexical code search. Reads never migrate the index.

## Publication and reads

`specgraph index` observes the configured files, reuses extractions bound to the bytes and to the parser version, resolves relations and publishes SQLite in one transaction. The parser reads a captured copy of each extracted file; observed changes prevent mixing an AST with a different source. This does not turn a mutable checkout into an atomic Git point-in-time snapshot.

Reads use one transaction per call. They do not walk the sources to prove freshness. `index --full` forces extraction; `doctor --deep` validates current governance. The legacy JSON cache of `impact`/`staleness` is a separate contract and requires `sync-cache`.

### Keeping the index current

```bash
specgraph reconcile --format json
specgraph watch --interval-seconds 30 --debounce-seconds 0.3
```

`watch` is a foreground process; stop it with Ctrl+C. One watcher per root holds the execution lease; a dead process loses the lease when it expires. It is not installed as a service. The post-edit hook records paths in a SQLite journal outside the checkout, without parsing, scanning or an indexing subprocess. Repeated events are idempotent; paths are coalesced and the backlog is bounded. The watcher reacts after the quiet window and also checks file SHA-256 values periodically, even without events. When a pass finds no change, it checks the sources before and after and skips extraction and relation assembly.

Events that arrive during a publication stay pending for the next pass. A failure or partial coverage does not confirm the reconciliation. `doctor` reports the method, the age of the last successful observation, the pending count and the watcher state. This information does not prove the checkout was fresh at the moment of a query. Hooks show what is pending with at most eight paths; age and heartbeat do not cause identical context to be sent again. The SDK provides `record_changes`, `reconcile_repository`, `watch_repository` and `lifecycle_status`.

`resolve` queries the published generation first, even when learned recovery exists. The v2 recovery cache binds root, configuration, limit, origin and generation; v1 records are not promoted automatically. A new generation is checked before the fallback is delivered. Literal text stays literal text. Injected structural probes without the SHA-256 of the source bytes may return advisory information, but they are not learned as validated structural evidence.

## Languages and visualization

```bash
python -m pip install -e ".[multilanguage]"
specgraph index
specgraph report --html specgraph-report.html
```

Python uses the native AST and static resolution with scopes. TypeScript/TSX and Rust use tree-sitter with optional grammars. Definitions, ranges, containment and call occurrences are indexed. Besides the supported local calls, TypeScript resolves relative named imports of directly exported functions; Rust resolves direct imports of `pub` functions in declared child modules. The [alias contract](specs/SPEC-SPECGRAPH-CROSSFILE-ALIASES.md) bounds the accepted paths and forms. Re-exports, type resolution, compiler configuration, `crate::` paths, grouped Rust imports, macros and dispatch stay unresolved. Missing grammars appear in the diagnostics. Run `index` or `reconcile` after upgrading: new facts require a new extraction, done explicitly by the writer.

The HTML report is an offline file with filters, source selection, directed paths and upstream impact. It states the node and edge cuts and queries only the exported subset. The `import_scip_json` adapter accepts an explicit subset of SCIP protobuf-JSON; it does not assume the distribution version is the protocol version and never changes the index automatically.

## Context by role

| Role | Information the service selects |
|---|---|
| Planner | Symbols, intent and complete criteria of the selected SPECs |
| Implementer | Identities, ranges, neighbors, declared tests and short complete snippets |
| Auditor | Links, declared tests, unresolved references and an explicit absence of attestation |
| Explorer | Symbols and neighbors to guide the next query |

The service receives an explicit root, selection and Work. It never picks a task on its own. Repeated context stays silent; after an edit it accumulates observations without a complete synchronous analysis; stopping does not require approving context. The context service uses no LLM and no network.

Text queries from the hook share the lexical retrieval of `query` and
`resolve`. Matches report the reason and, when they follow a SPEC or ADR, the
origin and the declared path. Documents without retrieved implementation can
still provide useful pointers. An explicit selection by file, symbol or SPEC
keeps priority; a SPEC selected without a file or symbol delivers its intent
without a broad search; the whole package respects the budget, including the
match evidence.

The `--symbol src/module.py:function` selector picks an exact identity without rediscovering the file. Repeated neighbors are deduplicated and state their direction; tests appear as declared references, without claiming execution. Planners may receive the complete criteria of linked intents; implementers receive short complete snippets when they fit the budget. Large optional units are omitted whole, never cut to look complete.

```bash
specgraph hook-context --role auditor --file src/specgraph/indexer.py --max-bytes 4096
specgraph hook-context --role planner --spec SPEC-SPECGRAPH-C4
```

A project enables the Kernel adapter with `hook_context.enabled: true` in `specgraph.yaml`; this repository already sets it. The edited file or an explicit workdir selects the declarative root, so a whole workspace is not treated as one project. The OS composes the same service in its session events, with an external receipt for deduplication.

This project uses `hook_context.automatic_roles: []`: context on demand, by explicit query, SPEC or symbol. The post-edit hook still records changes. Without this field, compatibility keeps the four automatic roles; an explicit list enables only the roles that were evaluated. The conservative choice came from real paired tasks: in that small sample, the smaller payload saved few total tokens and did not reduce wall time. This does not establish effectiveness on complete production tasks.

`doctor` separates the service's availability, registration, triggering, delivery and effectiveness. Testing the callback does not prove a daemon reload or savings on a task. The persistent SDK/MCP avoids starting Python for each query; the CLI has its own cost, measured with `bench --transport cli`.

With the Kernel installed in the same interpreter, `doctor` reads bounded observations from the hosts. Declared configuration, adapter invocation, returned output and payload observed in the transcript are distinct stages. The handler does not read transcripts: that analysis is an explicit action outside the synchronous path. `doctor --effectiveness-report PATH` includes an explicitly selected experiment as a report, without attesting production benefit. Missing integration or evidence appears as unavailable or not observed.

Local links can be declared in `intent_mapping_paths`. Each mapping requires a qualified source, the SHA-256 of the SPEC file, the ACs and the exact identity of the target. Changing the file takes part in reconciliation and in the strict cache; conflicts, different sources and missing targets are diagnosed. A declared link or test does not prove complete implementation or successful execution.

## Federated sources and review

`ground` projects metadata from a sources manifest that the host generates from Git ([contract](LIBRARY_GROUNDING_CONTRACT.md)). To read the selected bodies, use `ground --read-bodies --repo REPO --library-manifest MANIFEST --owner REPO=CHECKOUT --include-id DOCUMENT`. Acquisition verifies commit, path and SHA-256 against the owner's Git blob. A new, uncommitted document never gets a fictitious pin.

`review-bundle HEAD --max-bytes 8192` selects the diff against a real commit and requires it to match the bytes of the published snapshot. The graph is the current one, with K=1 retention; it is not presented as a historical graph. Removed sources or an oversized selection produce explicit limits. The SDK accepts explicitly acquired federated documents.

`resolve_intent_mappings` produces exact links or advisory candidates; it never rewrites the source or accepts conflicts automatically. `evaluate_evidence` compares source, criteria, configuration, environment, dependencies and artifacts with an attestation supplied by the executor's trusted verifier. Test declarations and JUnit XML do not promote evidence to Tier 1. Admission and Work state stay with their owners.

The Kernel keeps the bytes of execution receipts in `ObservedWorkProgress.evidence_bytes`. The `project_executor_observation` adapter validates these receipts and binds them to the candidate and its descriptor, keeping `NEEDS_REVALIDATION`, unobserved coverage and unverified attestation. The OS writes this observation into the candidate's custody and checks commands, criteria, results and identities before treating the observed verification as complete. This does not grant a higher evidence class.

At admission, the OS compares the Backlog Work with the exact AC text of the Git body that the run's sources manifest selects. A mismatch blocks admission and produces `SOURCE_ADMISSION.json`.

## Qualification

Run `specgraph bench parse_file RepositoryIndex missing___never --samples 100 --rounds 2` and repeat it with `--transport cli` to measure the whole process. The results record p50/p95/p99, samples, corpus identity and excluded costs. They do not replace qualification on real hosts, a reference profile, or measuring task success and context savings.

`tools/qualify_benchmark.py` pins a corpus and a copy of the implementation in real isolated commits, with configuration, hardware, raw samples and the SQLite location. On WSL, keep the state on the native Linux filesystem (the default, under `~/.local/state`); a profile with the database under `/mnt/c` cost much more. Results from different profiles are not a universal latency guarantee. `tools/qualify_hook_tasks.py` runs paired microtasks with a real model and includes cache and response tokens; preparation costs and the experiment's limits are reported separately.

See [the C7/C8 qualification and its limits](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08-c7-c8/README.md), [the initial evidence](https://github.com/augusto-scarvalho/tare.tools.specgraph/blob/d9f65e069d3f597f0744f58462441d27ce1ab4ef/docs/evidence/2026-09-08/README.md) and [the completion program](../planning/README.md).
