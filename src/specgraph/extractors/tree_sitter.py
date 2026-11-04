"""Tree-sitter structural extraction with deliberately narrow binding claims.

    Grammar sources: github.com/tree-sitter/tree-sitter-typescript and
    github.com/tree-sitter/tree-sitter-rust. No regex fallback parses code.
"""
from __future__ import annotations

from collections import Counter
import importlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import re
from typing import Any

from ..code_parser import CodeSymbol
from ..config import SpecGraphConfig
from ..resolver import CodeReference
from .crossfile import bind_imports, exported_function, module_imports

EXTENSIONS = {".ts": "typescript", ".tsx": "typescript", ".rs": "rust"}
PACKAGES = {"typescript": "tree-sitter-typescript", "rust": "tree-sitter-rust"}
CONSTRUCTS = {
    "typescript": ["function", "class", "method", "interface", "type_alias", "enum", "namespace", "named_arrow_function", "tsx_syntax", "call_occurrence", "containment", "declared_crossfile_import"],
    "rust": ["function", "struct", "enum", "trait", "impl", "method", "module", "type_alias", "call_occurrence", "containment", "declared_crossfile_import"],
}
LIMITATIONS = ["Cross-file binding only for relative TS named imports of exported functions and direct Rust child-module pub functions",
               "No reexports, package/type/config resolution, directory-index fallback, or Rust grouped/glob imports",
               "No dynamic receiver or overload dispatch",
               "No macro expansion or conditional compilation evaluation", "References beyond call occurrences are not complete",
               "Only unshadowed same-file top-level functions and the declared cross-file subset are bound"]
_SHADOW_WHOLE = frozenset(("formal_parameters", "parameters", "variable_declarator", "let_declaration",
                           "use_declaration", "closure_parameters", "match_pattern"))
_SHADOW_FIELDS = {"catch_clause": "parameter", "for_in_statement": "left",
                  "for_expression": "pattern", "const_item": "name", "static_item": "name", "let_condition": "pattern"}


def _version(package: str) -> str | None:
    try:
        return version(package)
    except PackageNotFoundError:
        return None


def _parser(language: str, tsx: bool = False) -> Any:
    core = importlib.import_module("tree_sitter")
    grammar = importlib.import_module(PACKAGES[language].replace("-", "_"))
    factory = getattr(grammar, "language_tsx" if tsx else "language_typescript" if language == "typescript" else "language")
    return core.Parser(core.Language(factory()))


def language_capabilities() -> dict[str, Any]:
    result = {}
    for language, package in PACKAGES.items():
        try:
            _parser(language)
            available = True
        except (ImportError, AttributeError, TypeError, ValueError, OSError):
            available = False
        result[language] = {"status": "AVAILABLE" if available else "UNAVAILABLE", "engine": "tree_sitter",
                            "core_version": _version("tree-sitter"), "grammar_package": package,
                            "grammar_version": _version(package), "constructs": CONSTRUCTS[language],
                            "limitations": LIMITATIONS}
    return result


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def _walk_nodes(node: Any):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(current.named_children))


def _shadow_bindings(node: Any, source: bytes) -> set[str]:
    """Collect lexical bindings conservatively, including patterns and imports."""
    if node.type == "use_declaration" and any(part.type == "use_wildcard" for part in _walk_nodes(node)):
        return {"*"}  # An unevaluated local glob can shadow every outer name.
    pattern = node if node.type in _SHADOW_WHOLE else node.child_by_field_name(_SHADOW_FIELDS[node.type]) if node.type in _SHADOW_FIELDS else None
    return {_text(part, source) for part in _walk_nodes(pattern)
            if part.type in ("identifier", "shorthand_property_identifier_pattern", "shorthand_field_identifier")} if pattern is not None else set()


def _declaration(node: Any, source: bytes, language: str) -> tuple[str, str] | None:
    if language == "typescript":
        kinds = {"function_declaration": "function", "generator_function_declaration": "function",
                 "class_declaration": "class", "abstract_class_declaration": "class",
                 "method_definition": "method", "method_signature": "method_signature",
                 "interface_declaration": "interface", "type_alias_declaration": "type_alias",
                 "enum_declaration": "enum", "internal_module": "namespace", "function_signature": "function_signature"}
        if node.type == "variable_declarator":
            value = node.child_by_field_name("value")
            name = node.child_by_field_name("name")
            if value is not None and value.type in ("arrow_function", "function_expression", "generator_function") and name is not None and name.type == "identifier":
                return _text(name, source), "arrow_function" if value.type == "arrow_function" else "function_expression"
    else:
        kinds = {"function_item": "function", "function_signature_item": "function_signature", "struct_item": "struct",
                 "enum_item": "enum", "trait_item": "trait", "mod_item": "module", "type_item": "type_alias"}
        if node.type == "impl_item":
            target = _text(node.child_by_field_name("type"), source)
            trait = _text(node.child_by_field_name("trait"), source)
            return f"impl<{target}{' as ' + trait if trait else ''}>", "implementation"
    kind = kinds.get(node.type)
    name = node.child_by_field_name("name")
    return (_text(name, source), kind) if kind and name is not None else None


