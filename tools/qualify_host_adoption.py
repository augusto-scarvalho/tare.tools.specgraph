"""Explicit native Aider /run adoption, isolated from global hooks and Work state.

Prepare freezes the task oracle before inference. Emit reads one published
SpecGraph snapshot; a loopback observer forwards actual Aider requests unchanged
to the explicitly permitted local endpoint and retains metadata, not prompts.
This qualifies an on-demand command bridge, never an automatic PreToolUse hook.
"""
from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.request

WORK = "SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908"
ENDPOINT = os.environ.get("TARE_LOCAL_MODELS", "http://127.0.0.1:8080").rstrip("/").removesuffix("/v1") + "/v1"
ROLES = ("planner", "implementer", "auditor", "explorer")
MAX_REQUEST = 65536
MAX_RESPONSE = 1048576


def wire(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


def save(path, value):
    Path(path).write_bytes(wire(value))


def read(path, limit=MAX_RESPONSE):
    with Path(path).open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ValueError("input exceeds explicit byte budget")
    return json.loads(body)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], stderr=subprocess.PIPE)


def health():
    def get(path):
        with urllib.request.urlopen(ENDPOINT.removesuffix("/v1") + path, timeout=5) as response:
            raw = response.read(MAX_REQUEST + 1)
        if len(raw) > MAX_REQUEST:
            raise ValueError("health response exceeds budget")
        return json.loads(raw)
    catalogue = get("/v1/models")
    status = get("/health")
    return {"endpoint": ENDPOINT, "models": [x["id"] for x in catalogue.get("data", [])],
            "health": {k: status.get(k) for k in ("status", "role", "backend_healthy", "current_model")}}


def usage(value):
    """Require actual provider counters; missing cache information remains unknown."""
    if not isinstance(value, dict):
        return None
    keys = ("prompt_tokens", "completion_tokens", "total_tokens")
    if not all(type(value.get(k)) is int and value[k] >= 0 for k in keys):
        return None
    if value["total_tokens"] != value["prompt_tokens"] + value["completion_tokens"]:
        return None
    details = value.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    if cached is not None and (type(cached) is not int or not 0 <= cached <= value["prompt_tokens"]):
        return None
    return {k: value[k] for k in keys} | {"cache_read_tokens": cached,
            "cache_accounting": "SUBSET_OF_PROMPT_TOKENS" if cached is not None else "NOT_REPORTED"}


def answer(text):
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1])
    try:
        return json.loads(text)
    except ValueError:
        return None


