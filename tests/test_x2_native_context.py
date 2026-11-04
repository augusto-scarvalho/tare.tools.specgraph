"""Offline producer behavior + transport through the real, unmodified Kernel consumer.

Set SPECGRAPH_X2_KERNEL_ROOT to an existing Kernel checkout, or install Kernel and
Agent Runtime locally. No downloads, frozen harness imports, or live providers.
Missing dependencies are explicit skips, never qualification of transport.
"""
import ast
import hashlib
import importlib
import inspect
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import warnings

import pytest

from specgraph.hook_context import context_hint
from specgraph.indexer import index_repository


@pytest.fixture
def native_dependencies(monkeypatch, record_property):
    checkout = os.environ.get("SPECGRAPH_X2_KERNEL_ROOT")
    root = Path(checkout).resolve() if checkout else None
    if root is not None:
        if not (root / "compute_plane/native_project_context.py").is_file():
            pytest.fail("SPECGRAPH_X2_KERNEL_ROOT does not contain the real consumer")
        monkeypatch.syspath_prepend(str(root))
        # sys.path alone cannot override a package already loaded from another checkout.
        for name, module in tuple(sys.modules.items()):
            if name == "compute_plane" or name.startswith("compute_plane."):
                origin = getattr(module, "__file__", None)
                if origin and not Path(origin).resolve().is_relative_to(root / "compute_plane"):
                    pytest.fail(f"mixed Kernel checkout: {name}: {origin}; selected {root}")
    try:
        consumer = importlib.import_module("compute_plane.native_project_context")
        runtime = importlib.import_module("tare_tools_agent_runtime")
        importlib.import_module("compute_plane.native_exploration")
    except ImportError as exc:
        pytest.skip(f"UNQUALIFIED native consumer: local dependency unavailable: {exc}")
    selected = root or Path(consumer.__file__).resolve().parent.parent
    for name, module in tuple(sys.modules.items()):
        if name == "compute_plane" or name.startswith("compute_plane."):
            origin = getattr(module, "__file__", None)
            if origin and not Path(origin).resolve().is_relative_to(selected / "compute_plane"):
                pytest.fail(f"mixed Kernel checkout: {name}: {origin}; selected {selected}")
    # Include defining Runtime modules, not just its re-exporting __init__.py.
    names = {"compute_plane.native_exploration", "compute_plane.native_project_context",
             "compute_plane.mediated_tools", "compute_plane.native_models",
             "compute_plane.tool_hooks", "compute_plane.native_journal", "compute_plane.workspace",
             "tare_tools_agent_runtime", "specgraph", "specgraph.hook_context", "specgraph.indexer"}
    names.update(inspect.getmodule(getattr(runtime, name)).__name__
                 for name in ("BindingRef", "ExecutionControl", "CancellationToken",
                              "ConversationLog", "ConversationLimits"))
    provenance = {}
    for name in sorted(names):
        module = importlib.import_module(name)
        origin = Path(module.__file__).resolve()
        provenance[name] = {"origin": str(origin), "sha256": hashlib.sha256(origin.read_bytes()).hexdigest()}
    versions = {}
    for distribution in ("tare-tools-specgraph", "tare-tools-kernel", "tare-tools-agent-runtime", "pytest"):
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "distribution metadata unavailable"
    receipt = {"selection": "SPECGRAPH_X2_KERNEL_ROOT" if checkout else "installed package root",
               "kernel_root": str(selected), "modules": provenance, "distribution_versions": versions}
    record_property("native_provenance", json.dumps(receipt, sort_keys=True))
    warnings.warn("X2 recovery provenance: " + json.dumps(receipt, sort_keys=True), UserWarning)
    return consumer, runtime, receipt


