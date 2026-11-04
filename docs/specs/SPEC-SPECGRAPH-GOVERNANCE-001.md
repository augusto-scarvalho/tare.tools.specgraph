---
id: SPEC-SPECGRAPH-GOVERNANCE-001
title: Concrete contract for residual per-target remediation
status: ACCEPTED
version: 1.0.2
owner: tare.tools.specgraph
work_id: SPECGRAPH-FOLLOWUP-GOVERNANCE-20260908
decision_authority: operator_instruction
delivery_status: NOT_QUALIFIED
authority: NONE
target_symbols: []
acceptance_criteria:
  AC-01: AST extraction of APIs and tools preserves identities, fields, literal methods, local prefixes and explicit annotations without executing the analyzed module.
  AC-02: Bounded SQL extraction recognizes tables and views, fields and explicit tags, reporting unrecognized residual content.
  AC-03: Shallow JSON Schema extraction preserves properties, required and tags; read or JSON errors do not become an empty success.
  AC-04: AST extraction of Python models recognizes supported bases and decorators and local annotated fields without importing models or resolving dynamic inheritance.
  AC-05: Ontology federation validates the exact owner, revision, hash, version and inventory before returning an ordered view; any divergence refuses the whole view.
  AC-06: The public SDK loads only requested exports and the CLI preserves explicit selection, defaults, formats and existing exit codes, without inferring authorization or qualification; CLI --version must announce the same version as the SDK and the package, using a single maintained version reference to avoid divergence.
  AC-07: Each of the 108 targets receives a verifiable individual disposition; before/after inventories preserve scope, errors and residual debt without binding inheritance or promoting a declaration to execution.
---

# Contract prior to implementation

Accepted by the operator's explicit instruction on 2026-10-15, exclusively as the contract of this Work. ACCEPTED does not mean implementation, dispatch admission, qualified delivery or backlog completion. Evidence authority: NONE. It does not change the C0..C8/X1..X5 program, its parameters, exclusions, contracts or history. No binding is created here: `target_symbols` stays empty. The lists below are an inventory and correspondence proposals, not mappings nor declarations of executed tests. AC-07 governs the remediation procedure; using it as a universal functional binding for the code is forbidden.

## Baseline and custody

Immutable baseline: private source `current-baseline.json`, schema/inventory of the 2026-10-15 revalidation batch, SHA-256 of the bytes `3a90d643812338083392f15029216ccef2ae97a7c3379430cb96a3178d7295bd`. The referenced history has SHA-256 `06d5ef4a2428f2af6d5609cf086dd2c59b65d1519d0e8247076e6c5347fcdbca`. The document does not embed the private batch, its machine paths or its raw receipts. The numbers are observations of that baseline, not proof of a new execution.

There are 101 code targets and seven schema targets, a total of 108 (kind, ID) pairs. Some IDs are equal in both kinds: do not deduplicate across code and schema. History: 55 code + seven schema still orphaned. Additional in HEAD: 46 code absent from the historical set, not necessarily introduced by that commit. Specs are separate debt and are not among the 108.

Preserve `governed_paths: [src]`, `indexed_paths: [src]`, `python_source_roots: [src]`, `test_paths: [tests]`, `intent_mapping_paths: [planning/traceability-mappings.json]`; exclusions `.git`, `.venv`, `__pycache__`, `.pytest_cache`, `.specgraph`, `build`, `dist`. Preserve all other parameters of `specgraph.yaml` (SHA-256 `ecc5d9641464b9596e4a7b2654a168a008d30079bf95d9d0d349b3bf59e607d3`), including hook enabled, empty automatic_roles, implementer, 4096 bytes and 50 ms. Do not shrink src nor add exclusions to obtain PASS.

Before dispatch, check HEAD, configuration bytes, referenced sources and contracts, and the inventory by kind/ID. A change requires a new baseline identified by commit/hash and an explicit revision of this contract; do not overwrite the historical observation. A missing ID does not prove a legitimate removal nor the absence of a renamed successor.

