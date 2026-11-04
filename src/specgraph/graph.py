from __future__ import annotations
import hashlib
import json
import re
from datetime import date, datetime
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .config import SpecGraphConfig
from .spec_parser import SpecNode, SpecParser
from .code_parser import CodeSymbol, CodeParser
from .test_parser import TestSymbol, TestParser
from .schema_parser import SchemaContract, SchemaParser
from .api_parser import ApiEndpoint, McpToolContract, ApiAndMcpParser
from .adr_parser import AdrNode, AdrParser


from .serialization import canonical_json, json_default

def sha256_digest(v: Any) -> str:
    return hashlib.sha256(canonical_json(v).encode("utf-8")).hexdigest()

@dataclass
class CausalTraceabilityMatrix:
    """The unified causal graph connecting Intent (ADRs/Specs), Contracts (Schemas/APIs/MCP), Code (AST), and Tests."""
    config: SpecGraphConfig
    root_dir: Path = field(default_factory=lambda: Path("."))
    adrs: dict[str, AdrNode] = field(default_factory=dict)
    specs: dict[str, SpecNode] = field(default_factory=dict)
    schemas: dict[str, SchemaContract] = field(default_factory=dict)
    apis: dict[str, ApiEndpoint] = field(default_factory=dict)
    mcp_tools: dict[str, McpToolContract] = field(default_factory=dict)
    symbols: dict[str, CodeSymbol] = field(default_factory=dict)
    tests: dict[str, TestSymbol] = field(default_factory=dict)
    edges: list[dict[str, Any]] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)
    duplicate_warnings: list[str] = field(default_factory=list)
    dangling_spec_refs: list[str] = field(default_factory=list)
    invalid_criteria_refs: list[str] = field(default_factory=list)

    @classmethod
    def check_staleness(cls, root_dir: Path | str = ".") -> Any:
        """Validate source staleness using the IncrementalCacheEngine."""
        from .cache import IncrementalCacheEngine
        return IncrementalCacheEngine.detect_staleness(root_dir=root_dir)

    def sync_cache(self, root_dir: Path | str = ".") -> Any:
        """Persist current matrix into .specgraph/cache.json manifest."""
        from .cache import IncrementalCacheEngine
        return IncrementalCacheEngine.sync_cache(self, root_dir=root_dir)

    @classmethod
    def build(cls, root_dir: Path | str = ".", auto_cache: bool = False) -> CausalTraceabilityMatrix:
        """Scan the project and build the complete in-memory Causal Traceability Matrix with strict error tracking."""
        root = Path(root_dir).resolve()
        cfg = SpecGraphConfig.load(root)

        adrs_list, adr_errs = AdrParser.discover_adrs(cfg.adr_paths, cfg.excluded_paths, root_dir=root)
        specs_list, spec_errs = SpecParser.discover_specs(cfg.spec_paths, cfg.excluded_paths, root_dir=root)
        schemas_list, schema_errs = SchemaParser.discover_schemas(cfg.schema_paths, cfg.excluded_paths, root_dir=root)
        apis_list, mcp_list, api_errs = ApiAndMcpParser.discover_apis_and_tools(cfg.api_paths, cfg.excluded_paths, root_dir=root)
        symbols_list, code_errs = CodeParser.discover_symbols(
            cfg.indexed_paths if cfg.indexed_paths is not None else cfg.governed_paths,
            cfg.excluded_paths, root_dir=root, source_roots=cfg.python_source_roots)
        from .extractors import TreeSitterCodeParser
        extra_symbols, extra_errors = TreeSitterCodeParser.discover_symbols(
            cfg.indexed_paths if cfg.indexed_paths is not None else cfg.governed_paths,
            cfg.excluded_paths, root_dir=root)
        symbols_list.extend(extra_symbols)
        code_errs.extend(extra_errors)
        tests_list, test_errs = TestParser.discover_tests(cfg.test_paths, cfg.excluded_paths, root_dir=root)

        all_errors = list(cfg.config_errors + adr_errs + spec_errs + schema_errs + api_errs + code_errs + test_errs)
        duplicate_warnings: list[str] = []

        # Deduplicate and detect collision
        adrs_dict: dict[str, AdrNode] = {}
        for a in adrs_list:
            if a.id in adrs_dict:
                duplicate_warnings.append(f"Duplicate ADR ID '{a.id}' in '{a.file_path}'")
            adrs_dict[a.id] = a

        specs_dict: dict[str, SpecNode] = {}
        for s in specs_list:
            if s.id in specs_dict:
                duplicate_warnings.append(f"Duplicate Spec ID '{s.id}' in '{s.file_path}' (overwriting '{specs_dict[s.id].file_path}')")
            specs_dict[s.id] = s

        schemas_dict: dict[str, SchemaContract] = {}
        for sch in schemas_list:
            if sch.schema_id in schemas_dict:
                duplicate_warnings.append(f"Duplicate Schema ID '{sch.schema_id}' in '{sch.file_path}'")
            schemas_dict[sch.schema_id] = sch

        apis_dict: dict[str, ApiEndpoint] = {}
        for ep in apis_list:
            if ep.endpoint_id in apis_dict:
                duplicate_warnings.append(f"Duplicate API Endpoint '{ep.endpoint_id}' in '{ep.file_path}:{ep.start_line}'")
            apis_dict[ep.endpoint_id] = ep

        mcp_dict: dict[str, McpToolContract] = {}
        for tool in mcp_list:
            if tool.tool_id in mcp_dict:
                duplicate_warnings.append(f"Duplicate MCP Tool ID '{tool.tool_id}' in '{tool.file_path}:{tool.start_line}'")
            mcp_dict[tool.tool_id] = tool

        symbols_dict: dict[str, CodeSymbol] = {}
        for sym in symbols_list:
            if sym.symbol_id in symbols_dict:
                duplicate_warnings.append(f"Duplicate Symbol ID '{sym.symbol_id}' in '{sym.file_path}:{sym.start_line}'")
            symbols_dict[sym.symbol_id] = sym

        tests_dict: dict[str, TestSymbol] = {}
        for t in tests_list:
            if t.test_id in tests_dict:
                duplicate_warnings.append(f"Duplicate Test ID '{t.test_id}' in '{t.file_path}:{t.start_line}'")
            tests_dict[t.test_id] = t

        matrix = cls(
            config=cfg,
            root_dir=root,
            adrs=adrs_dict,
            specs=specs_dict,
            schemas=schemas_dict,
            apis=apis_dict,
            mcp_tools=mcp_dict,
            symbols=symbols_dict,
            tests=tests_dict,
            parse_errors=all_errors,
            duplicate_warnings=duplicate_warnings
        )

        matrix._assemble_and_validate_edges()
        if auto_cache:
            matrix.sync_cache(root_dir=root)
        return matrix

    def _assemble_and_validate_edges(self, *, mapping_inputs: dict[str, bytes] | None = None) -> None:
        """Derive and validate all causal and static dependency edges against existing specs and acceptance criteria."""
        self.edges.clear()
        self.dangling_spec_refs.clear()
        self.invalid_criteria_refs.clear()
        self.parse_errors = [error for error in self.parse_errors if not error.startswith("Traceability mapping ")]

        # 1. ADR supersessions and Spec governance (SPEC_GOVERNED_BY_ADR, ADR_SUPERSEDES_ADR)
        for adr_id, adr in self.adrs.items():
            if adr.status == "SUPERSEDED" and not adr.superseded_by:
                self.invalid_criteria_refs.append(
                    f"ADR '{adr_id}' is marked SUPERSEDED but does not specify 'superseded_by' successor"
                )

            if adr.superseded_by:
                if adr.superseded_by not in self.adrs:
                    self.dangling_spec_refs.append(
                        f"ADR '{adr_id}' is superseded by non-existent ADR '{adr.superseded_by}'"
                    )
                elif adr.superseded_by == adr_id:
                    self.invalid_criteria_refs.append(
                        f"Detected self-referential ADR supersession loop in '{adr_id}'"
                    )
                else:
                    self.edges.append({
                        "from": adr.superseded_by,
                        "to": adr_id,
                        "type": "ADR_SUPERSEDES_ADR"
                    })

            for spec_ref in adr.governed_specs:
                if spec_ref not in self.specs:
                    self.dangling_spec_refs.append(
                        f"ADR '{adr_id}' governs non-existent spec '{spec_ref}'"
                    )
                else:
                    self.edges.append({
                        "from": spec_ref,
                        "to": adr_id,
                        "type": "SPEC_GOVERNED_BY_ADR"
                    })

        # Detect cyclic ADR supersessions (DFS cycle detector)
        visited: dict[str, int] = {}
        path: list[str] = []

        def dfs_adr(node: str):
            visited[node] = 1
            path.append(node)
            a = self.adrs.get(node)
            if a and a.superseded_by and a.superseded_by in self.adrs:
                nxt = a.superseded_by
                if visited.get(nxt) == 1:
                    cycle_idx = path.index(nxt)
                    cycle_str = " -> ".join(path[cycle_idx:] + [nxt])
                    self.invalid_criteria_refs.append(f"Detected cyclic ADR supersession: {cycle_str}")
                elif visited.get(nxt, 0) == 0:
                    dfs_adr(nxt)
            path.pop()
            visited[node] = 2

        for a_id in self.adrs:
            if visited.get(a_id, 0) == 0:
                dfs_adr(a_id)

        # 2. Spec -> Requirement (SPEC_IMPLEMENTS_REQ) and Spec ID grammar
        for spec_id, spec in self.specs.items():
            if not re.match(r"^SPEC-[A-Za-z0-9_\-]+$", spec_id, re.IGNORECASE):
                self.invalid_criteria_refs.append(
                    f"Specification '{spec_id}' has invalid ID format: must match 'SPEC-<name>'"
                )
            for ac_id in spec.acceptance_criteria:
                if not re.match(r"^AC-[A-Za-z0-9_\-]+$", ac_id, re.IGNORECASE):
                    self.invalid_criteria_refs.append(
                        f"Spec '{spec_id}' has invalid acceptance criterion format '{ac_id}': must match 'AC-<name>'"
                    )
            for req in spec.implements:
                self.edges.append({
                    "from": spec_id,
                    "to": req,
                    "type": "SPEC_IMPLEMENTS_REQ"
                })

        # Helper to validate a spec reference link
        def validate_link(node_kind: str, node_id: str, link: str, edge_type: str) -> None:
            parts = link.split(":")
            if len(parts) > 2 or (len(parts) == 2 and not parts[1].strip()):
                self.invalid_criteria_refs.append(
                    f"{node_kind} '{node_id}' has malformed spec reference '{link}': expected 'SPEC-ID' or 'SPEC-ID:CRITERION-ID'"
                )
                return

            base_spec = parts[0]
            target_crit = parts[1] if len(parts) == 2 else None

            if not re.match(r"^SPEC-[A-Za-z0-9_\-]+$", base_spec, re.IGNORECASE):
                self.invalid_criteria_refs.append(
                    f"{node_kind} '{node_id}' references invalid spec format '{base_spec}': must match 'SPEC-<name>'"
                )
                return

            if target_crit and not re.match(r"^AC-[A-Za-z0-9_\-]+$", target_crit, re.IGNORECASE):
                self.invalid_criteria_refs.append(
                    f"{node_kind} '{node_id}' references invalid criterion format '{target_crit}': must match 'AC-<name>'"
                )
                return

            is_valid = True
            if base_spec not in self.specs:
                self.dangling_spec_refs.append(
                    f"{node_kind} '{node_id}' references non-existent spec '{base_spec}'"
                )
                is_valid = False
            elif target_crit and target_crit not in self.specs[base_spec].acceptance_criteria:
                self.invalid_criteria_refs.append(
                    f"{node_kind} '{node_id}' references non-existent acceptance criterion '{target_crit}' in '{base_spec}'"
                )
                is_valid = False

            if is_valid and base_spec in self.specs:
                self.edges.append({
                    "from": node_id,
                    "to": base_spec,
                    "target_criterion": link if ":" in link else None,
                    "type": edge_type
                })

        # 3. Schemas -> Spec (SCHEMA_IMPLEMENTS_SPEC)
        for sch_id, sch in self.schemas.items():
            for link in sch.linked_specs:
                validate_link("Schema", sch_id, link, "SCHEMA_IMPLEMENTS_SPEC")

        # 4. APIs & MCP Tools -> Spec (API_IMPLEMENTS_SPEC, MCP_TOOL_IMPLEMENTS_SPEC)
        for ep_id, ep in self.apis.items():
            for link in ep.linked_specs:
                validate_link("API Endpoint", ep_id, link, "API_IMPLEMENTS_SPEC")

        for tool_id, tool in self.mcp_tools.items():
            for link in tool.linked_specs:
                validate_link("MCP Tool", tool_id, link, "MCP_TOOL_IMPLEMENTS_SPEC")

        # 5. Code -> Spec (CODE_IMPLEMENTS_SPEC)
        for sym_id, sym in self.symbols.items():
            for link in sym.linked_specs:
                validate_link("Code symbol", sym_id, link, "CODE_IMPLEMENTS_SPEC")

        # From spec target_symbols
        for spec_id, spec in self.specs.items():
            for target_sym in spec.target_symbols:
                matched = [s_id for s_id in self.symbols if s_id == target_sym or s_id.endswith(f":{target_sym}")]
                for s_id in matched:
                    if not any(e["from"] == s_id and e["to"] == spec_id and e["type"] == "CODE_IMPLEMENTS_SPEC" for e in self.edges):
                        self.edges.append({
                            "from": s_id,
                            "to": spec_id,
                            "type": "CODE_IMPLEMENTS_SPEC",
                            "origin": "spec_frontmatter"
                        })

        # 6. Test -> Spec (TEST_VERIFIES_SPEC)
        for test_id, t in self.tests.items():
            for link in t.verified_specs:
                parts = link.split(":")
                if len(parts) > 2 or (len(parts) == 2 and not parts[1].strip()):
                    self.invalid_criteria_refs.append(
                        f"Test '{test_id}' has malformed spec reference '{link}': expected 'SPEC-ID' or 'SPEC-ID:CRITERION-ID'"
                    )
                    continue

                base_spec = parts[0]
                crit = parts[1] if len(parts) == 2 else None

                if not re.match(r"^SPEC-[A-Za-z0-9_\-]+$", base_spec, re.IGNORECASE):
                    self.invalid_criteria_refs.append(
                        f"Test '{test_id}' verifies invalid spec format '{base_spec}': must match 'SPEC-<name>'"
                    )
                    continue

                if crit and not re.match(r"^AC-[A-Za-z0-9_\-]+$", crit, re.IGNORECASE):
                    self.invalid_criteria_refs.append(
                        f"Test '{test_id}' verifies invalid criterion format '{crit}': must match 'AC-<name>'"
                    )
                    continue

                is_valid = True
                if base_spec not in self.specs:
                    self.dangling_spec_refs.append(
                        f"Test '{test_id}' verifies non-existent spec '{base_spec}'"
                    )
                    is_valid = False
                elif crit and crit not in self.specs[base_spec].acceptance_criteria:
                    self.invalid_criteria_refs.append(
                        f"Test '{test_id}' verifies non-existent acceptance criterion '{crit}' in '{base_spec}'"
                    )
                    is_valid = False

                if is_valid and base_spec in self.specs:
                    self.edges.append({
                        "from": test_id,
                        "to": base_spec,
                        "verified_criterion": link if ":" in link else None,
                        "type": "TEST_VERIFIES_SPEC"
                    })

        # 7. Bind full module paths and lexical declarations, retaining unknowns
        # on CodeSymbol.references instead of inventing graph targets.
        from .resolver import resolve_references
        self.edges.extend(resolve_references({key: symbol for key, symbol in self.symbols.items()
                                              if symbol.language == "python"}))
        from .extractors import structural_edges
        self.edges.extend(structural_edges(self.symbols))
        from .traceability import apply_declared_mappings
        declared_edges, mapping_errors = apply_declared_mappings(self, inputs=mapping_inputs)
        self.edges.extend(declared_edges)
        self.parse_errors.extend(mapping_errors)

    def get_orphan_code(self) -> list[CodeSymbol]:
        """Return code symbols in governed paths that are not linked to any valid, existing specification."""
        linked_sym_ids = {e["from"] for e in self.edges if e["type"] == "CODE_IMPLEMENTS_SPEC"}
        governed: list[str] = []
        root = self.root_dir.resolve()
        for configured in self.config.governed_paths:
            try:
                governed.append((root / configured).resolve().relative_to(root).as_posix())
            except ValueError:
                continue  # Invalid scope is reported during discovery.
        return [sym for sym_id, sym in self.symbols.items()
                if sym_id not in linked_sym_ids
                and any(path == "." or sym.file_path == path
                        or sym.file_path.startswith(path + "/") for path in governed)]

    def get_orphan_specs(self) -> list[SpecNode]:
        """Return specs that have neither code implementations nor tests."""
        spec_with_code = {e["to"] for e in self.edges if e["type"] == "CODE_IMPLEMENTS_SPEC"}
        spec_with_tests = {e["to"] for e in self.edges if e["type"] == "TEST_VERIFIES_SPEC"}
        active_specs = spec_with_code | spec_with_tests
        return [s for s_id, s in self.specs.items() if s_id not in active_specs]

    def get_unverified_specs(self) -> list[SpecNode]:
        """Return specs that have code implementations but 0 verifying tests."""
        spec_with_code = {e["to"] for e in self.edges if e["type"] == "CODE_IMPLEMENTS_SPEC"}
        spec_with_tests = {e["to"] for e in self.edges if e["type"] == "TEST_VERIFIES_SPEC"}
        unverified_ids = spec_with_code - spec_with_tests
        return [self.specs[s_id] for s_id in unverified_ids if s_id in self.specs]

    def get_orphan_schemas(self) -> list[SchemaContract]:
        """Return schemas in governed schema paths that are not linked to any specification."""
        linked_schema_ids = {e["from"] for e in self.edges if e["type"] == "SCHEMA_IMPLEMENTS_SPEC"}
        return [sch for sch_id, sch in self.schemas.items() if sch_id not in linked_schema_ids]

    def get_orphan_apis(self) -> list[ApiEndpoint]:
        """Return APIs in governed API paths that are not linked to any specification."""
        linked_api_ids = {e["from"] for e in self.edges if e["type"] == "API_IMPLEMENTS_SPEC"}
        return [ep for ep_id, ep in self.apis.items() if ep_id not in linked_api_ids]

    def get_orphan_mcp_tools(self) -> list[McpToolContract]:
        """Return MCP tools in governed paths that are not linked to any specification."""
        linked_tool_ids = {e["from"] for e in self.edges if e["type"] == "MCP_TOOL_IMPLEMENTS_SPEC"}
        return [tool for tool_id, tool in self.mcp_tools.items() if tool_id not in linked_tool_ids]

    def summary_stats(self) -> dict[str, Any]:
        """Calculate high-level SDD metrics and coverage percentages."""
        total_adrs = len(self.adrs)
        total_specs = len(self.specs)
        total_schemas = len(self.schemas)
        total_apis = len(self.apis)
        total_mcp_tools = len(self.mcp_tools)
        total_symbols = len(self.symbols)
        total_tests = len(self.tests)

        orphan_code = self.get_orphan_code()
        orphan_specs = self.get_orphan_specs()
        orphan_schemas = self.get_orphan_schemas()
        orphan_apis = self.get_orphan_apis()
        orphan_mcp_tools = self.get_orphan_mcp_tools()
        unverified_specs = self.get_unverified_specs()

        spec_with_both = sum(
            1 for s_id in self.specs
            if any(e["to"] == s_id and e["type"] == "CODE_IMPLEMENTS_SPEC" for e in self.edges)
            and any(e["to"] == s_id and e["type"] == "TEST_VERIFIES_SPEC" for e in self.edges)
        )

        governed_code_coverage = (
            round((total_symbols - len(orphan_code)) / max(1, total_symbols) * 100.0, 2)
        )
        spec_verification_coverage = (
            round(spec_with_both / max(1, total_specs) * 100.0, 2)
        )

        return {
            "total_adrs": total_adrs,
            "total_specs": total_specs,
            "total_schemas": total_schemas,
            "total_apis": total_apis,
            "total_mcp_tools": total_mcp_tools,
            "total_governed_symbols": total_symbols,
            "total_tests": total_tests,
            "total_causal_edges": len(self.edges),
            "parse_errors_count": len(self.parse_errors),
            "dangling_refs_count": len(self.dangling_spec_refs),
            "invalid_criteria_count": len(self.invalid_criteria_refs),
            "orphan_code_count": len(orphan_code),
            "orphan_specs_count": len(orphan_specs),
            "orphan_schemas_count": len(orphan_schemas),
            "orphan_apis_count": len(orphan_apis),
            "orphan_mcp_tools_count": len(orphan_mcp_tools),
            "unverified_specs_count": len(unverified_specs),
            "governed_code_coverage_pct": governed_code_coverage,
            "spec_verification_coverage_pct": spec_verification_coverage
        }

    def canonical_digest(self) -> str:
        """Compute the complete content-addressed SHA-256 digest of all governed files and graph structure."""
        from .cache import IncrementalCacheEngine
        discovered, _ = IncrementalCacheEngine._discover_all_project_files(self.config, self.root_dir)

        file_hashes: dict[str, str] = {}
        for rel_path, (full_p, _) in sorted(discovered.items()):
            try:
                if full_p.exists() and full_p.is_file():
                    file_hashes[rel_path] = IncrementalCacheEngine.compute_sha256(full_p)
            except Exception:
                pass

        payload = {
            "file_hashes": sorted([{"file": k, "sha256": v} for k, v in file_hashes.items()], key=lambda x: x["file"]),
            "adrs": sorted([
                {
                    "id": a.id,
                    "title": a.title,
                    "status": a.status,
                    "superseded_by": a.superseded_by,
                    "governed_specs": a.governed_specs,
                    "drivers": a.decision_drivers,
                    "body_hash": hashlib.sha256(a.body.encode("utf-8")).hexdigest(),
                    "file": a.file_path
                }
                for a in self.adrs.values()
            ], key=lambda x: x["id"]),
            "specs": sorted([
                {
                    "id": s.id,
                    "title": s.title,
                    "status": s.status,
                    "implements": s.implements,
                    "target_symbols": s.target_symbols,
                    "criteria": s.acceptance_criteria,
                    "body_hash": hashlib.sha256(s.body.encode("utf-8")).hexdigest(),
                    "file": s.file_path
                }
                for s in self.specs.values()
            ], key=lambda x: x["id"]),
            "schemas": sorted([
                {
                    "id": sch.schema_id,
                    "name": sch.name,
                    "kind": sch.kind,
                    "fields": [{"name": f.name, "type": f.type_name, "req": f.required} for f in sch.fields],
                    "specs": sch.linked_specs,
                    "def_hash": hashlib.sha256(sch.raw_definition.encode("utf-8")).hexdigest(),
                    "file": sch.file_path
                }
                for sch in self.schemas.values()
            ], key=lambda x: x["id"]),
            "apis": sorted([
                {
                    "id": ep.endpoint_id,
                    "name": ep.name,
                    "method": ep.method,
                    "path": ep.path,
                    "specs": ep.linked_specs,
                    "doc_hash": hashlib.sha256((ep.docstring or "").encode("utf-8")).hexdigest(),
                    "file": ep.file_path
                }
                for ep in self.apis.values()
            ], key=lambda x: x["id"]),
            "mcp_tools": sorted([
                {
                    "id": t.tool_id,
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "specs": t.linked_specs,
                    "file": t.file_path
                }
                for t in self.mcp_tools.values()
            ], key=lambda x: x["id"]),
            "symbols": sorted([
                {
                    "id": sym.symbol_id,
                    "name": sym.name,
                    "kind": sym.kind,
                    "lines": [sym.start_line, sym.end_line],
                    "deps": sym.static_dependencies,
                    "references": [asdict(ref) for ref in sym.references],
                    "module": sym.module_name,
                    "binding_status": sym.binding_status,
                    "enclosing_function": sym.enclosing_function,
                    "doc_hash": hashlib.sha256((sym.docstring or "").encode("utf-8")).hexdigest(),
                    "file": sym.file_path,
                    "specs": sym.linked_specs
                }
                for sym in self.symbols.values()
            ], key=lambda x: x["id"]),
            "tests": sorted([
                {
                    "id": t.test_id,
                    "name": t.name,
                    "file": t.file_path,
                    "lines": [t.start_line, t.end_line],
                    "verifies": t.verified_specs
                }
                for t in self.tests.values()
            ], key=lambda x: x["id"]),
            "edges": sorted([
                f"{e['from']}->{e['type']}->{e['to']} (criterion: {e.get('target_criterion') or e.get('verified_criterion')})"
                for e in self.edges
            ])
        }
        return sha256_digest(payload)

    def drift_check(self, strict_override: bool | None = None) -> dict[str, Any]:
        """Perform comprehensive drift detection with strict causal referential integrity across all 5 dimensions."""
        stats = self.summary_stats()
        orphan_code = self.get_orphan_code()
        orphan_specs = self.get_orphan_specs()
        orphan_schemas = self.get_orphan_schemas()
        orphan_apis = self.get_orphan_apis()
        orphan_mcp_tools = self.get_orphan_mcp_tools()
        unverified_specs = self.get_unverified_specs()

        violations = []
        # 1. Parse errors are ALWAYS critical violations
        for err in self.parse_errors:
            violations.append(f"Parsing Failure: {err}")

        # 2. Duplicate collision warnings
        for dup in self.duplicate_warnings:
            violations.append(f"Duplicate Collision: {dup}")

        # 3. Dangling Spec / ADR references
        for dang in self.dangling_spec_refs:
            violations.append(f"Dangling Reference: {dang}")

        # 4. Invalid Criteria references
        for inv_crit in self.invalid_criteria_refs:
            violations.append(f"Invalid Criterion: {inv_crit}")

        # 5. Superseded/Deprecated/Withdrawn/Rejected ADR check: any active spec governed by an inactive ADR is a drift violation
        INACTIVE_ADR_STATUSES = {"SUPERSEDED", "DEPRECATED", "WITHDRAWN", "REJECTED"}
        for adr in self.adrs.values():
            if adr.status in INACTIVE_ADR_STATUSES:
                for spec_id in adr.governed_specs:
                    if spec_id in self.specs and self.specs[spec_id].status not in ("DEPRECATED", "SUPERSEDED"):
                        sup_str = f" (superseded by '{adr.superseded_by}')" if adr.superseded_by else ""
                        violations.append(
                            f"Spec '{spec_id}' is governed by {adr.status.lower()} ADR '{adr.id}'{sup_str}"
                        )

        is_strict = self.config.strict_orphan_check if strict_override is None else strict_override

        if is_strict:
            if orphan_code:
                violations.append(f"Detected {len(orphan_code)} orphan code symbols in governed paths without valid spec binding")
            if orphan_schemas:
                violations.append(f"Detected {len(orphan_schemas)} orphan data schemas in governed paths without valid spec binding")
            if orphan_apis:
                violations.append(f"Detected {len(orphan_apis)} orphan API endpoints in governed paths without valid spec binding")
            if orphan_mcp_tools:
                violations.append(f"Detected {len(orphan_mcp_tools)} orphan MCP agent tools in governed paths without valid spec binding")
            if orphan_specs:
                violations.append(f"Detected {len(orphan_specs)} orphan specifications without verified implementations")

        if unverified_specs:
            violations.append(f"Detected {len(unverified_specs)} specs implemented in code without verifying tests")

        status = "FAIL" if violations else "PASS"

        return {
            "status": status,
            "violations": violations,
            "stats": stats,
            "parse_errors": self.parse_errors,
            "dangling_references": self.dangling_spec_refs,
            "invalid_criteria": self.invalid_criteria_refs,
            "orphan_code": [{"symbol": s.symbol_id, "file": s.file_path, "line": s.start_line} for s in orphan_code],
            "orphan_specs": [{"id": s.id, "title": s.title, "file": s.file_path} for s in orphan_specs],
            "orphan_schemas": [{"id": sch.schema_id, "file": sch.file_path, "kind": sch.kind} for sch in orphan_schemas],
            "orphan_apis": [{"id": ep.endpoint_id, "file": ep.file_path} for ep in orphan_apis],
            "orphan_mcp_tools": [{"id": tool.tool_id, "file": tool.file_path} for tool in orphan_mcp_tools],
            "unverified_specs": [{"id": s.id, "title": s.title, "file": s.file_path} for s in unverified_specs],
            "generation_id": self.canonical_digest()
        }

    def to_dict(self) -> dict[str, Any]:
        """Export matrix as standard dictionary."""
        return {
            "schema_version": "1.0",
            "generation_id": self.canonical_digest(),
            "summary": self.summary_stats(),
            "parse_errors": self.parse_errors,
            "dangling_references": self.dangling_spec_refs,
            "invalid_criteria": self.invalid_criteria_refs,
            "adrs": [asdict(a) for a in self.adrs.values()],
            "specs": [asdict(s) for s in self.specs.values()],
            "schemas": [asdict(sch) for sch in self.schemas.values()],
            "apis": [asdict(ep) for ep in self.apis.values()],
            "mcp_tools": [asdict(tool) for tool in self.mcp_tools.values()],
            "symbols": [asdict(s) for s in self.symbols.values()],
            "tests": [asdict(t) for t in self.tests.values()],
            "edges": self.edges
        }
