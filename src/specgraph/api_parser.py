from __future__ import annotations
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .identity import python_identities
from .resolver import extract_references

@dataclass
class ApiEndpoint:
    """Represents an HTTP/REST/RPC API endpoint interface."""
    endpoint_id: str  # e.g., "api:GET:/v1/tokens"
    name: str
    method: str  # GET, POST, PUT, DELETE, PATCH
    path: str  # "/v1/tokens"
    handler_symbol: str  # "src/api/routes.py:issue_token"
    file_path: str
    start_line: int = 1
    end_line: int = 1
    linked_specs: list[str] = field(default_factory=list)
    docstring: str | None = None

@dataclass
class McpToolContract:
    """Represents a Model Context Protocol (MCP) tool interface for AI agent governance."""
    tool_id: str  # e.g., "mcp:cas_read_blob"
    name: str
    description: str
    handler_symbol: str
    file_path: str
    start_line: int = 1
    end_line: int = 1
    parameters: list[str] = field(default_factory=list)
    linked_specs: list[str] = field(default_factory=list)

class ASTApiVisitor(ast.NodeVisitor):
    """AST Visitor discovering FastAPI/Flask endpoints and MCP Agent Tool definitions."""

    SPEC_REGEX = re.compile(r"@spec\s+([^\s\n\r\[]+)(?:\s*\[([A-Za-z0-9_\-,\s]+)\])?", re.IGNORECASE)
    HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "route", "api_route"}

    def __init__(self, rel_path: str, tree: ast.AST | None = None):
        self.rel_path = rel_path.replace("\\", "/")
        self.endpoints: list[ApiEndpoint] = []
        self.mcp_tools: list[McpToolContract] = []
        self.class_stack: list[str] = []
        self.identities = python_identities(tree, self.rel_path) if tree else {}
        self.scope_stack: list[str] = []
        self.handler_references = {}
        if tree:
            references = extract_references(tree, self.identities, "", False)
            valid_targets = {identity.symbol_id for identity in self.identities.values()}
            self.handler_references = {
                (ref.line, ref.column): ref.target
                for refs in references.values() for ref in refs
                if ref.status == "pending" and ref.target in valid_targets
            }
        self.router_prefixes: dict[str, str] = {}

        if tree:
            self._collect_prefixes(tree)

    def _collect_prefixes(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                func_name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else "")
                if func_name in ("APIRouter", "Blueprint"):
                    prefix = ""
                    for kw in node.value.keywords:
                        if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            prefix = kw.value.value
                            break
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            self.router_prefixes[target.id] = prefix

        for node in ast.walk(tree):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                if isinstance(call.func, ast.Attribute) and call.func.attr in ("include_router", "register_blueprint"):
                    child_router = ""
                    if call.args and isinstance(call.args[0], ast.Name):
                        child_router = call.args[0].id
                    else:
                        for kw in call.keywords:
                            if kw.arg in ("router", "blueprint") and isinstance(kw.value, ast.Name):
                                child_router = kw.value.id
                                break

                    inc_prefix = ""
                    for kw in call.keywords:
                        if kw.arg in ("prefix", "url_prefix") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            inc_prefix = kw.value.value
                            break

                    if child_router:
                        existing = self.router_prefixes.get(child_router, "")
                        combined = inc_prefix.rstrip("/") + ("/" + existing.lstrip("/") if existing else "")
                        self.router_prefixes[child_router] = combined

    def _extract_spec_links(self, docstring: str | None) -> list[str]:
        if not docstring:
            return []
        links = []
        for match in self.SPEC_REGEX.finditer(docstring):
            raw_spec = match.group(1).strip().upper()
            criteria_str = match.group(2)
            if criteria_str:
                for c in criteria_str.split(","):
                    c_clean = c.strip().upper()
                    if c_clean:
                        links.append(f"{raw_spec}:{c_clean}")
            else:
                links.append(raw_spec)
        return list(dict.fromkeys(links))

    def visit_Expr(self, node: ast.Expr) -> None:
        """Inspect imperative route registrations like app.add_api_route('/hidden', handler, methods=['GET'])."""
        if isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "add_api_route":
                caller_name = call.func.value.id if isinstance(call.func.value, ast.Name) else ""
                route_path = "/"
                if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                    route_path = call.args[0].value
                else:
                    for kw in call.keywords:
                        if kw.arg in ("path", "rule") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            route_path = kw.value.value
                            break

                if caller_name and caller_name in self.router_prefixes:
                    prefix = self.router_prefixes[caller_name].rstrip("/")
                    if prefix:
                        route_path = prefix + ("/" + route_path.lstrip("/") if not route_path.startswith("/") else route_path)

                handler_id = ""
                if len(call.args) >= 2 and isinstance(call.args[1], ast.Name):
                    argument = call.args[1]
                    handler_id = self.handler_references.get((argument.lineno, argument.col_offset), "")
                else:
                    for kw in call.keywords:
                        if kw.arg in ("endpoint", "handler") and isinstance(kw.value, ast.Name):
                            handler_id = self.handler_references.get((kw.value.lineno, kw.value.col_offset), "")
                            break

                methods_list = ["GET"]
                for kw in call.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                        methods_list = [elt.value.upper() for elt in kw.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]

                for m in methods_list:
                    endpoint_id = f"api:{m}:{route_path}"
                    self.endpoints.append(ApiEndpoint(
                        endpoint_id=endpoint_id,
                        name=f"{m} {route_path}",
                        method=m,
                        path=route_path,
                        handler_symbol=handler_id or f"{self.rel_path}:dynamic_handler",
                        file_path=self.rel_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        linked_specs=[]
                    ))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_stack.append(node.name)
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._inspect_function(node)
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._inspect_function(node)
        self.scope_stack.append(node.name)
        self.generic_visit(node)
        self.scope_stack.pop()

    def _inspect_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        doc = ast.get_docstring(node)
        spec_links = self._extract_spec_links(doc)
        identity = self.identities.get(node)
        full_name = ".".join(self.scope_stack + [node.name])
        handler_id = identity.symbol_id if identity else f"{self.rel_path}:{full_name}"

        # Inspect decorators for API routes or MCP tools
        for dec in node.decorator_list:
            # 1. API Route Decorators: @app.get("/path"), @router.post("/items"), @api.route("/login"), @router.api_route("/path")
            if isinstance(dec, ast.Call):
                func = dec.func
                method_name = ""
                caller_name = ""
                if isinstance(func, ast.Attribute) and func.attr.lower() in self.HTTP_METHODS:
                    method_name = func.attr.upper()
                    if isinstance(func.value, ast.Name):
                        caller_name = func.value.id
                elif isinstance(func, ast.Name) and func.id.lower() in self.HTTP_METHODS:
                    method_name = func.id.upper()

                if method_name:
                    route_path = "/"
                    if dec.args and isinstance(dec.args[0], ast.Constant) and isinstance(dec.args[0].value, str):
                        route_path = dec.args[0].value
                    else:
                        for kw in dec.keywords:
                            if kw.arg in ("path", "rule") and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                                route_path = kw.value.value
                                break

                    # Apply router prefix if present
                    if caller_name and caller_name in self.router_prefixes:
                        prefix = self.router_prefixes[caller_name].rstrip("/")
                        if prefix:
                            route_path = prefix + ("/" + route_path.lstrip("/") if not route_path.startswith("/") else route_path)

                    methods_list = [method_name]
                    if method_name in ("ROUTE", "API_ROUTE"):
                        methods_list = ["GET"]
                        for kw in dec.keywords:
                            if kw.arg == "methods":
                                if isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                                    extracted = []
                                    for elt in kw.value.elts:
                                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                            extracted.append(elt.value.upper())
                                    if extracted:
                                        methods_list = extracted

                    for m in methods_list:
                        endpoint_id = f"api:{m}:{route_path}"
                        self.endpoints.append(ApiEndpoint(
                            endpoint_id=endpoint_id,
                            name=node.name,
                            method=m,
                            path=route_path,
                            handler_symbol=handler_id,
                            file_path=self.rel_path,
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            linked_specs=spec_links,
                            docstring=doc
                        ))

                # 2. MCP Tool Decorators: @mcp.tool(), @server.tool(), @agent.tool()
                is_mcp = False
                if isinstance(func, ast.Attribute) and func.attr.lower() in ("tool", "mcp_tool", "agent_tool"):
                    is_mcp = True
                elif isinstance(func, ast.Name) and func.id.lower() in ("tool", "mcp_tool", "agent_tool"):
                    is_mcp = True

                if is_mcp:
                    params = [arg.arg for arg in node.args.args if arg.arg != "self"]
                    tool_name = node.name
                    for kw in dec.keywords:
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                            tool_name = kw.value.value
                            break

                    desc = (doc or "").split("\n\n")[0].strip()
                    self.mcp_tools.append(McpToolContract(
                        tool_id=f"mcp:{tool_name}",
                        name=tool_name,
                        description=desc,
                        handler_symbol=handler_id,
                        file_path=self.rel_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        parameters=params,
                        linked_specs=spec_links
                    ))

            elif isinstance(dec, ast.Attribute) and dec.attr.lower() in ("tool", "mcp_tool"):
                params = [arg.arg for arg in node.args.args if arg.arg != "self"]
                tool_name = node.name
                desc = (doc or "").split("\n\n")[0].strip()
                self.mcp_tools.append(McpToolContract(
                    tool_id=f"mcp:{tool_name}",
                    name=tool_name,
                    description=desc,
                    handler_symbol=handler_id,
                    file_path=self.rel_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    parameters=params,
                    linked_specs=spec_links
                ))