The acceptance-time baseline is not published: neither the commit it was taken at nor the SHA-256 of the 13 legacy `src/specgraph` sources inspected there.

## Reused criteria, with exact source

The references below use owner `tare.tools.specgraph` and a relative path; a future mapping row must include `source_ref`, the hash of the bytes, the list of exact ACs, kind, the owner-qualified target_id, an individual rationale and valid test_refs. The hash of this contract will be computed after it is written, never self-referenced in the file itself.

The SHA-256 values these contracts had when this one was accepted are not published; their bytes have changed since, and each mapping row in `planning/traceability-mappings.json` carries the current hash of its source.

| Existing contract | Bounded application |
|---|---|
| docs/specs/SPEC-SPECGRAPH-C5.md | AC-01 mapping helpers; AC-03 relation classification; AC-04 composite evidence validity |
| docs/specs/SPEC-SPECGRAPH-C7.md | AC-01 hook budget helper, not host adoption |
| docs/specs/SPEC-SPECGRAPH-X5.md | AC-01 local observation; AC-02 mutation comparison; AC-03 fallback/scope; AC-04 exploratory measurement |
| docs/specs/SPEC-SPECGRAPH-LEXICAL-INTENT.md | AC-02 normalization; AC-03 limits of the declared expansion |
| docs/specs/SPEC-SPECGRAPH-LEXICAL-QUALITY.md | complementary quality limits; do not bind helpers to ranking they do not implement |

The general canonical causality/context criteria do not adequately describe the legacy parsers. For them, use this contract's functional AC-01..06 according to the inventory, not a generic governance criterion.

## Historical code inventory — 55 exact IDs

Each line is an independent target, with an explicit functional proposal. The header sets the proposed criterion for the lines of that block, not binding inheritance from a class/module.

### API/MCP — 14 code; proposal GOVERNANCE-001:AC-01

```text
src/specgraph/api_parser.py:ASTApiVisitor
src/specgraph/api_parser.py:ASTApiVisitor.__init__
src/specgraph/api_parser.py:ASTApiVisitor._collect_prefixes
src/specgraph/api_parser.py:ASTApiVisitor._extract_spec_links
src/specgraph/api_parser.py:ASTApiVisitor._inspect_function
src/specgraph/api_parser.py:ASTApiVisitor.visit_AsyncFunctionDef
src/specgraph/api_parser.py:ASTApiVisitor.visit_ClassDef
src/specgraph/api_parser.py:ASTApiVisitor.visit_Expr
src/specgraph/api_parser.py:ASTApiVisitor.visit_FunctionDef
src/specgraph/api_parser.py:ApiAndMcpParser
src/specgraph/api_parser.py:ApiAndMcpParser.discover_apis_and_tools
src/specgraph/api_parser.py:ApiAndMcpParser.parse_file
src/specgraph/api_parser.py:ApiEndpoint
src/specgraph/api_parser.py:McpToolContract
```

### Schemas — 14 code; proposals per line

```text
src/specgraph/schema_parser.py:ASTPydanticVisitor | AC-04
src/specgraph/schema_parser.py:ASTPydanticVisitor.__init__ | AC-04
src/specgraph/schema_parser.py:ASTPydanticVisitor._extract_spec_links | AC-04
src/specgraph/schema_parser.py:ASTPydanticVisitor.visit_ClassDef | AC-04
src/specgraph/schema_parser.py:JSONSchemaParser | AC-03
src/specgraph/schema_parser.py:JSONSchemaParser.parse_file | AC-03
src/specgraph/schema_parser.py:SQLSchemaParser | AC-02
src/specgraph/schema_parser.py:SQLSchemaParser._extract_spec_tags | AC-02
src/specgraph/schema_parser.py:SQLSchemaParser.parse_file | AC-02
src/specgraph/schema_parser.py:SchemaContract | AC-02,AC-03,AC-04
src/specgraph/schema_parser.py:SchemaField | AC-02,AC-03,AC-04
src/specgraph/schema_parser.py:SchemaParser | AC-02,AC-03,AC-04
src/specgraph/schema_parser.py:SchemaParser.discover_schemas | AC-02,AC-03,AC-04
src/specgraph/schema_parser.py:SchemaParser.parse_python_models | AC-04
```