def prepare(args):
    output = args.output.resolve()
    if output.exists():
        raise ValueError("fresh output directory required; preserve previous trials")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_ref):
        raise ValueError("full source Git commit required")
    tasks = read(args.tasks, MAX_REQUEST)
    if not isinstance(tasks, list) or len(tasks) != 4 or {x.get("role") for x in tasks} != set(ROLES):
        raise ValueError("exactly four explicitly contracted roles required")
    probe = health()
    if args.model not in probe["models"] or probe["health"]["current_model"] != args.model:
        raise ValueError("model must be listed and already loaded; qualification never switches models")
    if probe["health"]["backend_healthy"] is not True:
        raise ValueError("local provider is not healthy")
    from specgraph.storage import RepositoryIndex
    with RepositoryIndex(args.root, state_dir=args.state_dir) as index:
        metadata = index.metadata()
    baselines = {}
    for task in tasks:
        if not isinstance(task.get("prompt"), str) or len(task["prompt"]) > 4096:
            raise ValueError("bounded explicit prompt required")
        if not isinstance(task.get("query"), str) or not task["query"] or len(task["query"]) > 1024:
            raise ValueError("bounded explicit query required")
        if "expected" not in task or not isinstance(task.get("source_paths"), list):
            raise ValueError("pre-inference oracle and exact source paths required")
        sources = []
        for path in task["source_paths"]:
            if not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts:
                raise ValueError("relative source path required")
            body = git(args.source_repo or args.root, "show", args.source_ref + ":" + path)
            if (args.root / path).read_bytes().replace(b"\r\n", b"\n") != body.replace(b"\r\n", b"\n"):
                raise ValueError("selected working source differs from pinned task source: " + path)
            source_hash = sha(body)
            selected_range = task.get("source_ranges", {}).get(path)
            if selected_range is not None:
                lines = body.splitlines(keepends=True)
                if not (isinstance(selected_range, list) and len(selected_range) == 2
                        and all(type(n) is int for n in selected_range)
                        and 1 <= selected_range[0] <= selected_range[1] <= len(lines)):
                    raise ValueError("invalid pinned source range")
                body = b"".join(lines[selected_range[0] - 1:selected_range[1]])
            if len(body) > 32768:
                raise ValueError("selected source range exceeds bounded task scope")
            sources.append({"path": path, "git_revision": args.source_ref,
                            "file_sha256": source_hash, "selected_range": selected_range,
                            "sha256": sha(body), "body": body.decode("utf-8")})
        baselines[task["role"]] = {"root": str(args.root.resolve()), "work_id": WORK,
            "source_ref": args.source_repository + ":" + args.source_ref, "sources": sources}
        if len(wire(baselines[task["role"]])) > MAX_REQUEST // 2:
            raise ValueError("baseline packet exceeds task budget")
    output.mkdir(parents=True)
    protocol = {"schema": "tare.tools/specgraph-host-adoption/1", "work_id": WORK,
        "authority": "NONE", "qualification_scope": "BOUNDED_REAL_HOST_TASKS_NOT_FULL_WORK_COMPLETION",
        "root": str(args.root.resolve()), "engine_source_root": str(args.engine_src.resolve()),
        "state_dir": str(args.state_dir.resolve()) if args.state_dir else None,
        "source_ref": args.source_repository + ":" + args.source_ref,
        "source_scope": "selected task files; not a claim that every working-tree byte matches the pin",
        "snapshot": {k: metadata[k] for k in ("revision_seq", "content_hash")},
        "spec_contract_sha256": sha(args.contract.read_bytes()),
        "runner_sha256": sha(Path(__file__).read_bytes()), "aider_version": importlib.metadata.version("aider-chat"),
        "model": args.model, "provider": probe, "tasks": tasks, "rounds": args.rounds,
        "budgets": {"packet_bytes": 4096, "query_timeout_ms": 1000, "model_output_tokens": 512,
                    "model_calls_per_trial": 1, "request_bytes": MAX_REQUEST,
                    "response_bytes": MAX_RESPONSE, "trial_timeout_seconds": 90},
        "automatic_hooks": "NOT_CONFIGURED_OR_CLAIMED", "automatic_roles": [],
        "agy": "LOCAL_ROUTE_NOT_IDENTIFIED; NATIVE_DELIVERY_NOT_QUALIFIED",
        "decision_rule": "Keep on-demand unless all paired tasks pass with lower total tokens and no greater total wall time. Missing usage prohibits benefit claim.",
        "residuals": ["AGY native delivery", "automatic callback delivery", "multi-root host sessions",
                      "native resume/subagent lifecycle", "full production task effectiveness"]}
    save(output / "protocol.json", protocol)
    save(output / "baselines.json", baselines)
    save(output / "protocol-lock.json", {"protocol_sha256": sha(wire(protocol)), "baseline_sha256": sha(wire(baselines))})
    return {"status": "PREPARED_NO_MODEL_CALL", "protocol": str(output / "protocol.json"),
            "sha256": sha(wire(protocol)), "snapshot": protocol["snapshot"]}


def load_protocol(directory):
    protocol = read(directory / "protocol.json")
    lock = read(directory / "protocol-lock.json")
    if sha(wire(protocol)) != lock["protocol_sha256"] or sha((directory / "baselines.json").read_bytes()) != lock["baseline_sha256"]:
        raise ValueError("frozen protocol or baseline changed")
    if sha(Path(__file__).read_bytes()) != protocol["runner_sha256"]:
        raise ValueError("runner changed after freeze")
    return protocol


