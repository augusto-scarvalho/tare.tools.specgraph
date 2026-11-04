"""Deterministic lexical Python identities shared by code and API extraction."""
from __future__ import annotations

import ast
from collections import Counter
from dataclasses import dataclass

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


@dataclass(frozen=True)
class PythonIdentity:
    symbol_id: str
    name: str
    kind: str


def scope_definitions(node: ast.AST) -> list[ast.AST]:
    """Definitions in this lexical scope, including conditional declarations."""
    found: list[ast.AST] = []

    def walk(current: ast.AST) -> None:
        for child in ast.iter_child_nodes(current):
            if isinstance(child, DEFINITIONS):
                found.append(child)
            elif not isinstance(child, (ast.Lambda, ast.ListComp, ast.SetComp,
                                        ast.DictComp, ast.GeneratorExp)):
                walk(child)

    walk(node)
    return found


def python_identities(tree: ast.AST, rel_path: str) -> dict[ast.AST, PythonIdentity]:
    """Preserve unambiguous legacy IDs; qualify scopes in actual lexical order.

    Repeated declarations (including overloads/conditional alternatives) receive
    a source-coordinate suffix. These IDs identify declarations, not runtime
    objects; their discriminators may change when the source moves.
    """
    result: dict[ast.AST, PythonIdentity] = {}

    def collect(scope: ast.AST, prefix: str = "") -> None:
        children = scope_definitions(scope)
        counts = Counter(child.name for child in children)
        for child in children:
            part = child.name
            if counts[part] > 1:
                part += f"@{child.lineno},{child.col_offset}"
            name = f"{prefix}.{part}" if prefix else part
            if isinstance(child, ast.ClassDef):
                kind = "class"
            else:
                kind = "method" if isinstance(scope, ast.ClassDef) else "function"
                if isinstance(child, ast.AsyncFunctionDef):
                    kind = "async_" + kind
            result[child] = PythonIdentity(f"{rel_path}:{name}", name, kind)
            collect(child, name)

    collect(tree)
    return result