### Ontology — 16 code; proposal GOVERNANCE-001:AC-05

```text
src/specgraph/ontology.py:FederatedOntologyView
src/specgraph/ontology.py:FederatedOntologyView.lookup
src/specgraph/ontology.py:FederatedOntologyView.to_dict
src/specgraph/ontology.py:OntologyConcept
src/specgraph/ontology.py:OntologyConcept.to_dict
src/specgraph/ontology.py:OntologyFederationError
src/specgraph/ontology.py:OntologySource
src/specgraph/ontology.py:OntologySource.to_dict
src/specgraph/ontology.py:_git_head
src/specgraph/ontology.py:_load_concepts
src/specgraph/ontology.py:_optional_string
src/specgraph/ontology.py:_required_string
src/specgraph/ontology.py:_safe_relative_path
src/specgraph/ontology.py:_string_tuple
src/specgraph/ontology.py:load_federated_ontologies
src/specgraph/ontology.py:parse_owner_roots
```

### SDK/CLI — six code; proposal GOVERNANCE-001:AC-06

```text
src/specgraph/__init__.py:__dir__
src/specgraph/__init__.py:__getattr__
src/specgraph/cli.py:build_parser
src/specgraph/cli.py:dump_trace
src/specgraph/cli.py:handle_init
src/specgraph/cli.py:main
```

### Mapping and hook — five code; existing specific proposals

```text
src/specgraph/traceability.py:_read | SPEC-SPECGRAPH-C5:AC-01
src/specgraph/traceability.py:_relative | SPEC-SPECGRAPH-C5:AC-01
src/specgraph/traceability.py:_unique_object | SPEC-SPECGRAPH-C5:AC-01
src/specgraph/traceability.py:mapping_files | SPEC-SPECGRAPH-C5:AC-01
src/specgraph/hook_context.py:context_hint.bounded | SPEC-SPECGRAPH-C7:AC-01
```

## Historical schema inventory — seven exact IDs

The same classes are also independent schema records. Functional proposals:

```text
src/specgraph/api_parser.py:ApiEndpoint | AC-01
src/specgraph/api_parser.py:McpToolContract | AC-01
src/specgraph/ontology.py:FederatedOntologyView | AC-05
src/specgraph/ontology.py:OntologyConcept | AC-05
src/specgraph/ontology.py:OntologySource | AC-05
src/specgraph/schema_parser.py:SchemaContract | AC-02,AC-03,AC-04
src/specgraph/schema_parser.py:SchemaField | AC-02,AC-03,AC-04
```

## Additional HEAD inventory — 46 code

X5 proposals do not accept the experiment as delivered: they are correspondences to the bounded mechanism described in the existing contract, which remains PROPOSED with a decision against broad adoption. Do not promote its status to fix orphans.

