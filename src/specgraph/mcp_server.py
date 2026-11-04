"""Bounded read-only MCP stdio server for one explicitly selected repository.

Implements the tools/lifecycle subset of MCP 2025-06-18, without an SDK:
https://modelcontextprotocol.io/specification/2025-06-18/basic/transports
https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle
https://modelcontextprotocol.io/specification/2025-06-18/server/tools

Each tool call owns a fresh read transaction, never an index writer. JSON is
returned once as TextContent, avoiding duplicated context in structuredContent.
HTTP, sampling, resources, subscriptions and background indexing are not exposed.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, BinaryIO

from .storage import IndexUnavailableError, RepositoryIndex

PROTOCOL_VERSION = "2025-06-18"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 65_536
MODES = ("search", "symbol", "refs", "neighbors", "path", "explain", "context")


class ProtocolError(ValueError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _object(value: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ProtocolError(-32602, "Invalid parameters or unknown fields")
    return value


def _integer(value: Any, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise ProtocolError(-32602, f"{name} must be an integer between {lower} and {upper}")
    return value


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(-32602, f"{name} must be a nonempty string")
    try:
        length = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise ProtocolError(-32602, f"{name} must be valid UTF-8") from exc
    if length > maximum:
        raise ProtocolError(-32602, f"{name} exceeds {maximum} UTF-8 bytes")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON number")


class MCPServer:
    """Sequential session; the root cannot be changed through protocol input."""

    def __init__(self, root: Path | str, *, state_dir: Path | str | None = None,
                 max_response_bytes: int = MAX_RESPONSE_BYTES):
        self.root = Path(root).resolve()
        self.state_dir = state_dir
        self.max_response_bytes = _integer(max_response_bytes, "max_response_bytes", 4096, 1_048_576)
        # TextContent may double JSON escape bytes; reserve the wrapper and ID.
        self.max_tool_bytes = min(32_768, (self.max_response_bytes - 1024) // 2)
        self.initialized = False
        self.ready = False

    def tools(self) -> list[dict[str, Any]]:
        bounds = {
            "query": {"type": "string", "minLength": 1, "maxLength": 512,
                      "description": "Literal symbol/path/query, at most 512 UTF-8 bytes."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            "max_bytes": {"type": "integer", "minimum": 1024, "maximum": self.max_tool_bytes,
                          "default": min(8192, self.max_tool_bytes)},
        }
        query_properties = bounds | {
            "mode": {"type": "string", "enum": list(MODES), "default": "search"},
            "target": {"type": "string", "maxLength": 512},
            "max_nodes": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100},
            "max_hops": {"type": "integer", "minimum": 0, "maximum": 10, "default": 3},
            "direction": {"type": "string", "enum": ["in", "out", "both"]},
            "relation": {"type": "string", "minLength": 1, "maxLength": 128},
        }
        definitions = [
            ("query_causal_graph", "Query the published repository snapshot. Coverage limits and snapshot identity accompany results.", query_properties, ["query"]),
            ("get_context_envelope", "Bounded persisted symbol context. Source bodies and current worktree freshness are not implied.", bounds, ["query"]),
            ("check_drift", "Read governance observed at publication; does not inspect the current worktree or rebuild the index.", {"max_bytes": bounds["max_bytes"]}, []),
        ]
        return [{"name": name, "description": description,
                 "inputSchema": {"type": "object", "properties": properties,
                                 "required": required, "additionalProperties": False},
                 "annotations": {"readOnlyHint": True, "destructiveHint": False,
                                 "idempotentHint": True, "openWorldHint": False}}
                for name, description, properties, required in definitions]

    def _drift(self, max_bytes: int) -> dict[str, Any]:
        with RepositoryIndex(self.root, state_dir=self.state_dir) as index:
            metadata = index.metadata()
            report = {
                "schema": "tare.tools/specgraph-drift-observation/1", "status": "OBSERVED_SNAPSHOT",
                "revision_seq": metadata["revision_seq"], "content_hash": metadata["content_hash"],
                "indexed_at": metadata["indexed_at"], "root": metadata["repository_root"],
                "governance": metadata["governance"], "coverage": metadata["coverage"],
                "freshness": {"status": "NOT_CHECKED", "current_worktree": "NOT_CHECKED",
                              "reason": "Governance is the stored publication observation only"},
                "truncated": False,
            }
            if len(_json(report).encode("utf-8")) + 1 > max_bytes:
                # Never turn an omitted violation list into a clean report.
                report["governance"] = {"status": metadata["governance"]["status"],
                                        "violations_omitted": len(metadata["governance"].get("violations", []))}
                report["coverage"] = {"status": metadata["coverage"]["status"], "details_omitted": True}
                report["truncated"] = True
            return report

    def call_tool(self, name: str, arguments: Any) -> dict[str, Any]:
        definitions = {tool["name"]: tool for tool in self.tools()}
        if name not in definitions:
            raise ProtocolError(-32602, "Unknown tool")
        allowed = set(definitions[name]["inputSchema"]["properties"])
        args = _object(arguments, allowed)
        max_bytes = _integer(args.get("max_bytes", min(8192, self.max_tool_bytes)), "max_bytes", 1024, self.max_tool_bytes)
        kwargs: dict[str, Any] = {"max_bytes": max_bytes, "state_dir": self.state_dir}
        if name != "check_drift":
            query = _text(args.get("query"), "query")
            kwargs["limit"] = _integer(args.get("limit", 20), "limit", 1, 100)
            kwargs["mode"] = "context" if name == "get_context_envelope" else args.get("mode", "search")
            if kwargs["mode"] not in MODES:
                raise ProtocolError(-32602, "Unknown query mode")
            if name == "query_causal_graph":
                kwargs["max_nodes"] = _integer(args.get("max_nodes", 100), "max_nodes", 1, 1000)
                kwargs["max_hops"] = _integer(args.get("max_hops", 3), "max_hops", 0, 10)
                for key in ("target", "relation"):
                    if key in args:
                        kwargs[key] = _text(args[key], key, 128 if key == "relation" else 512)
                if "direction" in args:
                    if args["direction"] not in ("in", "out", "both"):
                        raise ProtocolError(-32602, "Invalid direction")
                    kwargs["direction"] = args["direction"]
                if kwargs["mode"] == "path" and "target" not in kwargs:
                    raise ProtocolError(-32602, "path requires target")
        try:
            # Keep any dependency diagnostic off the MCP wire.
            with redirect_stdout(sys.stderr):
                if name == "check_drift":
                    payload = self._drift(max_bytes)
                else:
                    from .query import project_query
                    payload = project_query(self.root, query, **kwargs)
            encoded = _json(payload)
            if len(encoded.encode("utf-8")) + 1 > max_bytes:
                payload = {"status": "BUDGET_EXCEEDED", "reason": "Complete result exceeds max_bytes",
                           "revision_seq": payload.get("revision_seq"), "content_hash": payload.get("content_hash"),
                           "truncated": True, "freshness": {"status": "NOT_CHECKED"}}
            is_error = payload.get("status") in {"ERROR", "UNAVAILABLE", "BUDGET_EXCEEDED", "INVALID_ARGUMENT"}
        except (IndexUnavailableError, sqlite3.Error, OSError, json.JSONDecodeError):
            payload = {"status": "UNAVAILABLE", "reason": "Published index unavailable or invalid",
                       "revision_seq": None, "content_hash": None, "freshness": {"status": "NOT_CHECKED"}}
            is_error = True
        except ValueError as exc:
            raise ProtocolError(-32602, "Query arguments rejected") from exc
        except Exception:
            payload = {"status": "ERROR", "reason": "Internal query failure",
                       "revision_seq": None, "content_hash": None, "freshness": {"status": "NOT_CHECKED"}}
            is_error = True
        return {"content": [{"type": "text", "text": _json(payload)}], "isError": is_error}

    def handle(self, message: Any) -> dict[str, Any] | None:
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return _error(None, -32600, "Invalid JSON-RPC request")
        notification = "id" not in message
        request_id = message.get("id")
        if not notification:
            try:
                valid_id = (type(request_id) is str and len(request_id.encode("utf-8")) <= 256
                            or type(request_id) is int and -(2**63) <= request_id < 2**63)
            except UnicodeError:
                valid_id = False
            if not valid_id:
                return _error(None, -32600, "Invalid request ID")
        if notification:
            if message["method"] == "notifications/initialized" and self.initialized:
                self.ready = True
            return None  # Notifications never produce a response or execute tools.
        try:
            params = message.get("params", {})
            method = message["method"]
            if method == "ping":
                _object(params, {"_meta"})
                result: Any = {}
            elif method == "initialize":
                if self.initialized:
                    raise ProtocolError(-32600, "Session is already initialized")
                params = _object(params, {"protocolVersion", "capabilities", "clientInfo", "_meta"})
                _text(params.get("protocolVersion"), "protocolVersion", 64)
                if not isinstance(params.get("capabilities"), dict) or not isinstance(params.get("clientInfo"), dict):
                    raise ProtocolError(-32602, "initialize requires capabilities and clientInfo objects")
                _text(params["clientInfo"].get("name"), "clientInfo.name", 256)
                _text(params["clientInfo"].get("version"), "clientInfo.version", 256)
                from . import __version__
                result = {"protocolVersion": PROTOCOL_VERSION, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "specgraph", "version": __version__},
                          "instructions": "Read-only published snapshots for the configured root. Index explicitly outside this server; current worktree freshness is not checked."}
                self.initialized = True
            elif not self.ready:
                raise ProtocolError(-32002, "Initialize the session and send notifications/initialized first")
            elif method == "tools/list":
                params = _object(params, {"cursor", "_meta"})
                if params.get("cursor") is not None:
                    raise ProtocolError(-32602, "No pagination cursor is supported for this fixed tool list")
                result = {"tools": self.tools()}
            elif method == "tools/call":
                params = _object(params, {"name", "arguments", "_meta"})
                result = self.call_tool(_text(params.get("name"), "tool name", 128), params.get("arguments", {}))
            else:
                raise ProtocolError(-32601, "Method not found")
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except ProtocolError as exc:
            return _error(request_id, exc.code, str(exc))
        except Exception:
            return _error(request_id, -32603, "Internal server error")

    def encode_response(self, response: dict[str, Any]) -> bytes:
        encoded = (_json(response) + "\n").encode("utf-8")
        if len(encoded) > self.max_response_bytes:
            encoded = (_json(_error(response.get("id"), -32001, "Response exceeds transport byte budget")) + "\n").encode("utf-8")
        return encoded


def serve(root: Path | str, *, state_dir: Path | str | None = None,
          stdin: BinaryIO | None = None, stdout: BinaryIO | None = None,
          max_request_bytes: int = MAX_REQUEST_BYTES,
          max_response_bytes: int = MAX_RESPONSE_BYTES) -> int:
    """Serve newline-delimited JSON until EOF; never writes source or index data."""
    _integer(max_request_bytes, "max_request_bytes", 1024, 1_048_576)
    server = MCPServer(root, state_dir=state_dir, max_response_bytes=max_response_bytes)
    reader, writer = stdin or sys.stdin.buffer, stdout or sys.stdout.buffer
    while True:
        line = reader.readline(max_request_bytes + 1)
        if not line:
            return 0
        if len(line) > max_request_bytes:
            while line and not line.endswith(b"\n"):
                line = reader.readline(max_request_bytes + 1)
            response = _error(None, -32600, "Request exceeds transport byte budget")
        elif not line.endswith(b"\n"):
            response = _error(None, -32700, "Truncated newline-delimited message")
        else:
            try:
                message = json.loads(line.decode("utf-8"), object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
                response = server.handle(message)
            except (ValueError, UnicodeError, RecursionError):
                response = _error(None, -32700, "Invalid UTF-8 JSON message")
        if response is not None:
            writer.write(server.encode_response(response))
            writer.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--max-request-bytes", type=int, default=MAX_REQUEST_BYTES)
    parser.add_argument("--max-response-bytes", type=int, default=MAX_RESPONSE_BYTES)
    args = parser.parse_args(argv)
    try:
        return serve(args.root, state_dir=args.state_dir, max_request_bytes=args.max_request_bytes,
                     max_response_bytes=args.max_response_bytes)
    except (OSError, ValueError) as exc:
        print(f"SpecGraph MCP server: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