def _comment(node: Any, source: bytes) -> str | None:
    candidate = node
    if candidate.parent and candidate.parent.type in ("export_statement", "ambient_declaration", "lexical_declaration"):
        candidate = candidate.parent
    previous = candidate.prev_named_sibling
    if previous is not None and previous.type in ("comment", "line_comment", "block_comment"):
        if previous.end_point.row >= candidate.start_point.row - 1:
            return _text(previous, source)
    return None


def _extract(source: bytes, rel: str, language: str) -> tuple[list[CodeSymbol], list[str]]:
    tree = _parser(language, tsx=rel.endswith(".tsx")).parse(source)
    if tree.root_node.has_error:
        bad = next((node for node in _walk_nodes(tree.root_node) if node.is_error or node.is_missing), tree.root_node)
        return [], [f"Tree-sitter syntax error in {rel}:{bad.start_point.row + 1}:{bad.start_point.column + 1}"]
    imports, import_unsafe = module_imports(tree.root_node, source, rel, language)
    records: list[tuple[Any, CodeSymbol, Any | None]] = []
    declarations: dict[int, tuple[Any, CodeSymbol, Any | None]] = {}
    stack = [(tree.root_node, [], None)]
    while stack:
        node, scopes, parent = stack.pop()
        declaration = _declaration(node, source, language)
        next_scopes, next_parent = scopes, parent
        if declaration:
            name, kind = declaration
            if language == "rust" and kind == "function" and parent is not None and declarations[parent.id][1].kind in ("trait", "implementation"):
                kind = "method"
            qualified = ".".join(scopes + [name])
            comment = _comment(node, source)
            symbol = CodeSymbol(symbol_id=f"{rel}:{qualified}", name=qualified, kind=kind,
                                file_path=rel, start_line=node.start_point.row + 1,
                                end_line=node.end_point.row + (1 if node.end_point.column else 0),
                                docstring=comment, linked_specs=re.findall(r"@spec\s+(SPEC-[A-Za-z0-9_-]+(?::AC-[A-Za-z0-9_-]+)?)", comment or ""),
                                module_name=Path(rel).with_suffix("").as_posix(), language=language,
                                binding_facts={"crossfile_export": exported_function(node, source, language, import_unsafe)})
            record = (node, symbol, parent)
            records.append(record)
            declarations[node.id] = record
            next_scopes, next_parent = scopes + [name], node
        stack.extend((child, next_scopes, next_parent) for child in reversed(node.named_children))
    counts = Counter(symbol.symbol_id for _, symbol, _ in records)
    for node, symbol, _ in records:
        if counts[symbol.symbol_id] > 1:
            symbol.symbol_id += f"@{node.start_point.row + 1}:{node.start_point.column + 1}"
            symbol.binding_status = "ambiguous"
    top_level = {symbol.name: symbol for _, symbol, parent in records
                 if parent is None and symbol.kind == "function" and counts[f"{rel}:{symbol.name}"] == 1}
    # Destructuring/parameter/local bindings conservatively shadow by identifier;
    # this may leave resolvable calls unknown, but does not guess a target.
    for owner_node, symbol, parent in records:
        if parent is not None:
            symbol.container_id = declarations[parent.id][1].symbol_id
        shadowed = set()
        ancestor = owner_node.parent
        global_scope_visible = True
        while ancestor is not None:
            if ancestor.type in ("mod_item", "internal_module", "module", "namespace_declaration"):
                global_scope_visible = False
            if ancestor.id in declarations:
                for binding in _walk_nodes(ancestor):
                    shadowed.update(_shadow_bindings(binding, source))
            ancestor = ancestor.parent
        for node in _walk_nodes(owner_node):
            if node.id != owner_node.id and node.id in declarations:
                nested_name = node.child_by_field_name("name")
                if nested_name is not None:
                    shadowed.add(_text(nested_name, source))
            shadowed.update(_shadow_bindings(node, source))
        pending = [owner_node]
        while pending:
            node = pending.pop()
            if node.id != owner_node.id and node.id in declarations:
                continue
            if node.type == "call_expression":
                callee = node.child_by_field_name("function")
                if callee is not None:
                    name = _text(callee, source)
                    visible = global_scope_visible and callee.type == "identifier" and name not in shadowed and "*" not in shadowed
                    target = top_level.get(name) if visible else None
                    imported = imports.get(name, []) if visible and not target else []
                    reference = CodeReference(name=name, kind="call", line=callee.start_point.row + 1,
                                              column=callee.start_point.column, end_line=callee.end_point.row + 1,
                                              end_column=callee.end_point.column, origin=f"tree_sitter_{language}",
                                              status="resolved" if target else "unresolved",
                                              reason="unshadowed_same_file_function" if target else "declared_import_pending" if len(imported) == 1 else "crossfile_import_ambiguous" if imported else "semantic_resolution_not_supported",
                                              target=target.symbol_id if target else None,
                                              imported_target=imported[0] if len(imported) == 1 else None)
                    symbol.references.append(reference)
            pending.extend(reversed(node.named_children))
        symbol.static_dependencies = sorted({ref.target for ref in symbol.references if ref.target})
    return [symbol for _, symbol, _ in records], []