```text
src/specgraph/_pytest_x5.py:_stop | SPEC-SPECGRAPH-X5:AC-01,AC-03
src/specgraph/_pytest_x5.py:_charge | SPEC-SPECGRAPH-X5:AC-01,AC-03
src/specgraph/_pytest_x5.py:pytest_collection_finish | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/_pytest_x5.py:pytest_runtest_logreport | SPEC-SPECGRAPH-X5:AC-01,AC-04
src/specgraph/_pytest_x5.py:pytest_runtest_protocol | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/_pytest_x5.py:pytest_runtest_protocol.trace | SPEC-SPECGRAPH-X5:AC-01,AC-03
src/specgraph/_pytest_x5.py:pytest_sessionfinish | SPEC-SPECGRAPH-X5:AC-01,AC-03
src/specgraph/_pytest_x5.py:pytest_sessionfinish.payload | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/evidence.py:classify_test_relation | SPEC-SPECGRAPH-C5:AC-03
src/specgraph/lexical.py:indexed_text | SPEC-SPECGRAPH-LEXICAL-INTENT:AC-02
src/specgraph/lexical.py:match_expression | SPEC-SPECGRAPH-LEXICAL-INTENT:AC-02
src/specgraph/retrieval.py:retrieve.admit | SPEC-SPECGRAPH-LEXICAL-INTENT:AC-03
src/specgraph/test_selection.py:_sha | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/test_selection.py:_files | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:_environment | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files.files | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files.reached | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files.names_in | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files.dependencies | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:static_test_files.file_dependencies | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:_ruff_import_graph | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:core_binary | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:symbol_impact | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:_lazy_package | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:pytest_command | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/test_selection.py:_run | SPEC-SPECGRAPH-X5:AC-01
src/specgraph/test_selection.py:capture | SPEC-SPECGRAPH-X5:AC-01,AC-04
src/specgraph/test_selection.py:_change | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:_applicability | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:_applicability.safe | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/test_selection.py:recommend_from_receipt | SPEC-SPECGRAPH-X5:AC-01,AC-03; SPEC-SPECGRAPH-C5:AC-04
src/specgraph/test_selection.py:_confirm | SPEC-SPECGRAPH-X5:AC-02
src/specgraph/test_selection.py:compare | SPEC-SPECGRAPH-X5:AC-02,AC-04
src/specgraph/test_selection.py:compare.seconds | SPEC-SPECGRAPH-X5:AC-04
src/specgraph/test_selection.py:compare.run | SPEC-SPECGRAPH-X5:AC-02,AC-04
src/specgraph/test_selection.py:compare.confirm_runner | SPEC-SPECGRAPH-X5:AC-02
src/specgraph/test_selection.py:compare.excluded | SPEC-SPECGRAPH-X5:AC-02
src/specgraph/test_selection.py:recommend_tests | SPEC-SPECGRAPH-X5:AC-01,AC-03
src/specgraph/x5_scope.py:_text | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:_path | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:_block | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:_nodeid | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:_json | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:_build | SPEC-SPECGRAPH-X5:AC-03
src/specgraph/x5_scope.py:build_x5_scope | SPEC-SPECGRAPH-X5:AC-03
```

## Reproducible behaviors and falsifiers by family

Future procedure: use a synthetic temporary directory, local UTF-8 sources, no network/models; call the named APIs directly and compare exact structural results. Freeze fixtures and expected results before the implementation. Each receipt must identify command, executor, commit, hashes of sources/config/fixtures, environment, exit code and exercised criterion. The steps below are falsification plans, not tests declared as existing or executed.

### F-API — AC-01

Create `routes.py` with `APIRouter(prefix='/v1')`, a local include with prefix `/api`, an async function decorated `router.get('/items')`, `@mcp.tool(name='lookup')` and docstring `@spec SPEC-DEMO [AC-01]`. Call `ApiAndMcpParser.parse_file(path, root)`: check endpoint `api:GET:/api/v1/items`, exact relative handler, lines, docstring, tag and `mcp:lookup`, positional parameters without self. Exercise a synchronous function, a class and a nested function to confront identity; imperative registration `add_api_route` with a resolved local handler and an unresolved handler (`dynamic_handler`). Check literal route/api_route methods and the GET default, APIRouter/Blueprint prefixes and local include/register. Falsify by removing a prefix, swapping the method, binding or identity, and require a difference in the expected result.

It does not promise runtime framework resolution, router imports across files, dynamic expressions or a complete MCP signature (the parser collects `node.args.args`). Invalid syntax, invalid UTF-8 or an external file must produce errors, not a successful extraction. Discovery must respect explicit directories/exclusions and aggregate the errors. The dataclasses must preserve the observed fields and independent lists per instance.

### F-SQL — AC-02

