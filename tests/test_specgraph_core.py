from __future__ import annotations
import json
import os
import tempfile
import pytest
from datetime import date
from pathlib import Path

from specgraph.config import SpecGraphConfig
from specgraph.spec_parser import SpecParser, SpecNode
from specgraph.code_parser import CodeParser, CodeSymbol
from specgraph.test_parser import TestParser, TestSymbol
from specgraph.schema_parser import SchemaParser, SQLSchemaParser, JSONSchemaParser
from specgraph.api_parser import ApiAndMcpParser, ApiEndpoint, McpToolContract
from specgraph.adr_parser import AdrParser, AdrNode
from specgraph.graph import CausalTraceabilityMatrix, canonical_json
from specgraph.context import ContextPackager
from specgraph.cli import main
from specgraph.search import search_symbols

class TestSpecParser:
    def test_parse_markdown_frontmatter(self, tmp_path: Path):
        spec_file = tmp_path / "SPEC-001_auth.md"
        spec_file.write_text("""---
id: SPEC-001
title: Token Authentication System
status: IMPLEMENTED
implements:
  - REQ-01
  - REQ-02
target_symbols:
  - src/auth.py:generate_token
acceptance_criteria:
  AC-01: Sign payload with Ed25519
  AC-02: Enforce 15m expiration
---
# Detailed Intent
Authentication token service specifications.
""", encoding="utf-8")

        nodes, errs = SpecParser.parse_file(spec_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(nodes) == 1
        n = nodes[0]
        assert n.id == "SPEC-001"
        assert n.title == "Token Authentication System"
        assert n.status == "IMPLEMENTED"
        assert n.implements == ["REQ-01", "REQ-02"]
        assert n.target_symbols == ["src/auth.py:generate_token"]
        assert n.acceptance_criteria["AC-01"] == "Sign payload with Ed25519"
        assert n.acceptance_criteria["AC-02"] == "Enforce 15m expiration"

class TestCodeParser:
    def test_ast_extracts_symbols_and_docstring_tags(self, tmp_path: Path):
        code_file = tmp_path / "auth.py"
        code_file.write_text('''
import hashlib
from datetime import datetime

class TokenService:
    """Handles token operations."""
    
    def generate_token(self, user_id: str) -> str:
        """Issue an authentication token.
        
        @spec SPEC-001 [AC-01, AC-02]
        """
        return f"token_{user_id}"

def verify_token(token: str) -> bool:
    """Verify an existing token.
    
    @spec SPEC-001 [AC-01]
    """
    return bool(token)
''', encoding="utf-8")

        symbols, errs = CodeParser.parse_file(code_file, root_dir=tmp_path)
        assert len(errs) == 0
        sym_names = [s.name for s in symbols]
        assert "TokenService" in sym_names
        assert "TokenService.generate_token" in sym_names
        assert "verify_token" in sym_names

        gen_sym = next(s for s in symbols if s.name == "TokenService.generate_token")
        assert "SPEC-001:AC-01" in gen_sym.linked_specs
        assert "SPEC-001:AC-02" in gen_sym.linked_specs

    def test_structural_symbol_search_is_literal_ranked_and_bounded(self):
        symbols = [
            CodeSymbol("src/auth.py:TokenService", "TokenService", "class", "src/auth.py", 1, 8),
            CodeSymbol("src/auth.py:TokenService.issue", "TokenService.issue", "method", "src/auth.py", 3, 4),
            CodeSymbol("src/issue.py:issue", "issue", "function", "src/issue.py", 1, 2),
        ]

        results = search_symbols(symbols, "issue", limit=2)

        assert [item.symbol_id for item in results] == [
            "src/auth.py:TokenService.issue",
            "src/issue.py:issue",
        ]
        assert all(item.score == 400 for item in results)
        with pytest.raises(ValueError, match="must not be empty"):
            search_symbols(symbols, "  ")
        with pytest.raises(ValueError, match="between 1 and 100"):
            search_symbols(symbols, "issue", limit=101)

    def test_syntax_error_captures_error_without_swallowing(self, tmp_path: Path):
        broken_file = tmp_path / "broken.py"
        broken_file.write_text("def broken_syntax( { ::: invalid", encoding="utf-8")

        symbols, errs = CodeParser.parse_file(broken_file, root_dir=tmp_path)
        assert len(symbols) == 0
        assert len(errs) == 1
        assert "SyntaxError" in errs[0]

class TestTestParser:
    def test_ast_extracts_pytest_verifies_markers(self, tmp_path: Path):
        test_file = tmp_path / "test_auth.py"
        test_file.write_text('''
import pytest

@pytest.mark.verifies("SPEC-001", "AC-01", "AC-02")
def test_generate_token_valid():
    assert True

def test_verify_token_doc():
    """Test token verification logic.
    
    @verifies SPEC-001 [AC-01]
    """
    assert True
''', encoding="utf-8")

        tests, errs = TestParser.parse_file(test_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(tests) == 2
        t1 = next(t for t in tests if t.name == "test_generate_token_valid")
        assert "SPEC-001:AC-01" in t1.verified_specs
        assert "SPEC-001:AC-02" in t1.verified_specs

        t2 = next(t for t in tests if t.name == "test_verify_token_doc")
        assert "SPEC-001:AC-01" in t2.verified_specs

class TestSchemaParser:
    def test_sql_schema_ddl_parsing(self, tmp_path: Path):
        sql_file = tmp_path / "schema.sql"
        sql_file.write_text('''
-- @spec SPEC-001 [AC-01]
CREATE TABLE auth_tokens (
    token_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
''', encoding="utf-8")

        schemas, errs = SQLSchemaParser.parse_file(sql_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(schemas) == 1
        sch = schemas[0]
        assert sch.name == "auth_tokens"
        assert sch.kind == "sql_table"
        assert "SPEC-001:AC-01" in sch.linked_specs
        assert len(sch.fields) == 3

    def test_json_schema_parsing(self, tmp_path: Path):
        json_file = tmp_path / "user_payload.json"
        json_file.write_text('''{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "UserPayload",
  "$spec": "SPEC-001",
  "type": "object",
  "properties": {
    "username": {"type": "string"},
    "role": {"type": "string"}
  },
  "required": ["username"]
}''', encoding="utf-8")

        schemas, errs = JSONSchemaParser.parse_file(json_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(schemas) == 1
        sch = schemas[0]
        assert sch.name == "UserPayload"
        assert sch.kind == "json_schema"
        assert "SPEC-001" in sch.linked_specs
        assert len(sch.fields) == 2

    def test_pydantic_and_dataclass_parsing(self, tmp_path: Path):
        py_file = tmp_path / "models.py"
        py_file.write_text('''
from dataclasses import dataclass
from pydantic import BaseModel

class TokenClaims(BaseModel):
    """Token claims model.
    @spec SPEC-001 [AC-01]
    """
    sub: str
    exp: int

@dataclass
class SessionState:
    """Session state model.
    @spec SPEC-001 [AC-02]
    """
    user_id: str
    is_active: bool = True
''', encoding="utf-8")

        schemas, errs = SchemaParser.parse_python_models(py_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(schemas) == 2
        pyd = next(s for s in schemas if s.name == "TokenClaims")
        assert pyd.kind == "pydantic_model"
        assert "SPEC-001:AC-01" in pyd.linked_specs

        dc = next(s for s in schemas if s.name == "SessionState")
        assert dc.kind == "dataclass"
        assert "SPEC-001:AC-02" in dc.linked_specs

class TestApiAndMcpParser:
    def test_fastapi_and_mcp_routes_parsing(self, tmp_path: Path):
        api_file = tmp_path / "routes.py"
        api_file.write_text('''
from fastapi import APIRouter

router = APIRouter()

@router.post("/v1/auth/tokens")
def issue_token(payload: dict) -> dict:
    """Issue a new session token.
    @spec SPEC-001 [AC-01]
    """
    return {"token": "xyz"}

@mcp.tool()
def authenticate_agent(agent_id: str, secret: str) -> str:
    """Authenticate an autonomous agent tool.
    @spec SPEC-001 [AC-02]
    """
    return "ok"
''', encoding="utf-8")

        eps, tools, errs = ApiAndMcpParser.parse_file(api_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(eps) == 1
        assert len(tools) == 1

        ep = eps[0]
        assert ep.endpoint_id == "api:POST:/v1/auth/tokens"
        assert "SPEC-001:AC-01" in ep.linked_specs

        tool = tools[0]
        assert tool.tool_id == "mcp:authenticate_agent"
        assert tool.parameters == ["agent_id", "secret"]
        assert "SPEC-001:AC-02" in tool.linked_specs

class TestAdrParser:
    def test_adr_parsing_and_supersession(self, tmp_path: Path):
        adr_file = tmp_path / "ADR-046.md"
        adr_file.write_text("""---
id: ADR-046
title: Content Addressed Storage Engine
status: SUPERSEDED
superseded_by: ADR-048
governed_specs:
  - SPEC-001
decision_drivers:
  - Cryptographic verification
  - Zero corruption WAL
---
# ADR-046: Content Addressed Storage
## Context
Replacing relational keys with sha256 digests.
""", encoding="utf-8")

        adrs, errs = AdrParser.parse_file(adr_file, root_dir=tmp_path)
        assert len(errs) == 0
        assert len(adrs) == 1
        a = adrs[0]
        assert a.id == "ADR-046"
        assert a.status == "SUPERSEDED"
        assert a.superseded_by == "ADR-048"
        assert a.governed_specs == ["SPEC-001"]

class TestCausalTraceabilityMatrixAndDrift:
    def _setup_clean_project(self, root: Path) -> None:
        (root / "specs").mkdir(parents=True, exist_ok=True)
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "adr").mkdir(parents=True, exist_ok=True)

        # ADR
        (root / "docs" / "adr" / "ADR-001.md").write_text("""---
id: ADR-001
title: Microkernel Architecture
status: ACCEPTED
governed_specs:
  - SPEC-001
---
# Architecture Record
""", encoding="utf-8")

        # Spec
        (root / "specs" / "SPEC-001.md").write_text("""---
id: SPEC-001
title: Calculator Service
status: ACCEPTED
implements:
  - REQ-CALC
acceptance_criteria:
  AC-01: Sum two numbers
---
# Intent
Basic arithmetic.
""", encoding="utf-8")

        # Schema
        (root / "src" / "schema.sql").write_text('''
-- @spec SPEC-001 [AC-01]
CREATE TABLE calc_history (
    id TEXT PRIMARY KEY,
    result INTEGER NOT NULL
);
''', encoding="utf-8")

        # API & Code
        (root / "src" / "calc.py").write_text('''
from fastapi import APIRouter

router = APIRouter()

@router.post("/calc/add")
def add(a: int, b: int) -> int:
    """Compute sum.
    
    @spec SPEC-001 [AC-01]
    """
    return a + b
''', encoding="utf-8")

        # Test
        (root / "tests" / "test_calc.py").write_text('''
import pytest

@pytest.mark.verifies("SPEC-001", "AC-01")
def test_add():
    assert True
''', encoding="utf-8")

    def test_clean_project_passes_drift_check(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "PASS"
        assert len(drift["violations"]) == 0
        assert drift["stats"]["governed_code_coverage_pct"] == 100.0
        assert drift["stats"]["spec_verification_coverage_pct"] == 100.0
        assert len(drift["generation_id"]) == 64
        assert drift["stats"]["total_adrs"] == 1
        assert drift["stats"]["total_schemas"] == 1
        assert drift["stats"]["total_apis"] == 1

    def test_dangling_spec_reference_fails_drift_check(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "src" / "fake_spec_ref.py").write_text('''
def fake_fn():
    """@spec SPEC-404"""
    return 42
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("references non-existent spec 'SPEC-404'" in v for v in drift["violations"])

    def test_invalid_criterion_reference_fails_drift_check(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "src" / "invalid_crit.py").write_text('''
def crit_fn():
    """@spec SPEC-001 [AC-99]"""
    return 42
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("references non-existent acceptance criterion 'AC-99'" in v for v in drift["violations"])

    def test_superseded_adr_warning_in_drift(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "docs" / "adr" / "ADR-001.md").write_text("""---
id: ADR-001
title: Old Architecture
status: SUPERSEDED
superseded_by: ADR-002
governed_specs:
  - SPEC-001
---
""", encoding="utf-8")
        (tmp_path / "docs" / "adr" / "ADR-002.md").write_text("""---
id: ADR-002
title: New Architecture
status: ACCEPTED
---
""", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("governed by superseded ADR 'ADR-001'" in v for v in drift["violations"])

    def test_orphan_code_fails_drift_check_under_strict(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "src" / "rogue.py").write_text('''
def orphan_function():
    return "undocumented"
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("orphan code symbols" in v for v in drift["violations"])

    def test_syntax_error_in_code_fails_drift_check_closed(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "src" / "broken.py").write_text("def syntax_err( { broken", encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("Parsing Failure" in v for v in drift["violations"])

    def test_unverified_spec_fails_drift_check(self, tmp_path: Path):
        self._setup_clean_project(tmp_path)
        (tmp_path / "specs" / "SPEC-002.md").write_text("""---
id: SPEC-002
title: Unverified Spec
status: ACCEPTED
---
# Intent
Missing test.
""", encoding="utf-8")

        (tmp_path / "src" / "untested.py").write_text('''
def untested_func():
    """@spec SPEC-002"""
    pass
''', encoding="utf-8")

        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        drift = matrix.drift_check()

        assert drift["status"] == "FAIL"
        assert any("without verifying tests" in v for v in drift["violations"])

class TestContextPackagerAndCLI:
    def test_context_envelope_generation(self, tmp_path: Path):
        TestCausalTraceabilityMatrixAndDrift()._setup_clean_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        envelope = ContextPackager.generate_spec_context(matrix, "SPEC-001", root_dir=tmp_path)

        assert "# Context Envelope: [SPEC-001] Calculator Service" in envelope
        assert "Sum two numbers" in envelope
        assert "def add(a: int, b: int) -> int:" in envelope
        assert "def test_add():" in envelope

    def test_cli_execution_commands(self, tmp_path: Path, capsys):
        TestCausalTraceabilityMatrixAndDrift()._setup_clean_project(tmp_path)
        
        # Test CLI trace
        exit_code = main(["--root", str(tmp_path), "trace", "--format", "json"])
        assert exit_code == 0
        captured = capsys.readouterr().out
        assert "SPEC-001" in captured
        assert "ADR-001" in captured

        # Test CLI trace with filter
        exit_code_kind = main(["--root", str(tmp_path), "trace", "--kind", "schemas", "--format", "table"])
        assert exit_code_kind == 0
        captured_kind = capsys.readouterr().out
        assert "calc_history" in captured_kind

        # Test bounded structural symbol search
        exit_code_search = main([
            "--root", str(tmp_path), "search", "add", "--limit", "1", "--format", "json"
        ])
        assert exit_code_search == 0
        captured_search = capsys.readouterr().out
        search_payload = json.loads(captured_search)
        assert search_payload["count"] == 1
        assert search_payload["results"][0]["name"] == "add"

        # Test CLI doctor
        exit_code_doc = main(["--root", str(tmp_path), "doctor", "--deep", "--format", "json"])
        assert exit_code_doc == 0
        captured_doc = capsys.readouterr().out
        assert "PASS" in captured_doc

    def test_json_trace_serializes_yaml_dates_deterministically(self, tmp_path: Path, capsys):
        TestCausalTraceabilityMatrixAndDrift()._setup_clean_project(tmp_path)
        adr = tmp_path / "docs" / "adr" / "ADR-002.md"
        adr.write_text(
            """---
id: ADR-002
title: Dated decision
status: ACCEPTED
date: 2026-09-03
---
# Context
The YAML loader intentionally produces a date scalar.
""",
            encoding="utf-8",
        )

        first = main(["--root", str(tmp_path), "trace", "--format", "json"])
        assert first == 0
        first_payload = capsys.readouterr().out
        second = main(["--root", str(tmp_path), "trace", "--format", "json"])
        assert second == 0
        second_payload = capsys.readouterr().out
        assert first_payload == second_payload
        json.loads(first_payload)
        assert canonical_json({"date": date(2026, 9, 3)}) == '{"date":"2026-09-03"}'


    def test_reviewer_bundle_generation(self, tmp_path: Path):
        TestCausalTraceabilityMatrixAndDrift()._setup_clean_project(tmp_path)
        matrix = CausalTraceabilityMatrix.build(root_dir=tmp_path)
        
        # Bundle by spec
        bundle = ContextPackager.generate_reviewer_bundle(matrix, spec_id="SPEC-001", root_dir=tmp_path)
        assert "# 📦 REVIEWER CONTEXT BUNDLE" in bundle
        assert "Governing Acceptance Criteria (EARS)" in bundle
        assert "AC-01" in bundle
        assert "Relevant Code Implementations (AST Ranges)" in bundle
        assert "def add(a: int, b: int) -> int:" in bundle
        assert "Verifying Test Suites" in bundle
        assert "test_add" in bundle
        assert "frugal_bundle_v1" in bundle

        # Bundle by file
        bundle_files = ContextPackager.generate_reviewer_bundle(matrix, files=["src/calc.py"], root_dir=tmp_path)
        assert "SPEC-001" in bundle_files
        assert "def add" in bundle_files