class TreeSitterCodeParser:
    """@spec SPEC-SPECGRAPH-C6
    """
    @classmethod
    def parse_file(cls, path: Path | str, root_dir: Path | str = ".") -> tuple[list[CodeSymbol], list[str]]:
        root, source_path = Path(root_dir).resolve(), Path(path).resolve()
        try:
            rel = source_path.relative_to(root).as_posix()
        except ValueError:
            return [], [f"Security Violation: source escapes repository: {path}"]
        language = EXTENSIONS.get(source_path.suffix.lower())
        if language is None:
            return [], []
        try:
            source = source_path.read_bytes()
            source.decode("utf-8", errors="strict")
            return _extract(source, rel, language)
        except (ImportError, AttributeError, TypeError, OSError) as exc:
            return [], [f"UNAVAILABLE {language} extractor for {rel}: install wheels with pip install --only-binary=:all: 'tare-tools-specgraph[multilanguage]' ({type(exc).__name__})"]
        except (UnicodeError, ValueError) as exc:
            return [], [f"Tree-sitter extraction failure in {rel}: {exc}"]

    @classmethod
    def discover_symbols(cls, paths: list[str], excluded_paths: list[str], root_dir: Path | str = ".") -> tuple[list[CodeSymbol], list[str]]:
        root, symbols, errors, seen = Path(root_dir).resolve(), [], [], set()
        for configured in paths:
            candidate = (root / configured).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: indexed path escapes repository: {configured}")
                continue
            files = [candidate] if candidate.is_file() else sorted(candidate.rglob("*")) if candidate.is_dir() else []
            for path in files:
                if path.suffix.lower() not in EXTENSIONS or path.resolve() in seen:
                    continue
                rel = path.relative_to(root).as_posix()
                if SpecGraphConfig.is_path_excluded(rel, excluded_paths):
                    continue
                seen.add(path.resolve())
                extracted, diagnostics = cls.parse_file(path, root)
                symbols.extend(extracted)
                errors.extend(diagnostics)
        return symbols, errors


def structural_edges(symbols: dict[str, CodeSymbol]) -> list[dict[str, Any]]:
    bind_imports(symbols)
    edges = []
    for symbol in symbols.values():
        if symbol.language not in PACKAGES:
            continue
        if symbol.container_id and symbol.container_id in symbols:
            edges.append({"from": symbol.container_id, "to": symbol.symbol_id, "type": "CONTAINS",
                          "origin": f"tree_sitter_{symbol.language}", "status": "resolved",
                          "source_file": symbol.file_path, "source_line": symbol.start_line})
        for reference in symbol.references:
            if reference.status == "resolved" and reference.target in symbols:
                edges.append({"from": symbol.symbol_id, "to": reference.target, "type": "DEPENDS_ON_STATIC",
                              "origin": reference.origin, "status": "resolved", "reference_kind": "call",
                              "source_file": symbol.file_path, "source_line": reference.line,
                              "source_column": reference.column, "resolution_reason": reference.reason})
    return sorted(edges, key=lambda edge: (edge["from"], edge["type"], edge["to"], edge["source_line"]))