A `.sql` fixture with tag `-- @spec SPEC-DEMO [AC-01]`, `CREATE TABLE items (id INTEGER NOT NULL, amount NUMERIC(10,2), PRIMARY KEY(id));` and a view: check IDs `file:name`, kinds, lines, raw_definition, field id required and amount not required, without turning a constraint into a column. Add an unrecognized `ALTER TABLE`: require a residual error, even when recognized contracts are returned. It does not promise a complete SQL parser, nullability inference from a PK, semantic SQL validation or view fields. Root escape and invalid UTF-8 must be diagnosed.

### F-JSON — AC-03

A fixture with title, properties, required, `$spec` and an annotated description: verify the relative schema_id, kind, type/description of each property, required by membership, deduplicated tags and raw_definition limited to the first 2000 characters. Use a property without a type to obtain any and a non-object property for the defaults. JSON that does not identify a schema returns empty; malformed JSON returns an error. It does not promise resolving `$ref`, composition, recursive validation or every JSON Schema rule. Do not turn a parse error into a legitimate absence.

### F-PYMODEL — AC-04

AST of BaseModel/Schema and dataclass classes (simple, qualified and called decorator), field `x: int` and `y: str = ''`: check types via AST, required only without a value, and tags from the class's own docstring. A plain class must not become a schema. Check discovery of `.sql/.ddl/.json/.py`, exclusions, aggregated errors and that module side effects are not executed. It does not promise inherited fields, semantic aliases, Pydantic validation or qualified identity of nested classes (the schema extractor uses the simple name).

### F-ONTOLOGY — AC-05

Build a `tare.tools/federated-ontology-index/1.0` catalog and two temporary owners with YAML, full revisions supplied by a synthetic revision_reader and real hashes of the bytes. Require sources ordered by repository, concepts by id, counts, catalog_sha256 and exact/None lookup; compare to_dict of the three dataclasses. Change in isolation the revision, payload hash, version, inventory, a missing root, a duplicated ID or a duplicated owner: require OntologyFederationError without a partial view. Exercise required/optional strings, invalid lists and relationships, `../` and an absolute path, invalid/duplicated `REPOSITORY=PATH` syntax. `_git_head` must reject a subprocess with an error or an incomplete revision using a local stub, not the network. It does not promise cloning, repair, location inference, semantic resolution of relationships or a universal filesystem sandbox; any symlink/platform gap remains open and is not claimed as proven protection.

### F-PUBLIC — AC-06

Import the package in an isolated Python process with the local source: compare the sorted `dir` and exports, request one export and check caching in globals; an unknown name raises AttributeError. Preserved historical observation: SDK and package are 0.7.5 and the CLI announces 0.7.3 in the baseline; that divergence is a product error, not a functional limit nor behavior to preserve. Run CLI `--version` in an isolated process on the same local subject as the SDK/package and compare the announced version with the SDK's public version and the package metadata: require all three to be equal, with a successful exit. Require a single maintained version reference, shared or derived, without an independent literal in the CLI; the test must follow that reference, not freeze 0.7.5 as a future version. The falsifier must fail on the old baseline (CLI 0.7.3 versus SDK/package 0.7.5) and detect a divergence introduced in any of the three surfaces. These steps are later implementation and test requirements, not an execution declared in this revision.

In a temporary root, call build_parser and handle_init: keep the existing defaults and do not overwrite config/sample already present; init creates the scaffold only when requested. `dump_trace` must preserve JSON/YAML and tables per announced kind without promoting bindings to proof. Additional observation from the review: in the baseline the code/tests kinds accepted by the CLI produce only a header, and apis/mcp mix both types. That is a defect, not behavior to freeze in tests. Markdown/table tables must show the requested symbols or tests and separate APIs from MCP when the kind is specific. The kind filter in JSON/YAML is not promised (the implementation serializes the full matrix); the CLI help must state explicitly that the filter selects table/Markdown sections and that JSON/YAML preserve the full matrix. The falsifiers must require content and the absence of unselected sections, including code/tests and API versus MCP, instead of accepting empty output. For main, use handler stubs and small inputs: check parsing, forwarding of root/format/flags and the PASS=0/FAIL=1 returns in the handled branches. Do not run real capture/compare nor sweep every command for this contract. It does not promise read-only behavior for init/index: effects remain those of each explicit command, not authorized by this document.