@pytest.mark.parametrize("dynamic", [False, True], ids=["direct-positive", "callback-negative"])
def test_real_native_lookup_preserves_assessment_and_witnesses(tmp_path, native_dependencies, dynamic,
                                                               record_property):
    """@verifies SPEC-SPECGRAPH-X2:AC-03

    Question: which indexed declaration binds caller's call at line 3?
    This test's policy is NOT a production downstream sufficiency gate.
    """
    consumer, runtime, _ = native_dependencies
    origin = Path(inspect.getfile(consumer.NativeProjectContext)).resolve()
    record_property("consumer_origin", str(origin))
    record_property("consumer_sha256", hashlib.sha256(origin.read_bytes()).hexdigest())
    record_property("runtime_origin", runtime.__file__)
    codecs = importlib.import_module("compute_plane.native_models")
    codecs_origin = Path(codecs.__file__).resolve()
    runtime_sha = hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest()
    codecs_sha = hashlib.sha256(codecs_origin.read_bytes()).hexdigest()
    record_property("runtime_sha256", runtime_sha)
    record_property("codecs_sha256", codecs_sha)
    import warnings
    warnings.warn(
        "X2 offline dependency provenance: consumer=" + str(origin)
        + "; consumer_sha256=" + hashlib.sha256(origin.read_bytes()).hexdigest()
        + "; runtime=" + str(runtime.__file__) + "; runtime_sha256=" + runtime_sha
        + "; codecs=" + str(codecs_origin) + "; codecs_sha256=" + codecs_sha, UserWarning)
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    source = ("def leaf(): return 1\ndef caller(callback):\n    return callback()\n" if dynamic else
              "def leaf(): return 1\ndef caller():\n    return leaf()\n")
    # Independent stdlib AST/source oracle; does not use SpecGraph's assessment.
    tree = ast.parse(source)
    caller_ast = tree.body[1]
    call_ast = caller_ast.body[0].value
    expected_name = "callback" if dynamic else "leaf"
    assert isinstance(call_ast, ast.Call) and isinstance(call_ast.func, ast.Name)
    assert call_ast.func.id == expected_name
    (root / "src/lib.py").write_bytes(source.encode("utf-8"))
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    (root / "src/lib.py").unlink()  # Both real APIs must use the published snapshot.
    direct = context_hint(root, role="implementer", event="pre", query="caller",
                          work_id="x2-offline", state_dir=state, timeout_ms=1000, max_bytes=8192)
    binding = runtime.BindingRef("x2-offline", "b", "o", 1, "t")
    control = runtime.ExecutionControl(binding, runtime.CancellationToken(), time.monotonic() + 30)
    provider = consumer.NativeProjectContext(repositories={"fixture": str(root)}, work_id="x2-offline",
        state_dirs={"fixture": state}, max_bytes=8192, timeout_ms=1000)
    transported = provider.lookup("fixture", "caller", control)
    assert direct["status"] == transported["status"] == "HIT"
    assert transported["substitute_assessment"] == direct["substitute_assessment"]
    assert transported["symbols"] == direct["symbols"]
    assert transported["digest"] == direct["digest"]
    assert transported["snapshot"] == metadata["content_hash"]
    assert transported["repository"] == "fixture" and transported["root"] == str(root.resolve())
    assert transported["absence_proven"] is False and transported["authority"] == "NONE"
    symbol, = transported["symbols"]
    assert symbol["id"] == "src/lib.py:caller"
    assert symbol["range"] == [caller_ast.lineno, caller_ast.end_lineno]
    call, = [r for r in symbol["references"] if r["kind"] == "call"]
    assert (call["name"], call["line"], call["column"]) == (expected_name, call_ast.lineno, call_ast.col_offset)
    snippet, = transported["snippets"]
    assert snippet["text"] == "".join(source.splitlines(keepends=True)[1:])
    assert snippet["sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assessment = transported["substitute_assessment"]
    # Explicitly scoped offline policy: uncertainty retains necessary recovery.
    blockers = assessment["reasons"]
    substitute_for_this_question = not blockers and call["status"] == "resolved" and bool(call["target"])
    if dynamic:
        assert call["target"] is None
        reason, = blockers
        assert reason == {"code": "UNRESOLVED_REFERENCE", "symbol": symbol["id"],
                          "reference_index": symbol["references"].index(call), "reason": call["reason"]}
        assert not substitute_for_this_question
        recovery = {"snapshot": transported["snapshot"], "symbol": reason["symbol"],
                    "reference": symbol["references"][reason["reference_index"]],
                    "needed": "caller-supplied callback binding; no execution conclusion"}
        assert recovery["reference"]["name"] == "callback"
        assert assessment["status"] == "RECOVERY_REQUIRED"
    else:
        assert call["target"] == "src/lib.py:leaf" and call["status"] == "resolved"
        assert substitute_for_this_question
        assert assessment["status"] == "STATIC_REFERENCES_DELIVERED"
    assert assessment["execution_complete"] is False


@pytest.mark.parametrize("dynamic", [False, True], ids=["direct-positive", "callback-insufficient"])
def test_real_collector_default_recovery_and_hash_refusal(tmp_path, native_dependencies, dynamic,
                                                         record_property):
    """@verifies SPEC-SPECGRAPH-X2:AC-03

    Production collect recovers in BOTH cases; no test-local sufficiency decision.
    Extra raw reads below corroborate the serialized read envelope, not a gate.
    """
    consumer, runtime, provenance = native_dependencies
    exploration = importlib.import_module("compute_plane.native_exploration")
    codecs = importlib.import_module("compute_plane.native_models")
    reader = importlib.import_module("compute_plane.mediated_tools")
    kernel_root = Path(provenance["kernel_root"])
    module_names = ("compute_plane.native_exploration", "compute_plane.native_project_context",
                    "compute_plane.mediated_tools", "compute_plane.native_models")
    for name in module_names:
        module = importlib.import_module(name)
        expected_origin = (kernel_root / (name.replace(".", "/") + ".py")).resolve()
        assert Path(module.__file__).resolve() == expected_origin
        assert provenance["modules"][name] == {
            "origin": str(expected_origin),
            "sha256": hashlib.sha256(expected_origin.read_bytes()).hexdigest()}
    assert exploration.NativeProjectContext is consumer.NativeProjectContext
    assert exploration.MediatedTools is reader.MediatedTools
    assert exploration.encode is codecs.encode
    # Both source-line encodings are supported contracts, not checkout identities.
    # Module hashes above are provenance only; source/AST still determines every
    # delivered line. Never accept arbitrary text by learning it from the reader.
    delimiters = ("|", ": ")
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    source = ("def leaf(): return 1\ndef caller(callback):\n    return callback()\n" if dynamic else
              "def leaf(): return 1\ndef caller():\n    return leaf()\n")
    raw = source.encode("utf-8")
    target = root / "src/lib.py"
    target.write_bytes(raw)  # Known LF bytes, still readable throughout collection.
    tree = ast.parse(source)
    caller = tree.body[1]
    call_ast = caller.body[0].value
    assert isinstance(call_ast, ast.Call) and isinstance(call_ast.func, ast.Name)
    assert call_ast.func.id == ("callback" if dynamic else tree.body[0].name)
    start, end = caller.lineno, caller.end_lineno
    # Exact source-derived alternatives differ ONLY in the line-number separator.
    expected_texts = {
        delimiter: "\n".join(f"{i}{delimiter}{line}"
                             for i, line in enumerate(source.splitlines(), 1) if start <= i <= end)
        for delimiter in delimiters}
    original_sha = hashlib.sha256(raw).hexdigest()
    state = tmp_path / "state"
    metadata = index_repository(root, state_dir=state)
    binding = runtime.BindingRef("x2-recovery", "b", "o", 1, "t")
    control = runtime.ExecutionControl(binding, runtime.CancellationToken(), time.monotonic() + 30)

    def collector():
        return exploration.ExplorationSources(root, "fixture", control, index_state=state)

    def check_observations(instance, packet, observed_raw):
        context = packet["context"]
        assert context["status"] == "HIT"
        assert context["query"] == "src/lib.py:caller"
        assert context["snapshot"] == metadata["content_hash"]
        assert packet["selection"] == "up_to_four_snapshot_symbols"
        assert packet["authority"] == "NONE" and packet["absence_proven"] is False
        # These are real additional reads through the same production tool, unmocked.
        # Assert the complete envelope before comparing its actual UTF-8 serialization.
        envelope = instance.tools.perform("read", {"repository": "fixture", "path": "src/lib.py",
                                                   "start": start, "end": end})
        lines = observed_raw.decode("utf-8").splitlines()
        expected_envelope = {
            "sha256": hashlib.sha256(observed_raw).hexdigest(), "lines": len(lines),
            "text": "\n".join(f"{i}{delimiter}{line}" for i, line in enumerate(lines, 1)
                              if start <= i <= end),
            "start": start, "end": end, "requested_end": end,
            "truncated": False, "next_start": None}
        assert envelope == expected_envelope
        numbered = envelope["text"]
        context_event, read_event = packet["observations"]
        assert context_event["operation"] == "context" and context_event["status"] == "HIT"
        assert read_event["operation"] == "read" and read_event["path"] == "src/lib.py"
        assert context_event["returned_bytes"] == len(codecs.encode(context).encode("utf-8"))
        assert read_event["returned_bytes"] == len(codecs.encode(envelope).encode("utf-8"))
        for event in packet["observations"]:
            assert event["duration_ms"] >= 0 and event["tokens"] is None
        return {"fixture_bytes": len(observed_raw), "fixture_sha256": envelope["sha256"],
                "range": [start, end], "read_text": numbered,
                "read_text_bytes": len(numbered.encode("utf-8")),
                "observations": [dict(event) for event in packet["observations"]]}

    initial = collector()
    packet = initial.collect("src/lib.py:caller", focus=())
    recovered, = packet["sources"]
    matching_delimiters = [separator for separator, text in expected_texts.items()
                           if recovered["text"] == text]
    assert len(matching_delimiters) == 1, recovered
    delimiter, = matching_delimiters
    expected_text = expected_texts[delimiter]
    receipt = {"case": "callback-insufficient" if dynamic else "direct-positive",
               "provenance": {"kernel_root": str(kernel_root), "selection": provenance["selection"],
                              "modules": {name: provenance["modules"][name] for name in module_names},
                              "line_delimiter": delimiter},
               "initial": check_observations(initial, packet, raw)}
    symbol, = packet["context"]["symbols"]
    assert symbol["id"] == "src/lib.py:caller" and symbol["range"] == [start, end]
    assert symbol["source_sha256"] == original_sha
    call, = [ref for ref in symbol["references"] if ref["kind"] == "call"]
    assert (call["name"], call["line"], call["column"]) == (
        call_ast.func.id, call_ast.lineno, call_ast.col_offset)
    assessment = packet["context"]["substitute_assessment"]
    if dynamic:
        assert call["target"] is None and call["status"] != "resolved"
        reason, = assessment["reasons"]
        assert reason == {"code": "UNRESOLVED_REFERENCE", "symbol": symbol["id"],
                          "reference_index": symbol["references"].index(call), "reason": call["reason"]}
        assert assessment["status"] == "RECOVERY_REQUIRED"
    else:
        assert call["target"] == "src/lib.py:leaf" and call["status"] == "resolved"
        assert assessment["reasons"] == []
        assert assessment["status"] == "STATIC_REFERENCES_DELIVERED"
    assert assessment["execution_complete"] is False
    recovered, = packet["sources"]  # Also required when the direct report is positive.
    assert packet["missing"] == []
    assert recovered["text"] == expected_text
    assert receipt["initial"]["read_text"] == expected_text
    assert {key: recovered[key] for key in ("repository", "path", "revision", "start_line", "end_line",
                                            "sha256", "text")} == {
        "repository": "fixture", "path": "src/lib.py", "revision": "WORKTREE",
        "start_line": start, "end_line": end, "sha256": original_sha, "text": expected_text}
    assert target.read_bytes() == raw

    # Change actual bytes without reindexing. New collect still selects the OLD hash.
    changed = raw.replace(b"return leaf()", b"return leaf() # changed").replace(
        b"return callback()", b"return callback() # changed")
    assert changed != raw
    target.write_bytes(changed)
    stale = collector()
    refused = stale.collect("src/lib.py:caller", focus=())
    receipt["changed"] = check_observations(stale, refused, changed)
    old_symbol, = refused["context"]["symbols"]
    assert old_symbol["source_sha256"] == original_sha
    assert refused["sources"] == []
    assert refused["missing"] == [{"path": "src/lib.py", "reason": "exploration_source_changed"}]
    assert refused["directory"] == {"entries": ["src/"], "truncated": False}
    with pytest.raises(ValueError, match="^exploration_source_changed$"):
        initial.verify(packet["sources"])
    # verify attempted a real read too; it cannot publish changed text as a valid source.
    assert initial.observations[-1]["operation"] == "read"
    assert initial.observations[-1]["returned_bytes"] == receipt["changed"]["observations"][1]["returned_bytes"]
    assert initial.observations[-1]["duration_ms"] >= 0
    assert initial.observations[-1]["tokens"] is None
    assert recovered["sha256"] == original_sha and recovered["text"] == expected_text
    receipt["initial"]["published_source_count"] = len(packet["sources"])
    receipt["changed"].update(published_source_count=len(refused["sources"]),
                              missing=refused["missing"], verify_error="exploration_source_changed",
                              verify_observation=dict(initial.observations[-1]))
    record_property("recovery_receipt", json.dumps(receipt, sort_keys=True))
    warnings.warn("X2 real recovery receipt: " + json.dumps(receipt, sort_keys=True), UserWarning)
