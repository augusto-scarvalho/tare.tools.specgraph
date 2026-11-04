from __future__ import annotations
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass
class TestSymbol:
    """Represents a unit or integration test verifying specific specifications."""
    __test__ = False
    test_id: str  # e.g., "tests/test_auth.py:test_generate_token"
    name: str
    file_path: str
    start_line: int
    end_line: int
    verified_specs: list[str] = field(default_factory=list)  # ["SPEC-042", "SPEC-042:AC-01"]
    docstring: str | None = None

class ASTTestVisitor(ast.NodeVisitor):
    """Extracts test functions and pytest markers (@pytest.mark.verifies(...))."""

    VERIFIES_DOC_REGEX = re.compile(r"@verifies\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)

    def __init__(self, rel_path: str):
        self.rel_path = rel_path.replace("\\", "/")
        self.tests: list[TestSymbol] = []
        self.current_class: str | None = None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        prev = self.current_class
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = prev

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inspect_test(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inspect_test(node)

    def _inspect_test(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.name.startswith("test_") and not node.name.endswith("_test"):
            self.generic_visit(node)
            return

        verified: list[str] = []

        # 1. Parse Decorators: @pytest.mark.verifies("SPEC-042", "AC-01")
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                func = dec.func
                if isinstance(func, ast.Attribute) and func.attr in ("verifies", "spec"):
                    args = [ast.literal_eval(a) for a in dec.args if isinstance(a, ast.Constant)]
                    if args:
                        spec_id = str(args[0]).upper()
                        if len(args) > 1:
                            for ac in args[1:]:
                                verified.append(f"{spec_id}:{str(ac).upper()}")
                        else:
                            verified.append(spec_id)

        # 2. Parse Docstring Tags: @verifies SPEC-042 [AC-01, AC-02]
        doc = ast.get_docstring(node)
        if doc:
            for match in self.VERIFIES_DOC_REGEX.finditer(doc):
                raw_spec = match.group(1).strip().upper()
                criteria = match.group(2)
                if criteria:
                    for c in criteria.split(","):
                        c_clean = c.strip().upper()
                        if c_clean:
                            verified.append(f"{raw_spec}:{c_clean}")
                else:
                    verified.append(raw_spec)

        name = f"{self.current_class}.{node.name}" if self.current_class else node.name
        test_sym = TestSymbol(
            test_id=f"{self.rel_path}:{name}",
            name=name,
            file_path=self.rel_path,
            start_line=node.lineno,
            end_line=node.end_lineno or node.lineno,
            verified_specs=list(dict.fromkeys(verified)),
            docstring=doc
        )
        self.tests.append(test_sym)
        self.generic_visit(node)

class TestParser:
    """Parses test suites to extract verification links with error capture."""

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[TestSymbol], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix != ".py":
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: Test path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for test '{path}': {pre_err}"]

        try:
            code = p.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(code, filename=str(p))
            visitor = ASTTestVisitor(rel_path)
            visitor.visit(tree)
            return visitor.tests, []
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in test {rel_path}: {u_err}")
            return [], errors
        except SyntaxError as syn_err:
            errors.append(f"AST SyntaxError in test {rel_path}:{syn_err.lineno}: {syn_err.msg}")
            return [], errors
        except Exception as exc:
            errors.append(f"AST Parse Failure in test {rel_path}: {exc}")
            return [], errors

    @classmethod
    def discover_tests(
        cls,
        test_dirs: list[str],
        excluded_patterns: list[str],
        root_dir: Path | str = "."
    ) -> tuple[list[TestSymbol], list[str]]:
        root = Path(root_dir).resolve()
        tests: list[TestSymbol] = []
        errors: list[str] = []

        normalized_exclusions = set()
        for pattern in excluded_patterns:
            if not pattern:
                continue
            normalized = pattern.replace("\\", "/").strip("/")
            if normalized:
                normalized_exclusions.add(normalized)

        def is_excluded(rel_path: str) -> bool:
            if not normalized_exclusions:
                return False
            rel_normalized = rel_path.replace("\\", "/").strip("/")
            if not rel_normalized:
                return False
            parts = rel_normalized.split("/")
            for ex in normalized_exclusions:
                ex_parts = ex.split("/")
                if rel_normalized == ex or rel_normalized.startswith(ex + "/"):
                    return True
                if len(ex_parts) == 1 and ex_parts[0] in parts:
                    return True
            return False

        for tdir in test_dirs:
            target = (root / tdir).resolve()

            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: Test dir '{tdir}' escapes project root '{root}'")
                continue

            if not target.exists():
                continue

            files = [target] if target.is_file() else sorted(target.rglob("*.py"))
            for f in files:
                rel = str(f.relative_to(root)).replace("\\", "/")
                if is_excluded(rel):
                    continue
                t_list, errs = cls.parse_file(f, root_dir=root)
                tests.extend(t_list)
                errors.extend(errs)

        return tests, errors