Revision 1.0.1, prior to the filter repair: keeps the 108 IDs, criteria and historical baseline; corrects the F-PUBLIC expectation that accepted announced filters without content. The previous revision and its refused attempt remain identified in the private receipts. No new mapping was applied before this revision; the implementation uses the hash of this current version and preserves the reference to the initial contract, whose commit predates the published history.

### F-MAPPING — C5:AC-01

Create an explicit JSON mapping with the owner's source_ref, the real hash, an existing AC and an exact target. Check the helpers `_relative`, `_read`, `_unique_object` and mapping_files against traversal, backslash/colon, symlink, missing/non-JSON file, exclusion, duplicated key/file, and exceeded bytes and counts. Preserve the current limits: 16 files, 1 MiB per mapping, 2048 rows, source 262144 bytes. A changed source/missing AC must refuse the binding; a child member does not inherit the class's binding. In-memory inputs remain subject to the bytes type and the limit. A functional criterion of the mapping, not evidence that the target code executed.

### F-HOOK — C7:AC-01

Exercise context_hint with a long identity and an allowed max_bytes: check complete JSON plus LF in UTF-8; if it does not fit, require BUDGET_REQUIRED with a selection_digest SHA-256 of the canonical serialization, without clipping criteria/identity. post/stop/end events remain SILENT without a stop gate. Do not promise hard real-time latency nor host adoption from the nested helper's test.

### F-X5-OBS — X5:AC-01/AC-03/AC-04 and C5:AC-03

A synthetic hook with an inventory and setup/call/teardown reports: confront nodeids, failed and finite durations; an overflow of 4096 nodeids, 32768 pairs, text 1024 or JSON 1 MiB yields truncated/complete false and a reason without changing the pytest exit status. Trace must restore the previous callback and observe only Python lines under root; it does not prove threads/subprocesses/native code. Missing/unwritable output is not evidence of success. `_run` refuses a missing inventory, a failed collection and an origin outside the worktree when given. pytest_command uses an argfile above the 30000-character limit. Capture keeps fingerprints/config/environment/times and attestation NONE_LOCAL_OBSERVATION_ONLY. Classification: TEST_REFERENCES_SYMBOL → STATIC_REFERENCE; DECLARED_VERIFIES → DECLARED_ONLY; EXECUTED_COVERAGE remains distinct; unknown → UNKNOWN. Local JUnit/hash never earns Tier 1.

### F-X5-SELECT — X5:AC-03 and C5:AC-04

A fixture with a direct/transitive import, a lazy package, a script by path and a code string: verify conservative candidates and reasons. A missing/failing Ruff stub must activate AST; an unparseable file widens dependencies; a missing core returns None and falls back to the static policy. Do not claim universal closure because of this. For recommend_tests, `complete:true` remains FULL_SUITE. For recommend_from_receipt, change config/env, an unlisted fingerprint, an unknown mandatory or a block without reach: require fallback and reasons. Applicability CANDIDATE never authorizes execution nor supports_success.

For x5_scope, freeze a synthetic capture and current files: the canonical hash must change with target, capture, selection or mandatory; normalized order/deduplication must be deterministic. Reject non-representable JSON/NaN, control characters, traversal, an unknown nodeid, a line outside the file, file addition/removal, a mandatory outside the selection or an unlisted change. Without a verifier callable and independent current observations, EXTERNALLY_VERIFIED JSON does not enable selection. Qualified Python composition requires an exact C5 binding of the scope and an executor recheck before running; do not grant this through the CLI or a local receipt. It does not promise a global minimum nor waive mandatory tests.

### F-X5-COMPARE — X5:AC-02/AC-04

