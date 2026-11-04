from __future__ import annotations
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import python_identities
from .resolver import CodeReference, extract_references, python_module_name, resolve_references

@dataclass
class CodeSymbol:
    """Represents an AST-level symbol (function, method, class) in governed paths."""
    symbol_id: str  # e.g., "src/auth.py:TokenService.generate_jwt"
    name: str
    kind: str  # "function", "async_function", "class", "method", "async_method"
    file_path: str
    start_line: int
    end_line: int
    docstring: str | None = None
    linked_specs: list[str] = field(default_factory=list)  # ["SPEC-042", "SPEC-042:AC-01"]
    static_dependencies: list[str] = field(default_factory=list)  # Used imported targets / symbol IDs
    references: list[CodeReference] = field(default_factory=list)
    module_name: str = ""
    enclosing_function: bool = False
    binding_status: str = "resolved"
    language: str = "python"
    container_id: str | None = None
    binding_facts: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # JSON/store round-trips retain typed references without a custom codec.
        self.references = [CodeReference(**r) if isinstance(r, dict) else r for r in self.references]

class ASTCodeVisitor(ast.NodeVisitor):
    """Visits Python AST nodes to extract symbol definitions, nested hierarchies, docstrings, and spec links."""

    SPEC_DOCSTRING_REGEX = re.compile(r"@spec\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)

    def __init__(self, rel_path: str, source_code: str, tree: ast.AST | None = None,
                 source_roots: list[str] | None = None):
        self.rel_path = rel_path.replace("\\", "/")
        self.source_code = source_code
        self.symbols: list[CodeSymbol] = []
        self.tree = tree or ast.parse(source_code)
        self.identities = python_identities(self.tree, self.rel_path)
        self.module_name = python_module_name(self.rel_path, source_roots)
        self.binding_statuses: dict[ast.AST, str] = {}
        self.references = extract_references(self.tree, self.identities, self.module_name,
                                             Path(self.rel_path).name == "__init__.py",
                                             self.binding_statuses)
        self.function_depth = 0

    def _extract_spec_links(self, docstring: str | None) -> list[str]:
        if not docstring:
            return []
        links = []
        for match in self.SPEC_DOCSTRING_REGEX.finditer(docstring):
            raw_spec = match.group(1).strip().upper()
            criteria_str = match.group(2)
            if criteria_str:
                for c in criteria_str.split(","):
                    c_clean = c.strip().upper()
                    if c_clean:
                        links.append(f"{raw_spec}:{c_clean}")
            else:
                links.append(raw_spec)
        return list(dict.fromkeys(links))  # preserve unique

    def _add_symbol(self, node: ast.AST) -> None:
        identity = self.identities[node]
        doc = ast.get_docstring(node)
        references = self.references[node]
        self.symbols.append(CodeSymbol(
            symbol_id=identity.symbol_id, name=identity.name, kind=identity.kind,
            file_path=self.rel_path, start_line=node.lineno,
            end_line=node.end_lineno or node.lineno, docstring=doc,
            linked_specs=self._extract_spec_links(doc), references=references,
            module_name=self.module_name, enclosing_function=bool(self.function_depth),
            binding_status=self.binding_statuses.get(node, "resolved"),
            static_dependencies=sorted({r.imported_target or r.target for r in references
                                        if r.imported_target or r.target}),
        ))

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._add_symbol(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._add_symbol(node)
        self.function_depth += 1
        self.generic_visit(node)
        self.function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

class CodeParser:
    """Parses codebases in governed paths using Python AST with fail-closed error capture."""

    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".",
                   source_roots: list[str] | None = None) -> tuple[list[CodeSymbol], list[str]]:
        """@spec SPEC-SPECGRAPH-C1
        """
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix != ".py":
                return [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [f"Security Violation: Path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [f"File Resolution Error for code '{path}': {pre_err}"]

        try:
            code = p.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(code, filename=str(p))
            visitor = ASTCodeVisitor(rel_path, code, tree=tree, source_roots=source_roots)
            visitor.visit(tree)
            resolve_references({s.symbol_id: s for s in visitor.symbols})
            return visitor.symbols, []
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in code {rel_path}: {u_err}")
            return [], errors
        except SyntaxError as syn_err:
            errors.append(f"AST SyntaxError in {rel_path}:{syn_err.lineno}: {syn_err.msg}")
            return [], errors
        except Exception as exc:
            errors.append(f"AST Parse Failure in {rel_path}: {exc}")
            return [], errors

    @classmethod
    def discover_symbols(
        cls,
        governed_dirs: list[str],
        excluded_patterns: list[str],
        root_dir: Path | str = ".",
        source_roots: list[str] | None = None
    ) -> tuple[list[CodeSymbol], list[str]]:
        root = Path(root_dir).resolve()
        symbols: list[CodeSymbol] = []
        errors: list[str] = []

        seen: set[Path] = set()
        for gdir in governed_dirs:
            target = (root / gdir).resolve()
            
            # Anti-Path Traversal Check
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: Governed path '{gdir}' escapes project root '{root}'")
                continue

            if not target.exists():
                continue

            files = [target] if target.is_file() else sorted(target.rglob("*.py"))
            for f in files:
                if f.resolve() in seen:
                    continue
                seen.add(f.resolve())
                rel = str(f.relative_to(root)).replace("\\", "/")
                from .config import SpecGraphConfig
                if SpecGraphConfig.is_path_excluded(rel, excluded_patterns):
                    continue
                syms, errs = cls.parse_file(f, root_dir=root, source_roots=source_roots)
                symbols.extend(syms)
                errors.extend(errs)

        return symbols, errors