class ApiAndMcpParser:
    """Discovers and extracts HTTP APIs and MCP Tools across governed paths."""

    @classmethod
    def parse_file(
        cls, path: Path | str, root_dir: Path | str = "."
    ) -> tuple[list[ApiEndpoint], list[McpToolContract], list[str]]:
        errors: list[str] = []
        try:
            p = Path(path).resolve()
            root = Path(root_dir).resolve()

            if not p.exists() or p.suffix != ".py":
                return [], [], []

            try:
                rel_path = str(p.relative_to(root)).replace("\\", "/")
            except ValueError:
                return [], [], [f"Security Violation: Path '{p}' escapes project root '{root}'"]
        except Exception as pre_err:
            return [], [], [f"File Resolution Error for API/MCP '{path}': {pre_err}"]

        try:
            code = p.read_text(encoding="utf-8", errors="strict")
            tree = ast.parse(code, filename=str(p))
            visitor = ASTApiVisitor(rel_path, tree=tree)
            visitor.visit(tree)
            return visitor.endpoints, visitor.mcp_tools, []
        except UnicodeDecodeError as u_err:
            errors.append(f"UTF-8 Decoding Error in API/MCP {rel_path}: {u_err}")
            return [], [], errors
        except SyntaxError as syn_err:
            errors.append(f"AST SyntaxError in API/MCP {rel_path}:{syn_err.lineno}: {syn_err.msg}")
            return [], [], errors
        except Exception as exc:
            errors.append(f"AST Parse Failure in API/MCP {rel_path}: {exc}")
            return [], [], errors

    @classmethod
    def discover_apis_and_tools(
        cls,
        search_dirs: list[str],
        excluded_patterns: list[str],
        root_dir: Path | str = "."
    ) -> tuple[list[ApiEndpoint], list[McpToolContract], list[str]]:
        root = Path(root_dir).resolve()
        endpoints: list[ApiEndpoint] = []
        mcp_tools: list[McpToolContract] = []
        errors: list[str] = []

        for sdir in search_dirs:
            target = (root / sdir).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                errors.append(f"Security Violation: Search path '{sdir}' escapes project root '{root}'")
                continue

            if not target.exists():
                continue

            files = [target] if target.is_file() else sorted(target.rglob("*.py"))
            for f in files:
                rel = str(f.relative_to(root)).replace("\\", "/")
                if any(ex in rel.split("/") for ex in excluded_patterns):
                    continue

                eps, tools, errs = cls.parse_file(f, root_dir=root)
                endpoints.extend(eps)
                mcp_tools.extend(tools)
                errors.extend(errs)

        return endpoints, mcp_tools, errors