Plan a single mutation in a temporary copy, with synthetic runners for selection/full/confirmation: a full failure outside the selection must be rerun in isolation, distinguishing a confirmed omission from instability. Compare finite non-negative costs, phases, setup/cleanup and overhead; a case without measurement does not become zero savings. The comparator keeps the copy's exclusions and does not change the exclusions of the governed scope. An empty/multiple find and an escaping path must refuse the mutation. A confirmed omission suspends qualification; zero omissions in a bounded set does not demonstrate broad adoption nor a delivered 30% target. Campaigns, the full suite and models are not run in this preparation.

## Later disposition and gates

Each kind/ID pair remains OPEN in this contract. The next step must record per target: decision (exact functional mapping, behavior/contract correction or legitimate removal), justification, source_ref/hash/AC, falsifier and result/limits. A class does not grant a relation to its methods; code does not grant schema. The operator's current order already authorizes the necessary corrections of this Work's specific behaviors, including AC-06 version parity; do not require a new authorization for each necessary correction within that scope. Removing behavior and changing scope still require an explicit decision and evidence; they cannot be simulated by renaming/deleting targets or adding exclusions to obtain PASS. If the proposed correspondence does not survive the falsifier, keep it open and correct the contract/behavior before mapping. This revision changes only this document; it does not perform those corrections nor authorize changes to other contracts by inference.

After the authorized implementation: rerun doctor/drift in the same scope and publish before/after inventories by ID, errors, decisions and residuals. Preserve the historical observation including negative results. A structural PASS is not behavioral qualification. Bindings, test_refs/markers and observed execution are three distinct facts; attestation belongs to the executor and admission/Work to the backlog owner.

The six current orphan specs are separate, named residual debt:

- SPEC-SPECGRAPH-C8 — qualification evidence outside the source index;
- SPEC-SPECGRAPH-X3 — optional experiment;
- SPEC-SPECGRAPH-X4 — optional experiment;
- SPEC-SPECGRAPH-GRAPHIFY-COMPARISON;
- SPEC-SPECGRAPH-NATIVE-EDIT;
- SPEC-SPECGRAPH-RESPONSE-CONTRACT.

X1 and X5 exist and are not orphaned in the baseline, but that does not deliver their experiments. Do not declare the four optional experiments nor old Aider/AGY protocols as implemented to obtain PASS. The classification of these six specs does not authorize removal or artificial binding; their treatment remains separate from the remediation of the 108.

## Gaps before dispatch

Accepting this document removes only the absence of a concrete contract. Still missing: individual review of the correspondences, frozen fixtures/falsifiers, exact source/hash binding of the written contract in the dispatch envelope, and a baseline check on the subject actually dispatched. No behavioral execution or attestation was produced in this step. Mappings, tests, before/after doctor/drift and result evidence belong to the later implementation; they are not pre-declared as done. The backlog remains NOT_DONE and is not updated by this preparation.

## Revision 1.0.2: ontology catalog loader removed

On 2026-11-03 the owner deleted the Library's federated ontology catalog (`FEDERATED_ONTOLOGIES.json`) together with its document catalog and manifest; no host calls `specgraph ontology --catalog`, and each repository keeps its ontology in its own tree. By that explicit decision, the AC-05 targets get the legitimate-removal disposition and are deleted with their tests and mapping rows: code `FederatedOntologyView`, `FederatedOntologyView.lookup`, `FederatedOntologyView.to_dict`, `OntologyConcept`, `OntologyConcept.to_dict`, `OntologySource`, `OntologySource.to_dict`, `_git_head`, `_load_concepts`, `_optional_string`, `_required_string`, `_safe_relative_path`, `_string_tuple` and `load_federated_ontologies`, and schema `FederatedOntologyView`, `OntologyConcept` and `OntologySource`, all in `src/specgraph/ontology.py`. AC-05 and F-ONTOLOGY stay as the historical contract and no longer have an implementation. `parse_owner_roots` and `OntologyFederationError` stay in the same module because `ground --read-bodies --owner REPOSITORY=PATH` uses them; their mapping moves to SPEC-SPECGRAPH-C5 AC-02 (the owners' explicit sources). The inventories above remain the historical baseline and are not rewritten.