def emit(directory, role, arm, trial_dir):
    protocol = load_protocol(directory)
    task = next(x for x in protocol["tasks"] if x["role"] == role)
    started = time.perf_counter()
    if arm == "file_context":
        packet = read(directory / "baselines.json")[role]
    else:
        sys.path.insert(0, protocol["engine_source_root"])
        from specgraph.hook_context import context_hint
        packet = context_hint(protocol["root"], role=role, event="pre", query=task["query"],
            spec_id=task.get("spec_id"), symbol_id=task.get("symbol_id"), work_id=WORK,
            source_ref=protocol["source_ref"], max_bytes=4096, timeout_ms=1000, state_dir=protocol["state_dir"])
        if packet.get("snapshot") != protocol["snapshot"]["content_hash"]:
            raise ValueError("snapshot changed or unavailable after task freeze")
    body = wire(packet)
    if len(body) > (4096 if arm == "hook_context" else MAX_REQUEST // 2):
        raise ValueError("packet exceeds frozen budget")
    (trial_dir / "delivered-packet.json").write_bytes(body)
    save(trial_dir / "emission.json", {"packet_sha256": sha(body), "bytes": len(body),
        "wall_ms": (time.perf_counter() - started) * 1000, "mode": arm,
        "host_invocation": "NATIVE_AIDER_RUN_COMMAND; not automatic callback",
        "snapshot": packet.get("snapshot"), "status": packet.get("status", "PINNED_SOURCE")})
    sys.stdout.buffer.write(body)


def trial(directory, role, arm, round_number):
    protocol = load_protocol(directory)
    task = next(x for x in protocol["tasks"] if x["role"] == role)
    dest = directory / f"{round_number}-{role}-{arm}"
    dest.mkdir()
    calls = []
    class Observer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/v1/chat/completions" or len(calls) >= 1:
                self.send_error(429, "single explicit local model call budget")
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST:
                self.send_error(413)
                return
            raw = self.rfile.read(length)
            request = json.loads(raw)
            if request.get("model") != protocol["model"] or request.get("stream", False) is not False:
                self.send_error(400, "model or streaming differs from frozen protocol")
                return
            if type(request.get("max_tokens")) is not int or not 1 <= request["max_tokens"] <= 512:
                self.send_error(400, "output token cap required")
                return
            packet = (dest / "delivered-packet.json").read_text(encoding="utf-8").strip()
            messages = request.get("messages", [])
            included = any(packet in m.get("content", "") for m in messages if isinstance(m, dict) and isinstance(m.get("content"), str))
            record = {"request_sha256": sha(raw), "request_bytes": len(raw), "host_packet_observed_in_model_input": included,
                      "packet_sha256": sha((packet + "\n").encode()), "provider": ENDPOINT,
                      "model": request["model"], "usage": None, "answer": None}
            calls.append(record)
            started = time.perf_counter()
            try:
                forward = urllib.request.Request(ENDPOINT + "/chat/completions", data=raw,
                    headers={"Content-Type": "application/json", "Authorization": "Bearer local-key"})
                with urllib.request.urlopen(forward, timeout=70) as response:
                    body = response.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE:
                    raise ValueError("provider response too large")
                decoded = json.loads(body)
                record.update(usage=usage(decoded.get("usage")), response_sha256=sha(body),
                    observed_model=decoded.get("model"), answer=answer(decoded["choices"][0]["message"].get("content")))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                record["error"] = type(exc).__name__
                self.send_error(502, "bounded local provider failure")
            finally:
                record["provider_wall_ms"] = (time.perf_counter() - started) * 1000

    server = ThreadingHTTPServer(("127.0.0.1", 0), Observer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    helper = [sys.executable, "-B", str(Path(__file__).resolve()), "emit", "--output", str(directory),
              "--role", role, "--arm", arm, "--trial-dir", str(dest)]
    if os.name != "nt":
        import shlex
        command = shlex.join(helper)
    else:
        if any(any(c in part for c in "%&|<>^\r\n") for part in helper):
            raise ValueError("shell-sensitive path unsupported")
        command = subprocess.list2cmdline(helper)
    (dest / "commands.txt").write_text("/run " + command + "\n", encoding="utf-8")
    (dest / "prompt.txt").write_text(task["prompt"] + "\nReturn only the requested JSON. Do not edit files or run commands.\n", encoding="utf-8")
    (dest / "empty.yml").write_text("{}\n", encoding="utf-8")
    (dest / "empty.env").write_text("", encoding="utf-8")
    (dest / "model.yml").write_text(json.dumps([{"name": "openai/" + protocol["model"], "edit_format": "ask",
        "extra_params": {"max_tokens": 512, "num_retries": 0, "temperature": 0}}]), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() in {
        "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"}}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    cmd = [sys.executable, "-B", "-m", "aider", "--model", "openai/" + protocol["model"],
        "--openai-api-base", f"http://127.0.0.1:{server.server_port}/v1", "--openai-api-key", "local-key",
        "--model-settings-file", str(dest / "model.yml"), "--config", str(dest / "empty.yml"),
        "--env-file", str(dest / "empty.env"), "--edit-format", "ask", "--map-tokens", "0",
        "--no-git", "--no-auto-commits", "--no-dirty-commits", "--no-stream", "--no-pretty",
        "--no-check-update", "--no-show-release-notes", "--no-analytics", "--no-detect-urls",
        "--no-show-model-warnings", "--yes-always", "--timeout", "75", "--load", str(dest / "commands.txt"),
        "--message-file", str(dest / "prompt.txt"), "--chat-history-file", str(dest / "host-chat.md"),
        "--input-history-file", str(dest / "host-input.txt")]
    started = time.perf_counter()
    result = {"role": role, "arm": arm, "round": round_number, "retry_budget": 0, "interventions": 0}
    try:
        process = subprocess.Popen(cmd, cwd=dest, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = process.communicate(timeout=90)
            result["exit_code"] = process.returncode
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=10)
            else:
                process.kill()
            stdout, stderr = process.communicate(timeout=10)
            result.update(exit_code=None, timeout=True)
        result.update(stdout_sha256=sha(stdout), stderr_sha256=sha(stderr),
                      host_added_output=bool(re.search(rb"Added \d+ lines? of output to the chat", stdout)))
        (dest / "stdout.txt").write_bytes(stdout[:65536])
        (dest / "stderr.txt").write_bytes(stderr[:8192])
    finally:
        result["wall_ms"] = (time.perf_counter() - started) * 1000
        server.shutdown()
        server.server_close()
    result.update(calls=calls, success=bool(result.get("exit_code") == 0 and len(calls) == 1
        and calls[0].get("host_packet_observed_in_model_input") and calls[0].get("answer") == task["expected"]),
        authority="NONE", host_delivery="OBSERVED_IN_ACTUAL_AIDER_MODEL_REQUEST" if any(c.get("host_packet_observed_in_model_input") for c in calls) else "NOT_OBSERVED",
        bridge="NATIVE_EXPLICIT_RUN_COMMAND; NOT_AUTOMATIC_HOOK")
    save(dest / "receipt.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["probe", "prepare", "emit", "run"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--source-ref")
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--source-repository", default="tare.tools.specgraph")
    parser.add_argument("--engine-src", type=Path)
    parser.add_argument("--contract", type=Path)
    parser.add_argument("--tasks", type=Path)
    parser.add_argument("--model", default="qwen38")
    parser.add_argument("--rounds", type=int, choices=[1, 2], default=1)
    parser.add_argument("--role", choices=ROLES)
    parser.add_argument("--arm", choices=["file_context", "hook_context"])
    parser.add_argument("--trial-dir", type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if args.action == "probe":
        result = {"provider": health(), "aider_version": importlib.metadata.version("aider-chat"),
                  "qualification": "AVAILABILITY_ONLY_NO_GENERATION", "authority": "NONE"}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        save(args.output, result)
    elif args.action == "prepare":
        if not all((args.root, args.source_ref, args.tasks, args.contract, args.engine_src)):
            parser.error("prepare requires explicit root, full source-ref, engine-src, tasks and contract")
        sys.path.insert(0, str(args.engine_src.resolve()))
        result = prepare(args)
    elif args.action == "emit":
        if not all((args.role, args.arm, args.trial_dir)):
            parser.error("emit requires role, arm and trial-dir")
        emit(args.output.resolve(), args.role, args.arm, args.trial_dir.resolve())
        return
    else:
        if not args.execute:
            parser.error("run requires --execute; preparation never invokes a model")
        protocol = load_protocol(args.output.resolve())
        records = []
        for number in range(protocol["rounds"]):
            for role in ROLES:
                # Alternate order to reduce consistent warm-up bias.
                for arm in (("file_context", "hook_context") if number % 2 == 0 else ("hook_context", "file_context")):
                    if args.role and args.role != role or args.arm and args.arm != arm:
                        continue
                    records.append(trial(args.output.resolve(), role, arm, number))
                    save(args.output / "results.json", {"records": records, "authority": "NONE",
                        "work_complete": False, "recommendation": "KEEP_ON_DEMAND; full Work residuals remain"})
        result = {"trials": len(records), "passed": sum(r["success"] for r in records), "work_complete": False}
    print(wire(result).decode(), end="")


if __name__ == "__main__":
    main()
