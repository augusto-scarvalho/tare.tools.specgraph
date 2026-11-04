"""MCP stdio interoperability and read-only boundaries (SPEC-SPECGRAPH-C2 AC-05)."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from specgraph.graph import CausalTraceabilityMatrix
from specgraph.indexer import index_repository
from specgraph.mcp_server import MCPServer, PROTOCOL_VERSION, serve
from specgraph.storage import index_path


def request(method, params=None, request_id=1):
    item = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        item["params"] = params
    return item


def initialize(version=PROTOCOL_VERSION):
    return request("initialize", {"protocolVersion": version, "capabilities": {},
                                  "clientInfo": {"name": "contract-test", "version": "1"}})


INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def wire(messages):
    return b"".join((json.dumps(m, ensure_ascii=False) + "\n").encode() for m in messages)


def payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def ready_server(root, state, **kwargs):
    server = MCPServer(root, state_dir=state, **kwargs)
    assert "result" in server.handle(initialize())
    assert server.handle(INITIALIZED) is None
    return server


@pytest.fixture
def indexed_repo(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src/example.py").write_text('def alpha():\n    """Publicação α."""\n    return 1\n', encoding="utf-8")
    (root / "specgraph.yaml").write_text("strict_orphan_check: true\n", encoding="utf-8")
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    return root, state, metadata


def run_process(root, state, messages, **kwargs):
    env = os.environ.copy()
    source = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = str(source) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "specgraph.mcp_server", "--root", str(root),
                           "--state-dir", str(state)], input=wire(messages), capture_output=True,
                          env=env, timeout=15, **kwargs)


def test_real_stdio_initialize_list_query_context_drift_and_eof(indexed_repo):
    root, state, metadata = indexed_repo
    from specgraph.query import project_query
    sdk = project_query(root, "alpha", state_dir=state)
    source_bytes = (root / "src/example.py").read_bytes()
    database_bytes = index_path(root, state_dir=state).read_bytes()
    messages = [initialize(), INITIALIZED, request("tools/list", request_id=2),
                request("tools/call", {"name": "query_causal_graph", "arguments": {"query": "alpha"}}, 3),
                request("tools/call", {"name": "get_context_envelope", "arguments": {"query": "alpha"}}, 4),
                request("tools/call", {"name": "check_drift"}, 5), request("ping", request_id=6),
                request("tools/call", {"name": "index"}, 7),
                request("tools/call", {"name": "query_causal_graph", "arguments": {"query": "alpha", "root": str(root.parent)}}, 8)]
    result = run_process(root, state, messages)
    assert result.returncode == 0 and result.stderr == b""
    lines = result.stdout.splitlines()
    assert len(lines) == 8  # No response to notifications/initialized.
    responses = [json.loads(line) for line in lines]
    assert [r["id"] for r in responses] == list(range(1, 9))
    assert responses[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    tools = responses[1]["result"]["tools"]
    assert {t["name"] for t in tools} == {"query_causal_graph", "get_context_envelope", "check_drift"}
    assert all(t["annotations"]["readOnlyHint"] and not t["annotations"]["destructiveHint"] for t in tools)
    assert payload(responses[2]) == sdk
    assert payload(responses[3])["mode"] == "context"
    drift = payload(responses[4])
    assert drift["revision_seq"] == metadata["revision_seq"]
    assert drift["content_hash"] == metadata["content_hash"]
    assert drift["governance"]["status"] == "FAIL"  # Published orphans are visible, not blocked.
    assert drift["freshness"]["status"] == "NOT_CHECKED"
    assert responses[5]["result"] == {}
    assert responses[6]["error"]["code"] == responses[7]["error"]["code"] == -32602
    assert (root / "src/example.py").read_bytes() == source_bytes
    assert index_path(root, state_dir=state).read_bytes() == database_bytes
    assert not (root / ".specgraph").exists()


def test_version_negotiation_initialization_order_and_notifications(tmp_path):
    server = MCPServer(tmp_path)
    assert server.handle(request("ping"))["result"] == {}
    assert server.handle(request("tools/list"))["error"]["code"] == -32002
    response = server.handle(initialize("2099-01-01"))
    assert response["result"]["protocolVersion"] == "2025-06-18"
    assert server.handle(request("tools/list"))["error"]["code"] == -32002
    assert server.handle(INITIALIZED) is None
    assert server.handle({"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "index"}}) is None
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/unknown"}) is None
    assert server.handle(initialize())["error"]["code"] == -32600
    assert server.handle(request("shutdown"))["error"]["code"] == -32601


@pytest.mark.parametrize("params", [
    {"name": "index", "arguments": {}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "root": "/another/repo"}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "state_dir": "/other"}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "limit": True}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "limit": 1.5}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "max_nodes": 1001}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "max_hops": -1}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "mode": "index"}},
    {"name": "query_causal_graph", "arguments": {"query": "alpha", "mode": "path"}},
    {"name": "query_causal_graph", "arguments": {"query": "α" * 257}},
    {"name": "get_context_envelope", "arguments": {"query": "alpha", "mode": "path"}},
    {"name": "check_drift", "arguments": {"deep": True}},
    {"name": "query_causal_graph", "arguments": {}},
    {"name": "query_causal_graph", "arguments": []},
])
def test_invalid_and_crossroot_tool_arguments_are_protocol_errors(tmp_path, params):
    server = ready_server(tmp_path, tmp_path / "state")
    response = server.handle(request("tools/call", params))
    assert response["error"]["code"] == -32602
    assert not (tmp_path / "state").exists()


def test_queries_do_not_read_source_or_build_and_drift_is_not_live(indexed_repo, monkeypatch):
    root, state, metadata = indexed_repo
    from specgraph import query  # Import before disabling source-read helpers.
    shutil.rmtree(root / "src")
    def prohibited(*args, **kwargs):
        raise AssertionError("Forbidden source read or build")
    monkeypatch.setattr(CausalTraceabilityMatrix, "build", prohibited)
    monkeypatch.setattr(Path, "read_bytes", prohibited)
    monkeypatch.setattr(Path, "read_text", prohibited)
    monkeypatch.setattr(os, "scandir", prohibited)
    server = ready_server(root, state)
    for name in ("query_causal_graph", "get_context_envelope", "check_drift"):
        args = {} if name == "check_drift" else {"query": "alpha"}
        response = server.handle(request("tools/call", {"name": name, "arguments": args}))
        assert response["result"]["isError"] is False
        assert payload(response)["content_hash"] == metadata["content_hash"]


def test_each_call_opens_a_new_snapshot(indexed_repo):
    root, state, old = indexed_repo
    server = ready_server(root, state)
    call = request("tools/call", {"name": "check_drift"})
    assert payload(server.handle(call))["revision_seq"] == old["revision_seq"]
    (root / "src/example.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    new = index_repository(root, state_dir=state)
    second = payload(server.handle(call))
    assert second["revision_seq"] == new["revision_seq"] == old["revision_seq"] + 1
    assert second["content_hash"] == new["content_hash"]


def test_missing_corrupt_and_wrong_root_indices_are_tool_errors_without_writes(indexed_repo):
    root, state, _ = indexed_repo
    other = root.parent / "other"
    other.mkdir()
    call = request("tools/call", {"name": "check_drift"})
    server = ready_server(other, state)
    missing = server.handle(call)
    assert missing["result"]["isError"] is True
    assert payload(missing)["revision_seq"] is None
    wrong = index_path(other, state_dir=state)
    assert not wrong.exists()
    wrong.parent.mkdir(parents=True)
    shutil.copy2(index_path(root, state_dir=state), wrong)
    assert payload(server.handle(call))["status"] == "UNAVAILABLE"
    wrong.write_bytes(b"not sqlite")
    assert payload(server.handle(call))["status"] == "UNAVAILABLE"
    assert wrong.read_bytes() == b"not sqlite"


def test_stdio_rejects_bad_frames_recovers_and_bounds_memory_and_output(tmp_path):
    input_bytes = (b'x' * 2048 + b'\n' + b'\xff\n' + b'[]\n' +
                   b'{"jsonrpc":"2.0","id":1,"method":"ping","method":"index"}\n' +
                   b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n' +
                   wire([request("ping", request_id=9)]) + b'{"jsonrpc":"2.0"}')
    output = io.BytesIO()
    assert serve(tmp_path, stdin=io.BytesIO(input_bytes), stdout=output,
                 max_request_bytes=1024, max_response_bytes=4096) == 0
    lines = output.getvalue().splitlines(keepends=True)
    assert all(len(line) <= 4096 and line.endswith(b"\n") for line in lines)
    responses = [json.loads(line) for line in lines]
    assert [r.get("error", {}).get("code") for r in responses] == [-32600, -32700, -32600, -32700, -32700, None, -32700]
    assert responses[-2]["id"] == 9 and responses[-2]["result"] == {}


@pytest.mark.parametrize("request_id", [None, True, 1.2, "x" * 257, "\ud800", 2**64])
def test_invalid_request_ids_cannot_escape_the_wire_bound(tmp_path, request_id):
    server = MCPServer(tmp_path)
    response = server.handle(request("ping", request_id=request_id))
    assert response["error"]["code"] == -32600
    assert json.loads(server.encode_response(response))["id"] is None


def test_query_errors_are_bounded_and_do_not_leak_exception_or_source_text(indexed_repo, monkeypatch):
    root, state, metadata = indexed_repo
    from specgraph import query
    server = ready_server(root, state, max_response_bytes=4096)
    call = request("tools/call", {"name": "query_causal_graph", "arguments": {"query": "alpha", "max_bytes": 1024}})
    def huge(*args, **kwargs):
        return {"status": "HIT", "revision_seq": metadata["revision_seq"], "content_hash": metadata["content_hash"], "results": ['"\\\n' * 5000]}
    monkeypatch.setattr(query, "project_query", huge)
    response = server.handle(call)
    assert payload(response)["status"] == "BUDGET_EXCEEDED"
    assert payload(response)["content_hash"] == metadata["content_hash"]
    assert len(server.encode_response(response)) <= 4096
    def failure(*args, **kwargs):
        raise RuntimeError("confidential detail must not reach wire")
    monkeypatch.setattr(query, "project_query", failure)
    response = server.handle(call)
    assert response["result"]["isError"] is True
    assert b"confidential" not in server.encode_response(response)
    too_big = {"jsonrpc": "2.0", "id": 1, "result": "x" * 5000}
    assert json.loads(server.encode_response(too_big))["error"]["code"] == -32001
