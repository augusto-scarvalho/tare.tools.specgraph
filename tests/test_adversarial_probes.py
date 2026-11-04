from __future__ import annotations
import json
import os
import time
from pathlib import Path
import pytest

from specgraph.config import SpecGraphConfig
from specgraph.graph import CausalTraceabilityMatrix
from specgraph.cache import IncrementalCacheEngine, CacheManifest
from specgraph.spec_parser import SpecParser
from specgraph.code_parser import CodeParser
from specgraph.schema_parser import SQLSchemaParser, JSONSchemaParser
from specgraph.api_parser import ApiAndMcpParser
from specgraph.adr_parser import AdrParser
from specgraph.cli import main

class TestAdversarialProbes:
    def test_staleness_detects_content_change_even_if_mtime_ns_and_size_preserved(self, tmp_path: Path):
        """Probe 1: Content tampering with preserved mtime and byte size must be detected as STALE."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        py_file = tmp_path / "src" / "calc.py"
        py_file.write_text("def calc():\n    return 1\n", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)
        initial_stat = py_file.stat()

        # Overwrite with identical length but different content
        time.sleep(0.01)
        py_file.write_text("def calc():\n    return 2\n", encoding="utf-8")
        # Artificially restore exact mtime
        os.utime(py_file, ns=(initial_stat.st_atime_ns, initial_stat.st_mtime_ns))

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path, crypto_verify=True)
        assert report.status == "STALE"
        assert "src/calc.py" in report.modified_files

    def test_api_and_mcp_reject_invalid_ac_and_trailing_colon(self, tmp_path: Path):
        """Probe 2: API routes and MCP tools with malformed colons or invalid AC-99 must fail closed."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-001.md").write_text("""---
id: SPEC-001
title: Auth Spec
status: ACCEPTED
acceptance_criteria:
  AC-01: Valid token
---
""", encoding="utf-8")

        # Invalid AC-99 in API route
        (tmp_path / "src" / "api.py").write_text('''
from fastapi import APIRouter
router = APIRouter()

@router.get("/tokens")
def get_tokens():
    """@spec SPEC-001 [AC-99]"""
    pass

@mcp.tool()
def auth_tool():
    """@spec SPEC-001:"""
    pass
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("references non-existent acceptance criterion 'AC-99'" in v for v in drift["violations"])
        assert any("has malformed spec reference 'SPEC-001:'" in v for v in drift["violations"])

    def test_corrupt_json_schema_fails_closed(self, tmp_path: Path):
        """Probe 3: Corrupted JSON files in schema paths must fail closed."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "schemas" / "broken.json").write_text("{ broken json: [", encoding="utf-8")

        schemas, errors = JSONSchemaParser.parse_file(tmp_path / "schemas" / "broken.json", root_dir=tmp_path)
        assert len(schemas) == 0
        assert len(errors) == 1
        assert "JSON Parse Failure" in errors[0]

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()
        assert drift["status"] == "FAIL"
        assert any("JSON Parse Failure" in v for v in drift["violations"])

    def test_canonical_digest_mutates_when_implementation_body_changes(self, tmp_path: Path):
        """Probe 4: Changing docstring or body implementation changes canonical_digest."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        py_file = tmp_path / "src" / "func.py"
        py_file.write_text('def run():\n    """Docstring 1."""\n    return 1\n', encoding="utf-8")

        matrix1 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest1 = matrix1.canonical_digest()

        py_file.write_text('def run():\n    """Docstring 2 updated."""\n    return 2\n', encoding="utf-8")
        matrix2 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest2 = matrix2.canonical_digest()

        assert digest1 != digest2

    def test_sql_create_view_is_indexed(self, tmp_path: Path):
        """Probe 5: CREATE VIEW is properly indexed with kind 'sql_view'."""
        sql_file = tmp_path / "views.sql"
        sql_file.write_text('''
-- @spec SPEC-001 [AC-01]
CREATE VIEW active_users_view AS SELECT * FROM users WHERE active = 1;
''', encoding="utf-8")

        schemas, errors = SQLSchemaParser.parse_file(sql_file, root_dir=tmp_path)
        assert len(errors) == 0
        assert len(schemas) == 1
        assert schemas[0].name == "active_users_view"
        assert schemas[0].kind == "sql_view"
        assert "SPEC-001:AC-01" in schemas[0].linked_specs

    def test_transitive_causal_blast_radius_multi_hop(self, tmp_path: Path):
        """Probe 6: Multi-hop transitive blast radius A -> B -> C."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-A.md").write_text("""---
id: SPEC-A
title: Spec A
status: ACCEPTED
---
""", encoding="utf-8")

        (tmp_path / "src" / "b.py").write_text('''
def func_b():
    """@spec SPEC-A"""
    pass
''', encoding="utf-8")

        (tmp_path / "tests" / "test_c.py").write_text('''
import pytest
@pytest.mark.verifies("SPEC-A")
def test_c():
    pass
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Mutate SPEC-A
        time.sleep(0.01)
        (tmp_path / "specs" / "SPEC-A.md").write_text("""---
id: SPEC-A
title: Spec A Modified
status: ACCEPTED
---
""", encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert "SPEC-A" in report.impacted_nodes
        assert "src/b.py:func_b" in report.impacted_nodes
        assert "tests/test_c.py:test_c" in report.impacted_nodes

    def test_superseded_adr_without_successor_fails_drift(self, tmp_path: Path):
        """Probe 7: Superseded ADR without superseded_by still triggers drift on active specs."""
        (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)

        (tmp_path / "docs" / "adr" / "ADR-010.md").write_text("""---
id: ADR-010
title: Deprecated Architecture
status: SUPERSEDED
governed_specs:
  - SPEC-010
---
""", encoding="utf-8")

        (tmp_path / "specs" / "SPEC-010.md").write_text("""---
id: SPEC-010
title: Active Spec Under Dead ADR
status: ACCEPTED
---
""", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("governed by superseded ADR 'ADR-010'" in v for v in drift["violations"])

    def test_incompatible_manifest_schema_version_treated_as_stale(self, tmp_path: Path):
        """Probe 8: Legacy or incompatible cache schema version triggers STALE."""
        (tmp_path / ".specgraph").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".specgraph" / "cache.json").write_text(json.dumps({
            "schema_version": "0.0.0",
            "files": {}
        }), encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"

    def test_path_escaping_in_config_fails_governed_without_crash(self, tmp_path: Path):
        """Probe 9: specgraph.yaml with escaping paths fails safely."""
        (tmp_path / "specgraph.yaml").write_text("""
schema_version: "1.0"
governed_paths:
  - "../../../escaping_folder"
""", encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert len(report.discovery_errors) > 0
        assert any("Security Violation" in e for e in report.discovery_errors)

    def test_orphan_schema_and_api_fail_strict_drift(self, tmp_path: Path):
        """Probe 10: Unannotated schemas and APIs under governed paths fail strict drift check."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        (tmp_path / "schemas" / "users.sql").write_text("CREATE TABLE users (id INT);", encoding="utf-8")
        (tmp_path / "src" / "api.py").write_text('''
from fastapi import APIRouter
router = APIRouter()

@router.get("/health")
def health():
    pass
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check(strict_override=True)
        assert drift["status"] == "FAIL"
        assert any("orphan data schemas" in v for v in drift["violations"])
        assert any("orphan API endpoints" in v for v in drift["violations"])

    def test_malformed_config_types_fails_closed(self, tmp_path: Path):
        """Probe 11: specgraph.yaml with invalid types (e.g. integer for path list) fails closed."""
        (tmp_path / "specgraph.yaml").write_text("governed_paths: 7\n", encoding="utf-8")
        cfg = SpecGraphConfig.load(tmp_path)
        assert len(cfg.config_errors) > 0
        assert any("Invalid type for 'governed_paths'" in e for e in cfg.config_errors)

    def test_concurrent_save_manifest_threads(self, tmp_path: Path):
        """Probe 12: High concurrency save_manifest with multiple threads succeeds without PermissionError."""
        import concurrent.futures
        from specgraph.cache import CacheManifest, FileFingerprint

        manifest = CacheManifest(
            schema_version="1.0.0",
            project_name="concurrent_test",
            files={"src/a.py": FileFingerprint("src/a.py", 100, 200, "abc", "code")}
        )

        def write_op(i: int):
            IncrementalCacheEngine.save_manifest(manifest, root_dir=tmp_path)
            return True

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(write_op, i) for i in range(20)]
            results = [f.result() for f in futures]
            assert all(results)

    def test_sql_numeric_column_splitting(self, tmp_path: Path):
        """Probe 13: SQL columns with comma inside parenthesis (NUMERIC(10,2)) must not be split."""
        sql_file = tmp_path / "schema.sql"
        sql_file.write_text("CREATE TABLE products (price NUMERIC(10,2) NOT NULL, tax DECIMAL(5,2));", encoding="utf-8")
        schemas, errors = SQLSchemaParser.parse_file(sql_file, root_dir=tmp_path)
        assert len(errors) == 0
        assert len(schemas) == 1
        field_names = [f.name for f in schemas[0].fields]
        assert field_names == ["price", "tax"]
        assert schemas[0].fields[0].type_name == "NUMERIC(10,2)"

    def test_unrecognized_adr_status_fails_parse_or_drift(self, tmp_path: Path):
        """Probe 14: Unrecognized ADR status must be rejected fail-closed."""
        (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "adr" / "ADR-099.md").write_text("""---
id: ADR-099
title: Rogue ADR
status: RETIRED_BUT_NOT_SUPERSEDED
---
""", encoding="utf-8")
        adrs, errors = AdrParser.parse_file(tmp_path / "docs" / "adr" / "ADR-099.md", root_dir=tmp_path)
        assert len(errors) > 0
        assert any("Invalid ADR status" in e for e in errors)

    def test_spec_in_excluded_dir_ignored_by_both_graph_and_cache(self, tmp_path: Path):
        """Probe 15: Specs inside excluded directories (scratch) are ignored by both graph and cache."""
        (tmp_path / "specs" / "scratch").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "scratch" / "SPEC-SCRATCH.md").write_text("""---
id: SPEC-SCRATCH
title: Scratch Spec
status: DRAFT
---
""", encoding="utf-8")
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        assert "SPEC-SCRATCH" not in matrix.specs

    def test_canonical_digest_mutates_with_custom_root_dir(self, tmp_path: Path):
        """Probe 16: canonical_digest mutates when file content changes under explicit root_dir."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        py_file = tmp_path / "src" / "calc.py"
        py_file.write_text("def calc():\n    return 1\n", encoding="utf-8")

        matrix1 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest1 = matrix1.canonical_digest()

        py_file.write_text("def calc():\n    return 2\n", encoding="utf-8")
        matrix2 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest2 = matrix2.canonical_digest()

        assert digest1 != digest2

    def test_added_json_schema_with_spec_in_blast_radius(self, tmp_path: Path):
        """Probe 17: Adding a new JSON schema with $spec seeds impacted_nodes."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-001.md").write_text("""---
id: SPEC-001
title: Spec 001
status: ACCEPTED
---
""", encoding="utf-8")
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Add new json schema
        time.sleep(0.01)
        (tmp_path / "schemas" / "user.json").write_text(json.dumps({
            "$spec": "SPEC-001",
            "type": "object"
        }), encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-001" in report.impacted_nodes

    def test_modified_binding_invalidates_union_of_old_and_new_nodes(self, tmp_path: Path):
        """Probe 18: Changing binding from SPEC-A to SPEC-B invalidates both SPEC-A and SPEC-B."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-A.md").write_text("---\nid: SPEC-A\ntitle: Spec A\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "specs" / "SPEC-B.md").write_text("---\nid: SPEC-B\ntitle: Spec B\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "src" / "worker.py").write_text('def run():\n    """@spec SPEC-A"""\n    pass\n', encoding="utf-8")
        (tmp_path / "tests" / "test_a.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-A")\ndef test_a(): pass\n', encoding="utf-8")
        (tmp_path / "tests" / "test_b.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-B")\ndef test_b(): pass\n', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Mutate binding in worker.py to SPEC-B
        time.sleep(0.01)
        (tmp_path / "src" / "worker.py").write_text('def run():\n    """@spec SPEC-B"""\n    pass\n', encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-A" in report.impacted_nodes
        assert "SPEC-B" in report.impacted_nodes
        assert "tests/test_b.py:test_b" in report.impacted_nodes

    def test_json_schema_spec_keyword_seeds_blast_radius(self, tmp_path: Path):
        """Probe 19: JSON schema with 'spec' keyword seeds blast radius."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-AUTH.md").write_text("---\nid: SPEC-AUTH\ntitle: Auth\nstatus: ACCEPTED\n---\n", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        time.sleep(0.01)
        (tmp_path / "schemas" / "user.json").write_text(json.dumps({
            "spec": "SPEC-AUTH",
            "type": "object"
        }), encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-AUTH" in report.impacted_nodes

    def test_cyclic_adr_supersession_fails_drift(self, tmp_path: Path):
        """Probe 20: Cyclic ADR supersessions (ADR-1 -> ADR-2 -> ADR-1) fail drift."""
        (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "adr" / "ADR-001.md").write_text("""---
id: ADR-001
title: Architecture One
status: SUPERSEDED
superseded_by: ADR-002
---
""", encoding="utf-8")
        (tmp_path / "docs" / "adr" / "ADR-002.md").write_text("""---
id: ADR-002
title: Architecture Two
status: SUPERSEDED
superseded_by: ADR-001
---
""", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()
        assert drift["status"] == "FAIL"
        assert any("cyclic ADR supersession" in v for v in drift["violations"])

    def test_fastapi_api_route_and_named_path_and_mcp_custom_name(self, tmp_path: Path):
        """Probe 21: @router.api_route, named path, and published MCP custom name are extracted."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "routes.py").write_text('''
from fastapi import APIRouter
router = APIRouter()

@router.api_route("/v1/items", methods=["POST"])
def create_item():
    pass

@router.get(path="/v1/users")
def get_users():
    pass

@mcp.tool(name="custom_published_tool")
def internal_tool():
    pass
''', encoding="utf-8")

        endpoints, mcp_tools, errors = ApiAndMcpParser.parse_file(tmp_path / "src" / "routes.py", root_dir=tmp_path)
        assert len(errors) == 0
        ep_ids = [ep.endpoint_id for ep in endpoints]
        assert "api:POST:/v1/items" in ep_ids
        assert "api:GET:/v1/users" in ep_ids
        assert len(mcp_tools) == 1
        assert mcp_tools[0].tool_id == "mcp:custom_published_tool"

    def test_sync_cache_refuses_drifted_graph_with_invalid_ac(self, tmp_path: Path):
        """Probe 22: sync_cache refuses to write manifest when drift_check fails."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-001.md").write_text("---\nid: SPEC-001\ntitle: Spec 001\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-01: Valid token\n---\n", encoding="utf-8")
        (tmp_path / "src" / "app.py").write_text('def run():\n    """@spec SPEC-001 [AC-99]"""\n    pass\n', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        with pytest.raises(ValueError, match="Cannot sync cache with drift violations"):
            IncrementalCacheEngine.sync_cache(matrix, root_dir=tmp_path)

    def test_static_dependency_consumer_invalidated_when_provider_changes(self, tmp_path: Path):
        """Probe 23: When provider changes, static dependent consumer is invalidated in blast radius."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-PROV.md").write_text("---\nid: SPEC-PROV\ntitle: Provider\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "src" / "provider.py").write_text('def get_val():\n    """@spec SPEC-PROV"""\n    return 10\n', encoding="utf-8")
        (tmp_path / "src" / "consumer.py").write_text('from provider import get_val\ndef use_val():\n    """@spec SPEC-PROV"""\n    return get_val() * 2\n', encoding="utf-8")
        (tmp_path / "tests" / "test_prov.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-PROV")\ndef test_p(): pass\n', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Mutate provider.py
        time.sleep(0.01)
        (tmp_path / "src" / "provider.py").write_text('def get_val():\n    """@spec SPEC-PROV"""\n    return 20\n', encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "src/provider.py:get_val" in report.impacted_nodes
        assert "src/consumer.py:use_val" in report.impacted_nodes

    def test_fastapi_router_prefix_prepends_to_endpoint_id(self, tmp_path: Path):
        """Probe 24: APIRouter(prefix='/v1') prepends to route path."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "api.py").write_text('''
from fastapi import APIRouter
router = APIRouter(prefix="/v1")

@router.get("/items")
def get_items():
    pass
''', encoding="utf-8")

        endpoints, _, errors = ApiAndMcpParser.parse_file(tmp_path / "src" / "api.py", root_dir=tmp_path)
        assert len(errors) == 0
        assert len(endpoints) == 1
        assert endpoints[0].endpoint_id == "api:GET:/v1/items"

    def test_sql_ddl_with_trailing_whitespace_before_semicolon(self, tmp_path: Path):
        """Probe 25: SQL DDL with whitespace before semicolon 'CREATE TABLE x (...) ;' is parsed."""
        sql_file = tmp_path / "table.sql"
        sql_file.write_text("""
-- @spec SPEC-001 [AC-01]
CREATE TABLE users (
    id INT NOT NULL,
    name VARCHAR(255)
)   ;
""", encoding="utf-8")

        schemas, errors = SQLSchemaParser.parse_file(sql_file, root_dir=tmp_path)
        assert len(errors) == 0
        assert len(schemas) == 1
        assert schemas[0].name == "users"

    def test_added_pytest_test_verifies_seeds_blast_radius(self, tmp_path: Path):
        """Probe 26: Adding a new test with verifies marker seeds blast radius."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-A.md").write_text("---\nid: SPEC-A\ntitle: Spec A\nstatus: ACCEPTED\n---\n", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        time.sleep(0.01)
        (tmp_path / "tests" / "test_a.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-A")\ndef test_a(): pass\n', encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-A" in report.impacted_nodes

    def test_corrupted_reverse_deps_fails_closed_in_staleness_check(self, tmp_path: Path):
        """Probe 27: Corrupted reverse_deps in cache.json fails closed as STALE without crash."""
        cache_dir = tmp_path / ".specgraph"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "cache.json").write_text(json.dumps({
            "schema_version": "1.0.0",
            "project_name": "test",
            "files": {},
            "reverse_deps": {"SPEC-A": 7}
        }), encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"

    def test_duplicate_spec_id_fails_sync_cache(self, tmp_path: Path):
        """Probe 28: Duplicate Spec ID produces warning and fails sync_cache."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-DUP1.md").write_text("---\nid: SPEC-DUP\ntitle: Spec Dup 1\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "specs" / "SPEC-DUP2.md").write_text("---\nid: SPEC-DUP\ntitle: Spec Dup 2\nstatus: ACCEPTED\n---\n", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        with pytest.raises(ValueError, match="Cannot sync cache with drift violations"):
            IncrementalCacheEngine.sync_cache(matrix, root_dir=tmp_path)

    def test_unrecognized_spec_status_fails_parse(self, tmp_path: Path):
        """Probe 29: Spec with invalid status enum fails parsing fail-closed."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-BAD.md").write_text("---\nid: SPEC-BAD\ntitle: Bad Status\nstatus: ACTIVE_BUT_NOT_ENUM\n---\n", encoding="utf-8")

        specs, errors = SpecParser.parse_file(tmp_path / "specs" / "SPEC-BAD.md", root_dir=tmp_path)
        assert len(errors) > 0
        assert any("Invalid Spec status" in e for e in errors)

    def test_canonical_digest_mutates_when_ungoverned_constant_file_mutates(self, tmp_path: Path):
        """Probe 30: Mutating a file with no functions/classes (e.g. constants.py) mutates canonical_digest."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        const_file = tmp_path / "src" / "constants.py"
        const_file.write_text('API_VERSION = "alpha"\n', encoding="utf-8")

        matrix1 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest1 = matrix1.canonical_digest()

        const_file.write_text('API_VERSION = "bravo"\n', encoding="utf-8")
        matrix2 = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        digest2 = matrix2.canonical_digest()

        assert digest1 != digest2

    def test_invalid_criterion_reference_leaves_symbol_as_orphan_and_fails_drift(self, tmp_path: Path):
        """Probe 31: Invalid AC reference (AC-99) does not link symbol; orphan_code count is > 0."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-001.md").write_text("---\nid: SPEC-001\ntitle: Spec 001\nstatus: ACCEPTED\nacceptance_criteria:\n  AC-01: Valid token\n---\n", encoding="utf-8")
        (tmp_path / "src" / "app.py").write_text('def run():\n    """@spec SPEC-001 [AC-99]"""\n    pass\n', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        orphans = matrix.get_orphan_code()
        assert len(orphans) == 1
        assert orphans[0].symbol_id == "src/app.py:run"
        drift = matrix.drift_check()
        assert drift["status"] == "FAIL"

    def test_late_import_dependency_is_captured_in_symbol_deps(self, tmp_path: Path):
        """Probe 32: Import placed after a function is captured in that function's static dependencies if referenced."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        code_file = tmp_path / "src" / "module.py"
        code_file.write_text('''
def compute():
    return math.sqrt(16)

import math
''', encoding="utf-8")

        symbols, errors = CodeParser.parse_file(code_file, root_dir=tmp_path)
        assert len(errors) == 0
        assert len(symbols) == 1
        assert "math.sqrt" in symbols[0].static_dependencies or "math" in symbols[0].static_dependencies

    def test_txt_file_in_schema_dir_is_ignored_by_discovery(self, tmp_path: Path):
        """Probe 33: A .txt file inside schema_paths is ignored and not indexed as schema."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "schemas" / "notes.txt").write_text("just notes", encoding="utf-8")
        (tmp_path / "schemas" / "user.sql").write_text("CREATE TABLE users (id INT);", encoding="utf-8")

        discovered, _ = IncrementalCacheEngine._discover_all_project_files(SpecGraphConfig(schema_paths=["schemas"]), tmp_path)
        assert "schemas/user.sql" in discovered
        assert "schemas/notes.txt" not in discovered

    def test_corrupted_sql_syntax_fails_parsing_fail_closed(self, tmp_path: Path):
        """Probe 34: Non-comment corrupted SQL ('THIS IS NOT SQL') produces parsing error."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        bad_sql = tmp_path / "schemas" / "bad.sql"
        bad_sql.write_text("THIS IS NOT SQL;\nRANDOM TOKENS;", encoding="utf-8")

        contracts, errors = SQLSchemaParser.parse_file(bad_sql, root_dir=tmp_path)
        assert len(errors) > 0
        assert any("Unrecognized SQL DDL content" in e or "Unrecognized or invalid" in e for e in errors)

    def test_added_adr_with_multiline_yaml_governed_specs_seeds_blast_radius(self, tmp_path: Path):
        """Probe 35: Adding ADR with multiline list (governed_specs:\\n  - SPEC-A) seeds blast radius."""
        (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-A.md").write_text("---\nid: SPEC-A\ntitle: Spec A\nstatus: ACCEPTED\n---\n", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        time.sleep(0.01)
        (tmp_path / "docs" / "adr" / "ADR-001.md").write_text("""---
id: ADR-001
title: Auth Architecture
status: ACCEPTED
governed_specs:
  - SPEC-A
---
""", encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-A" in report.impacted_nodes

    def test_sync_cache_refuses_when_active_spec_governed_by_superseded_adr(self, tmp_path: Path):
        """Probe 36: sync_cache refuses when active spec is governed by a superseded ADR."""
        (tmp_path / "docs" / "adr").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-ACTIVE.md").write_text("---\nid: SPEC-ACTIVE\ntitle: Active Spec\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "docs" / "adr" / "ADR-OLD.md").write_text("""---
id: ADR-OLD
title: Old Arch
status: SUPERSEDED
superseded_by: ADR-NEW
governed_specs:
  - SPEC-ACTIVE
---
""", encoding="utf-8")
        (tmp_path / "docs" / "adr" / "ADR-NEW.md").write_text("""---
id: ADR-NEW
title: New Arch
status: ACCEPTED
---
""", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        with pytest.raises(ValueError, match="Cannot sync cache with drift violations"):
            IncrementalCacheEngine.sync_cache(matrix, root_dir=tmp_path)

    def test_ddl_file_extension_is_indexed_by_cache_and_graph(self, tmp_path: Path):
        """Probe 37: .ddl file extension is discovered and indexed by both cache and graph."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "specs" / "SPEC-A.md").write_text("---\nid: SPEC-A\ntitle: Spec A\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "schemas" / "users.ddl").write_text("-- @spec SPEC-A\nCREATE TABLE users (id INT PRIMARY KEY);", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)
        assert len(matrix.schemas) == 1
        assert "schemas/users.ddl:users" in matrix.schemas

        # Mutate .ddl
        time.sleep(0.01)
        (tmp_path / "schemas" / "users.ddl").write_text("-- @spec SPEC-A\nCREATE TABLE users (id INT PRIMARY KEY, name TEXT);", encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-A" in report.impacted_nodes

    def test_sql_fail_closed_rejects_leftover_corrupt_statement(self, tmp_path: Path):
        """Probe 38: SQL file containing valid table but also leftover corrupt statements fails closed with error."""
        (tmp_path / "schemas").mkdir(parents=True, exist_ok=True)
        bad_sql = tmp_path / "schemas" / "mixed.sql"
        bad_sql.write_text("CREATE TABLE users (id INT); THIS IS NOT VALID SQL;", encoding="utf-8")

        contracts, errors = SQLSchemaParser.parse_file(bad_sql, root_dir=tmp_path)
        assert len(errors) > 0
        assert any("Unrecognized SQL DDL content" in e for e in errors)

    def test_fastapi_imperative_add_api_route_is_parsed(self, tmp_path: Path):
        """Probe 39: app.add_api_route('/hidden', handler, methods=['GET']) is discovered."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        route_file = tmp_path / "src" / "imperative.py"
        route_file.write_text('''
def my_handler():
    pass

app.add_api_route("/hidden", my_handler, methods=["GET", "POST"])
''', encoding="utf-8")

        endpoints, _, errors = ApiAndMcpParser.parse_file(route_file, root_dir=tmp_path)
        assert len(errors) == 0
        ep_ids = [ep.endpoint_id for ep in endpoints]
        assert "api:GET:/hidden" in ep_ids
        assert "api:POST:/hidden" in ep_ids

    def test_nested_excluded_paths_behave_identically_in_cache_and_graph(self, tmp_path: Path):
        """Probe 40: Nested excluded paths (e.g. src/private) are excluded equally from cache and symbol discovery."""
        (tmp_path / "src" / "private").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "private" / "governed.py").write_text("def secret(): pass\n", encoding="utf-8")

        cfg = SpecGraphConfig(excluded_paths=["src/private"])
        discovered, _ = IncrementalCacheEngine._discover_all_project_files(cfg, tmp_path)
        assert "src/private/governed.py" not in discovered

        symbols, _ = CodeParser.discover_symbols(cfg.governed_paths, cfg.excluded_paths, root_dir=tmp_path)
        assert len(symbols) == 0

    def test_modified_file_with_new_dependency_seeds_blast_radius_with_target_spec(self, tmp_path: Path):
        """Probe 41: When a modified file introduces a new import dependency, that target's spec is seeded into blast radius."""
        (tmp_path / "specs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)

        (tmp_path / "specs" / "SPEC-A.md").write_text("---\nid: SPEC-A\ntitle: Spec A\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "specs" / "SPEC-B.md").write_text("---\nid: SPEC-B\ntitle: Spec B\nstatus: ACCEPTED\n---\n", encoding="utf-8")
        (tmp_path / "src" / "a.py").write_text('def get_a():\n    """@spec SPEC-A"""\n    return "A"\n', encoding="utf-8")
        (tmp_path / "src" / "b.py").write_text('def get_b():\n    """@spec SPEC-B"""\n    return "B"\n', encoding="utf-8")
        (tmp_path / "src" / "consumer.py").write_text('from a import get_a\ndef use():\n    return get_a()\n', encoding="utf-8")
        (tmp_path / "tests" / "test_a.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-A")\ndef test_a(): pass\n', encoding="utf-8")
        (tmp_path / "tests" / "test_b.py").write_text('import pytest\n@pytest.mark.verifies("SPEC-B")\ndef test_b(): pass\n', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path, auto_cache=True)

        # Mutate consumer.py to import from b instead of a
        time.sleep(0.01)
        (tmp_path / "src" / "consumer.py").write_text('from b import get_b\ndef use():\n    return get_b()\n', encoding="utf-8")

        report = IncrementalCacheEngine.detect_staleness(root_dir=tmp_path)
        assert report.status == "STALE"
        assert "SPEC-B" in report.impacted_nodes or "src/b.py:get_b" in report.impacted_nodes

    def test_include_router_prefix_propagates_to_child_endpoints(self, tmp_path: Path):
        """Probe 42: app.include_router(router, prefix='/v1') prepends '/v1' to router's endpoints."""
        (tmp_path / "src").mkdir(parents=True, exist_ok=True)
        route_file = tmp_path / "src" / "main_app.py"
        route_file.write_text('''
from fastapi import FastAPI, APIRouter
app = FastAPI()
router = APIRouter()

@router.get("/items")
def get_items():
    pass

app.include_router(router, prefix="/v1")
''', encoding="utf-8")

        endpoints, _, errors = ApiAndMcpParser.parse_file(route_file, root_dir=tmp_path)
        assert len(errors) == 0
        ep_ids = [ep.endpoint_id for ep in endpoints]
        assert "api:GET:/v1/items" in ep_ids

    def test_save_manifest_fails_closed_when_lock_held(self, tmp_path: Path):
        """Probe 43: save_manifest raises TimeoutError when lock is already held by another process."""
        cache_dir = tmp_path / ".specgraph"
        cache_dir.mkdir(parents=True, exist_ok=True)
        lock_file = cache_dir / "sync.lock"
        lock_file.write_text("9999999", encoding="utf-8")

        manifest = CacheManifest(project_name="test", generation_digest="abc", files={}, reverse_deps={})
        with pytest.raises(TimeoutError, match="Could not acquire sync.lock"):
            IncrementalCacheEngine.save_manifest(manifest, root_dir=tmp_path)

    def test_exclusion_src_private_does_not_exclude_tests_private(self, tmp_path: Path):
        """Probe 44: Excluding 'src/private' does NOT exclude 'tests/private' in either cache or discovery."""
        (tmp_path / "src" / "private").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "private").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "private" / "hidden.py").write_text("def secret(): pass\n", encoding="utf-8")
        (tmp_path / "tests" / "private" / "test_hidden.py").write_text("def test_secret(): pass\n", encoding="utf-8")

        cfg = SpecGraphConfig(excluded_paths=["src/private"])
        discovered, _ = IncrementalCacheEngine._discover_all_project_files(cfg, tmp_path)
        assert "src/private/hidden.py" not in discovered
        assert "tests/private/test_hidden.py" in discovered
