"""Narrow, source-free binding of explicitly extracted TS/Rust import facts.

@spec SPEC-SPECGRAPH-CROSSFILE-ALIASES
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
import posixpath
from typing import Any


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8") if node is not None else ""


def _walk(node: Any):
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _relative_target(rel: str, module: str) -> str | None:
    if not module.startswith(("./", "../")) or "\\" in module:
        return None
    path = PurePosixPath(posixpath.normpath(posixpath.join(posixpath.dirname(rel), module)))
    if path.is_absolute() or ".." in path.parts:
        return None
    if not path.suffix:
        path = path.with_suffix(".ts")
    return path.as_posix() if path.suffix in (".ts", ".tsx") else None


def _import_names(node: Any, source: bytes, language: str) -> list[str]:
    """Count declared bindings even when their target syntax is unsupported."""
    names = []
    pending = [node]
    while pending:
        current = pending.pop()
        name = None
        if language == "typescript":
            if current.type == "import_specifier":
                name = current.child_by_field_name("alias") or current.child_by_field_name("name")
            elif current.type == "identifier" and current.parent.type in ("import_clause", "namespace_import"):
                name = current
        else:
            if current.type == "use_as_clause":
                name = current.child_by_field_name("alias")
            elif current.type == "scoped_identifier":
                name = current.child_by_field_name("name")
            elif current.type == "identifier" and current.parent.type in ("use_declaration", "use_list"):
                name = current
        if name is not None:
            names.append(_text(name, source))
        else:
            pending.extend(reversed(current.named_children))
    return names


def module_imports(root: Any, source: bytes, rel: str, language: str
                   ) -> tuple[dict[str, list[str]], bool]:
    """Extract direct module declarations; no file access or target guessing."""
    imports: dict[str, list[str]] = defaultdict(list)
    unsafe = language == "rust" and any(n.type in ("attribute_item", "inner_attribute_item", "macro_invocation") for n in _walk(root))
    if unsafe:
        return {}, True
    if language == "typescript":
        for declaration in root.named_children:
            if declaration.type != "import_statement" or any(c.type == "type" for c in declaration.children):
                continue
            source_node = declaration.child_by_field_name("source")
            if source_node is None or any(c.type == "escape_sequence" for c in _walk(source_node)):
                continue
            module = _text(source_node, source)[1:-1]
            target = _relative_target(rel, module)
            if target is None:
                continue
            for specifier in _walk(declaration):
                if specifier.type != "import_specifier" or any(c.type == "type" for c in specifier.children):
                    continue
                name = specifier.child_by_field_name("name")
                alias = specifier.child_by_field_name("alias") or name
                if name is not None and name.type == "identifier" and alias.type == "identifier":
                    imports[_text(alias, source)].append(target + ":" + _text(name, source))
    else:
        modules = defaultdict(list)
        base = PurePosixPath(rel).parent
        if PurePosixPath(rel).name not in ("main.rs", "lib.rs", "mod.rs"):
            base /= PurePosixPath(rel).stem
        for declaration in root.named_children:
            if declaration.type == "mod_item" and declaration.child_by_field_name("body") is None:
                name = _text(declaration.child_by_field_name("name"), source)
                modules[name].append((base / (name + ".rs")).as_posix())
        for declaration in root.named_children:
            if declaration.type != "use_declaration" or any(c.type == "visibility_modifier" for c in declaration.named_children):
                continue
            path = declaration.child_by_field_name("argument")
            alias = None
            if path is not None and path.type == "use_as_clause":
                alias = path.child_by_field_name("alias")
                path = path.child_by_field_name("path")
            if path is None or path.type != "scoped_identifier":
                continue
            module, name = path.child_by_field_name("path"), path.child_by_field_name("name")
            if module is None or module.type != "identifier" or name is None or name.type != "identifier":
                continue
            for target in modules.get(_text(module, source), []):
                imports[_text(alias or name, source)].append(target + ":" + _text(name, source))
    declarations = defaultdict(int)
    for declaration in root.named_children:
        if declaration.type == ("import_statement" if language == "typescript" else "use_declaration"):
            for name in _import_names(declaration, source, language):
                declarations[name] += 1
    for name, count in declarations.items():
        if count > 1:
            imports.pop(name, None)
    # A module-level variable can replace an imported binding. Scan binding
    # patterns, never initializer references, and stop at nested lexical scopes.
    pending = list(root.named_children)
    while pending:
        node = pending.pop()
        if node.type in ("function_declaration", "function_item", "class_declaration", "internal_module", "mod_item"):
            continue
        if node.type in ("variable_declarator", "let_declaration", "const_item", "static_item"):
            pattern = node.child_by_field_name("name") or node.child_by_field_name("pattern")
            if pattern is not None:
                for part in _walk(pattern):
                    imports.pop(_text(part, source), None)
        else:
            pending.extend(node.named_children)
    return dict(imports), False


def exported_function(node: Any, source: bytes, language: str, unsafe: bool) -> bool:
    """Export is witnessed by syntax, never inferred from a matching name."""
    if unsafe:
        return False
    if language == "typescript":
        parent = node.parent
        return bool(node.type == "function_declaration" and parent is not None
                    and parent.type == "export_statement" and parent.parent.type == "program"
                    and not any(c.type in ("default", "declare") for c in parent.children))
    return bool(node.type == "function_item" and node.parent.type == "source_file"
                and any(c.type == "visibility_modifier" and _text(c, source) == "pub"
                        for c in node.named_children))


def bind_imports(symbols: dict[str, Any]) -> None:
    """Rebind every imported call against current cached declarations."""
    exported = defaultdict(list)
    for symbol in symbols.values():
        if (symbol.language in ("typescript", "rust") and symbol.kind == "function"
                and symbol.container_id is None and symbol.binding_status == "resolved"
                and symbol.binding_facts.get("crossfile_export")):
            exported[(symbol.language, symbol.file_path + ":" + symbol.name)].append(symbol.symbol_id)
    for symbol in symbols.values():
        if symbol.language not in ("typescript", "rust"):
            continue
        for reference in symbol.references:
            if not reference.imported_target:
                continue
            reference.target = None
            reference.status = "unresolved"
            reference.candidates = []
            targets = exported.get((symbol.language, reference.imported_target), [])
            if len(targets) == 1:
                reference.target = targets[0]
                reference.status = "resolved"
                reference.reason = "declared_crossfile_import"
            else:
                reference.reason = "crossfile_target_ambiguous" if targets else "crossfile_target_not_exported_or_indexed"
        symbol.static_dependencies = sorted({r.target for r in symbol.references if r.target})
