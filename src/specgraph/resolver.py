"""Conservative Python name binding; never import or execute indexed code.

This resolves lexical declarations and explicit imports. It is not a runtime
call graph: receiver types, re-exports, monkey patches and control flow are not
inferred. Unsupported references remain visible with an explicit reason.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .identity import DEFINITIONS, PythonIdentity, scope_definitions


@dataclass
class CodeReference:
    name: str
    kind: str  # call or reference
    line: int
    column: int
    end_line: int
    end_column: int
    origin: str = "python_ast"
    status: str = "unresolved"
    reason: str = "unbound_name"
    target: str | None = None
    imported_target: str | None = None
    candidates: list[str] = field(default_factory=list)


def python_module_name(rel_path: str, source_roots: list[str] | None = None) -> str:
    path = PurePosixPath(rel_path)
    for source_root in source_roots if source_roots is not None else ["src", "."]:
        root = PurePosixPath(source_root.replace("\\", "/"))
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        parts = list(relative.with_suffix("").parts)
        if parts and parts[-1] == "__init__":
            parts.pop()
        return ".".join(parts)
    return ""  # Outside the declared import roots: do not guess by basename.


@dataclass
class _Binding:
    kind: str
    target: str = ""


@dataclass
class _Scope:
    node: ast.AST
    parent: _Scope | None
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    globals: set[str] = field(default_factory=set)
    nonlocals: set[str] = field(default_factory=set)
    star_import: bool = False

    def add(self, name: str, kind: str, target: str = "") -> None:
        value = _Binding(kind, target)
        values = self.bindings.setdefault(name, [])
        if value not in values:
            values.append(value)

    def lookup(self, name: str) -> list[_Binding]:
        scope: _Scope | None = self
        if name in (self.globals | self.nonlocals) and name in self.bindings:
            return [_Binding("unknown")]
        if name in self.globals:
            while scope.parent:
                scope = scope.parent
        elif name in self.nonlocals:
            scope = self.parent
            while scope and isinstance(scope.node, ast.ClassDef):
                scope = scope.parent
            if scope and isinstance(scope.node, ast.Module):
                return [_Binding("unknown")]
        while scope:
            if name in scope.bindings:
                values = scope.bindings[name]
                return values + ([_Binding("unknown")] if scope.star_import else [])
            if scope.star_import:
                return [_Binding("unknown")]
            scope = scope.parent
            # A class namespace is not a closure for functions or nested classes.
            while scope and isinstance(scope.node, ast.ClassDef):
                scope = scope.parent
        return []


class _Bindings(ast.NodeVisitor):
    def __init__(self, scope: _Scope, identities: dict[ast.AST, PythonIdentity],
                 package: str):
        self.scope, self.identities, self.package = scope, identities, package
        self.conditional_depth = 0

    def add(self, name: str, kind: str, target: str = "") -> None:
        self.scope.add(name, kind, target)
        if self.conditional_depth:
            self.scope.add(name, "unknown")

    def visit_If(self, node: ast.AST) -> None:
        self.conditional_depth += 1
        self.generic_visit(node)
        self.conditional_depth -= 1

    visit_Try = visit_If
    visit_TryStar = visit_If
    visit_For = visit_If
    visit_AsyncFor = visit_If
    visit_While = visit_If
    visit_Match = visit_If

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.add(node.name, "symbol", self.identities[node].symbol_id)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.add(alias.asname or alias.name.split(".")[0], "import",
                           alias.name if alias.asname else alias.name.split(".")[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            package = self.package.split(".") if self.package else []
            if node.level > len(package):
                for alias in node.names:
                    self.add(alias.asname or alias.name, "unknown")
                return
            module = ".".join(package[:len(package) - node.level + 1] +
                              ([module] if module else []))
        for alias in node.names:
            if alias.name == "*":
                self.scope.star_import = True
            else:
                target = f"{module}.{alias.name}" if module else alias.name
                self.add(alias.asname or alias.name, "import", target)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.add(node.id, "unknown")

    def visit_Global(self, node: ast.Global) -> None:
        self.scope.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.scope.nonlocals.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.scope.add(node.name, "unknown")
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.scope.add(node.name, "unknown")
        self.generic_visit(node)

    def visit_MatchStar(self, node: ast.MatchStar) -> None:
        if node.name:
            self.scope.add(node.name, "unknown")

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.scope.add(node.rest, "unknown")
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default:
                self.visit(default)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        # Comprehension targets are local to their implicit scope.
        for child in ast.walk(node):
            if isinstance(child, ast.NamedExpr):
                self.visit(child.target)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted(node.value)
        return f"{prefix}.{node.attr}" if prefix else None
    return None


class _References(ast.NodeVisitor):
    def __init__(self, scope: _Scope):
        self.scope = scope
        self.references: list[CodeReference] = []
        self.shadows: set[str] = set()

    def add(self, node: ast.AST, kind: str) -> None:
        name = _dotted(node)
        if not name:
            return
        ref = CodeReference(name, kind, node.lineno, node.col_offset,
                            node.end_lineno or node.lineno, node.end_col_offset or 0)
        first, _, suffix = name.partition(".")
        bindings = self.scope.lookup(first) if first not in self.shadows else [_Binding("unknown")]
        if len(bindings) > 1:
            ref.status, ref.reason = "ambiguous", "multiple_lexical_bindings"
            ref.candidates = sorted(b.target for b in bindings if b.target)
        elif bindings:
            binding = bindings[0]
            if binding.kind == "symbol":
                ref.target = binding.target + (f".{suffix}" if suffix else "")
                ref.reason = "lexical_declaration"
                ref.status = "pending"
            elif binding.kind == "import":
                ref.imported_target = binding.target + (f".{suffix}" if suffix else "")
                ref.reason, ref.status = "explicit_import", "pending"
            else:
                ref.reason = "dynamic_or_shadowed_binding"
        self.references.append(ref)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.add(node, "reference")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and _dotted(node):
            self.add(node, "reference")
        else:
            self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        if _dotted(node.func):
            self.add(node.func, "call")
        else:
            self.references.append(CodeReference(
                f"<dynamic:{type(node.func).__name__}>", "call", node.func.lineno, node.func.col_offset,
                node.func.end_lineno or node.func.lineno, node.func.end_col_offset or 0,
                reason="dynamic_call_target"))
            self.visit(node.func)
        for value in [*node.args, *(kw.value for kw in node.keywords)]:
            self.visit(value)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # Each definition owns its references, including its header.

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:
        previous = self.shadows.copy()
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default:
                self.visit(default)
        self.shadows.update(arg.arg for arg in [*node.args.posonlyargs, *node.args.args,
                                                *node.args.kwonlyargs])
        for arg in [node.args.vararg, node.args.kwarg]:
            if arg:
                self.shadows.add(arg.arg)
        self.visit(node.body)
        self.shadows = previous

    def visit_ListComp(self, node: ast.ListComp) -> None:
        previous = self.shadows.copy()
        for generator in node.generators:
            self.visit(generator.iter)
            self.shadows.update(n.id for n in ast.walk(generator.target) if isinstance(n, ast.Name))
            for condition in generator.ifs:
                self.visit(condition)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)
        self.shadows = previous

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp


def extract_references(tree: ast.AST, identities: dict[ast.AST, PythonIdentity],
                       module_name: str, is_package: bool,
                       binding_statuses: dict[ast.AST, str] | None = None) -> dict[ast.AST, list[CodeReference]]:
    package = module_name if is_package else module_name.rpartition(".")[0]
    result: dict[ast.AST, list[CodeReference]] = {}

    def collect(node: ast.AST, parent: _Scope | None) -> None:
        scope = _Scope(node, parent)
        binder = _Bindings(scope, identities, package)
        for statement in node.body:
            binder.visit(statement)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs,
                        node.args.vararg, node.args.kwarg]:
                if arg:
                    scope.add(arg.arg, "unknown")
        body = _References(scope)
        for statement in node.body:
            body.visit(statement)
        result[node] = body.references
        if parent:
            header = _References(parent)
            for decorator in node.decorator_list:
                header.visit(decorator)
            if isinstance(node, ast.ClassDef):
                for value in [*node.bases, *(kw.value for kw in node.keywords)]:
                    header.visit(value)
            else:
                for value in [*node.args.defaults, *node.args.kw_defaults, node.returns,
                              *(arg.annotation for arg in [*node.args.posonlyargs, *node.args.args,
                                                           *node.args.kwonlyargs])]:
                    if value:
                        header.visit(value)
            result[node] = sorted(header.references + body.references,
                                  key=lambda r: (r.line, r.column, r.kind))
        for child in scope_definitions(node):
            if binding_statuses is not None:
                bindings = scope.lookup(child.name)
                binding_statuses[child] = ("resolved" if len(bindings) == 1
                                          and bindings[0].target == identities[child].symbol_id
                                          else "ambiguous")
            collect(child, scope)

    collect(tree, None)
    return result


def resolve_references(symbols: dict[str, Any]) -> list[dict[str, Any]]:
    """Bind extracted references using complete module paths in the indexed set."""
    qualified: dict[str, list[str]] = {}
    modules: set[str] = set()
    for symbol in symbols.values():
        if symbol.module_name:
            modules.add(symbol.module_name)
            # Nested definitions are not importable module attributes.
            if not symbol.enclosing_function:
                lookup_name = re.sub(r"@\d+,\d+", "", symbol.name)
                qualified.setdefault(f"{symbol.module_name}.{lookup_name}", []).append(symbol.symbol_id)
    internal_top = {m.split(".")[0] for m in modules}
    edges: list[dict[str, Any]] = []
    for symbol in symbols.values():
        references = symbol.references
        # Backward-compatible programmatic CodeSymbol construction, with exact
        # IDs/full module names only. Never restore the basename heuristic.
        if not references and symbol.static_dependencies:
            references = [CodeReference(dep, "reference", symbol.start_line, 0,
                           symbol.start_line, 0, origin="legacy_static_dependency",
                           imported_target=None if dep in symbols else dep,
                           target=dep if dep in symbols else None, status="pending")
                          for dep in symbol.static_dependencies]
            symbol.references = references
        for ref in references:
            # Rebinding must work after files are added/deleted, without reparse.
            if ref.imported_target:
                candidates = qualified.get(ref.imported_target, [])
                ref.target = candidates[0] if len(candidates) == 1 else None
                ref.candidates = sorted(candidates)
                if len(candidates) == 1:
                    if symbols[candidates[0]].binding_status == "ambiguous":
                        ref.status, ref.reason = "ambiguous", "conditional_or_rebound_declaration"
                    else:
                        ref.status, ref.reason = "resolved", "explicit_import"
                elif candidates:
                    ref.status, ref.reason = "ambiguous", "multiple_module_declarations"
                else:
                    top = ref.imported_target.split(".")[0]
                    if top in sys.stdlib_module_names and top not in internal_top:
                        ref.status, ref.reason = "external", "stdlib_outside_index"
                    else:
                        ref.status, ref.reason = "unresolved", "import_outside_index_or_unsupported_export"
            elif ref.target:
                ref.status = "resolved" if ref.target in symbols else "unresolved"
                ref.reason = "lexical_declaration" if ref.target in symbols else "declaration_outside_index"
                if ref.target in symbols and symbols[ref.target].binding_status == "ambiguous":
                    ref.status, ref.reason = "ambiguous", "conditional_or_rebound_declaration"
            if ref.status == "resolved" and ref.target in symbols:
                edges.append({"from": symbol.symbol_id, "to": ref.target,
                              "type": "DEPENDS_ON_STATIC", "origin": ref.origin,
                              "status": ref.status, "reference_kind": ref.kind,
                              "source_line": ref.line, "source_column": ref.column})
        symbol.static_dependencies = sorted({r.target if r.status == "resolved" else r.imported_target
                                             for r in references if r.target or r.imported_target}
                                            - {None})
    return edges
