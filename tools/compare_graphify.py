"""Reproduce the pinned, zero-model retrieval comparison (no product dependencies).

Install both pinned projects in an isolated Python environment before running.
Preparation freezes corpus bytes and source-derived answers before any query runs.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time

SG_REV = "0bf8add8e41b86c89aa8488861a90eaf2fc565e7"
OS_REV = "06ce10855d4c7d6e33f13784a06c5eb6dffab0b9"
GF_REV = "67f99bd0059dd1bac9e44382907ef9f10098b39f"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


FIXTURE = {
    "src/demo/__init__.py": "",
    "src/demo/leaf.py": 'def leaf():\n    """Return the invoice checksum."""\n    return 41\n',
    "src/demo/other.py": 'def leaf():\n    """Unrelated duplicate name; never called by entry."""\n    return -1\n',
    "src/demo/middle.py": "from .leaf import leaf as checksum\n\ndef middle():\n    return checksum()\n",
    "src/demo/main.py": "from .middle import middle\n\ndef entry():\n    return middle()\n\ndef dynamic(name):\n    return globals()[name]()\n",
    "src/ts/leaf.ts": "export function checksum(): number { return 41; }\n",
    "src/ts/other.ts": "export function checksum(): number { return -1; }\n",
    "src/ts/main.ts": "import { checksum as compute } from './leaf';\nexport function entry(): number { return compute(); }\n",
    "src/rust/leaf.rs": "pub fn checksum() -> i32 { 41 }\n",
    "src/rust/main.rs": "mod leaf;\nuse leaf::checksum as compute;\npub fn entry() -> i32 { compute() }\n",
    "docs/specs/SPEC-INVOICE.md": '---\nid: SPEC-INVOICE\ntitle: Invoice checksum\nstatus: ACCEPTED\ntarget_symbols: ["src/demo/leaf.py:leaf"]\nacceptance_criteria:\n  AC-01: The checksum returns forty-one.\n---\n# Invoice checksum\nThe invoice checksum prevents duplicate billing.\n',
    "docs/adr/ADR-INVOICE.md": '---\nid: ADR-INVOICE\ntitle: Deterministic invoice checksums\nstatus: ACCEPTED\ngoverned_specs: [SPEC-INVOICE]\n---\n# Decision\nUse a deterministic checksum so billing retries are idempotent.\n',
}

# Each natural-language task has an exact-name counterpart and a source oracle.
# Defined before querying either engine; no answer judging by an LLM.
SEARCH_TASKS = [
    ("specgraph", "publish", "src/specgraph/indexer.py", "index_repository", "Publish a complete read model or leave the previous revision intact"),
    ("specgraph", "query", "src/specgraph/query.py", "project_query", "Recover code facts without rebuilding opening sources or invoking LLMs"),
    ("specgraph", "identity", "src/specgraph/identity.py", "python_identities", "Python identity content addressed symbol qualified scope"),
    ("specgraph", "resolution", "src/specgraph/resolver.py", "resolve_references", "resolve imported aliases across Python modules"),
    ("os", "descriptor", "relay/work_run.py", "derive_descriptor", "Descriptor payload from the Work node explicit inputs only"),
    ("os", "checkout", "relay/work_run.py", "refresh_checkout", "Refresh tracked checkout files without overwriting local edits"),
    ("os", "crawl", "relay/research.py", "collect", "Breadth first crawl into corpus and manifest no model involved"),
    ("os", "admission", "relay/landing_policy.py", "evaluate_admission", "Evaluate immutable evidence without provider calls or repository mutation"),
    ("fixture", "invoice", "src/demo/leaf.py", "leaf", "invoice checksum"),
]


def definition(root: Path, path: str, name: str) -> dict:
    raw = (root / path).read_bytes()
    tree = ast.parse(raw)
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name]
    if len(nodes) != 1:
        raise ValueError(f"Oracle is not unique: {path}:{name}")
    n = nodes[0]
    return {"file": path, "name": name, "start": n.lineno, "end": n.end_lineno,
            "sha256": sha(raw), "source": raw.decode().splitlines()[n.lineno - 1:n.end_lineno]}


def prepare(args) -> None:
    base = args.state.resolve()
    if (base / "protocol.json").exists():
        raise ValueError("Preparation is immutable; use a fresh state directory")
    manifests = {}
    for corpus, repo, rev in [("specgraph", args.specgraph_repo, SG_REV), ("os", args.os_repo, OS_REV)]:
        paths = git(repo, "ls-tree", "-r", "--name-only", rev).decode().splitlines()
        if corpus == "specgraph":
            paths = [p for p in paths if (p.startswith("src/specgraph/") and p.endswith(".py")) or
                     (p.startswith(("docs/specs/", "docs/adr/")) and p.endswith(".md")) or
                     p in ("docs/ADR-001_SPECGRAPH_NORTH_STAR.md", "docs/SPEC-SPECGRAPH-001.md", "planning/traceability-mappings.json")]
        else:
            paths = [p for p in paths if p.startswith(("relay/", "tools/")) and p.endswith(".py")
                     and "/history/" not in p and "/tests/" not in p and not Path(p).name.startswith("test_")]
        root = base / "corpora" / corpus
        manifest = {}
        for path in paths:
            raw = git(repo, "show", f"{rev}:{path}")
            dst = root / path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(raw)
            manifest[path] = {"sha256": sha(raw), "bytes": len(raw)}
        manifests[corpus] = {"revision": rev, "files": manifest}
    root = base / "corpora/fixture"
    for path, body in FIXTURE.items():
        dst = root / path
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(body, encoding="utf-8")
    manifests["fixture"] = {"revision": "embedded-controlled-fixture-v1", "files": {
        p: {"sha256": sha(v.encode()), "bytes": len(v.encode())} for p, v in FIXTURE.items()}}
    for corpus in manifests:
        root = base / "corpora" / corpus
        cfg = {"indexed_paths": ["relay", "tools"] if corpus == "os" else ["src"],
               "governed_paths": [], "python_source_roots": ["src", "."],
               "spec_paths": ["docs/specs", "docs/SPEC-SPECGRAPH-001.md"],
               "adr_paths": ["docs/adr", "docs/ADR-001_SPECGRAPH_NORTH_STAR.md"],
               "search_paths": ["docs"], "test_paths": [], "schema_paths": [], "api_paths": [],
               "excluded_paths": [".git", "graphify-out", ".specgraph", "__pycache__"],
               "intent_mapping_paths": ["planning/traceability-mappings.json"] if corpus == "specgraph" else []}
        write_json(root / "specgraph.yaml", cfg)  # JSON is valid YAML; identical file visible to both.
        raw = (root / "specgraph.yaml").read_bytes()
        manifests[corpus]["files"]["specgraph.yaml"] = {"sha256": sha(raw), "bytes": len(raw)}
    tasks = []
    for corpus, task, path, name, question in SEARCH_TASKS:
        oracle = definition(base / "corpora" / corpus, path, name)
        for mode, query in [("exact", name), ("language", question)]:
            tasks.append({"id": f"{corpus}-{task}-{mode}", "corpus": corpus, "kind": mode,
                          "query": query, "expected": [oracle]})
    for corpus in manifests:
        tasks.append({"id": f"{corpus}-negative", "corpus": corpus, "kind": "negative",
                      "query": "quasar_unicorn_missing_7f28c1", "expected": []})
    write_json(base / "corpus-manifest.json", manifests)
    write_json(base / "tasks.json", tasks)
    write_json(base / "protocol.json", {
        "schema": "tare.tools/specgraph-graphify-comparison/1", "phase": "LOCAL_RETRIEVAL_ZERO_MODEL",
        "versions": {"specgraph": SG_REV, "os_corpus": OS_REV, "graphify": GF_REV},
        "budget": {"model_calls": 0, "model_tokens": 0, "max_native_output_bytes": 8192,
                   "graphify_native_budget_estimated_tokens": 2000, "timeout_seconds": 60},
        "quality": "Source-file AND symbol on one delivered result; definition range when the native output carries it. Diagnostic coverage, not exhaustive precision or QA accuracy.",
        "latency": "100 complete CLI calls per engine per round, two rounds; alternating engine order; first sample, misses, failures and startup included",
        "tokens": "cl100k_base output token count, measurement proxy; no claim of total agent savings",
        "scope": "Same corpus bytes and host. Graphify --code-only --no-cluster, SpecGraph configured code+docs. Document intent is a separate capability observation, not an overall Graphify failure.",
        "limitations": ["One WSL host, native Linux filesystem, OS cache uncontrolled", "No agent task success measured in this phase", "No semantic model backend, media, SCIP or hosted Graphify platform evaluated", "Oracle includes tool-author-selected cases, not a blind or representative benchmark", "Native output formats and budget units differ; actual byte breaches reported, never silently clipped"],
        "tasks_sha256": sha((base / "tasks.json").read_bytes()),
        "corpus_manifest_sha256": sha((base / "corpus-manifest.json").read_bytes())})
    print(json.dumps({k: len(v["files"]) for k, v in manifests.items()}))


def clean_env(base: Path) -> dict:
    env = dict(os.environ)
    # Never let auto-detected provider credentials enable an unplanned semantic pass.
    for key in list(env):
        if key.endswith(("API_KEY", "AUTH_TOKEN")) or key.startswith(("GRAPHIFY_", "TARE_SPECGRAPH_")):
            env.pop(key)
    env.update({"GRAPHIFY_NO_TIPS": "1", "PYTHONHASHSEED": "0",
                "TARE_SPECGRAPH_STATE_DIR": str(base / "sg-state")})
    return env


def measured(argv: list[str], cwd: Path, env: dict, timeout=60) -> dict:
    start = time.perf_counter_ns()
    try:
        cp = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=timeout)
        stdout, stderr, code, status = cp.stdout, cp.stderr, cp.returncode, "COMPLETED"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr, code, status = exc.stdout or b"", exc.stderr or b"", None, "TIMEOUT"
    return {"argv": argv, "wall_ms": (time.perf_counter_ns() - start) / 1e6,
            "returncode": code, "execution": status, "stdout": stdout.decode("utf-8", "replace"),
            "stderr": stderr.decode("utf-8", "replace"), "stdout_bytes": len(stdout)}


def query_argv(engine: str, root: Path, query: str) -> list[str]:
    if engine == "specgraph":
        return [sys.executable, "-m", "specgraph.cli", "--root", str(root), "query", query,
                "--limit", "10", "--max-bytes", "8192", "--format", "json"]
    return [sys.executable, "-m", "graphify", "query", query, "--budget", "2000"]


def delivered_nodes(engine: str, text: str) -> list[dict]:
    if engine == "specgraph":
        value = json.loads(text)
        if "results" not in value or "status" not in value:
            raise ValueError("Missing SpecGraph result envelope")
        if value["status"] not in ("HIT", "PARTIAL", "UNKNOWN", "NOT_FOUND_IN_SNAPSHOT", "AMBIGUOUS"):
            raise ValueError("SpecGraph returned an error status")
        return [{"file": n.get("file_path"), "name": n.get("name"),
                 "start": n.get("start_line"), "end": n.get("end_line"),
                 "sha256": n.get("source", {}).get("sha256")} for n in value["results"]]
    if "No matching nodes found." in text:
        return []
    if "Traversal:" not in text:
        raise ValueError("Missing Graphify result envelope")
    nodes = []
    for line in text.splitlines():
        m = re.fullmatch(r"NODE (.*?) \[src=(.*?) loc=L(\d+)(?:[^ ]*) community=.*\]", line)
        if m:
            label, path, start = m.groups()
            nodes.append({"file": path, "name": label.removesuffix("()"), "start": int(start)})
    if not nodes:
        raise ValueError("Graphify traversal had no parseable source witnesses")
    return nodes


def score(engine: str, result: dict, task: dict) -> dict:
    if result["execution"] != "COMPLETED" or result["returncode"] not in (0, 1):
        return {"valid": False, "reason": result["execution"], "success": False}
    if engine == "graphify" and result["returncode"] != 0:
        return {"valid": False, "reason": "CLI_ERROR", "success": False}
    try:
        nodes = delivered_nodes(engine, result["stdout"])
    except (ValueError, TypeError, KeyError) as exc:
        return {"valid": False, "reason": str(exc), "success": False}
    ranks = []
    for expected in task["expected"]:
        rank = next((i + 1 for i, node in enumerate(nodes)
                     if node["file"] == expected["file"] and node["name"] == expected["name"]
                     and node["start"] == expected["start"]), None)
        ranks.append(rank)
    success = not nodes if task["kind"] == "negative" else all(r is not None for r in ranks)
    return {"valid": True, "success": success, "witness_ranks": ranks,
            "delivered_nodes": len(nodes), "budget_met": result["stdout_bytes"] <= 8192,
            "success_within_budget": success and result["stdout_bytes"] <= 8192,
            "first_five_witness": bool(ranks) and all(r is not None and r <= 5 for r in ranks)}


def verify_frozen(base: Path) -> None:
    protocol = json.loads((base / "protocol.json").read_text())
    for name, key in [("tasks.json", "tasks_sha256"), ("corpus-manifest.json", "corpus_manifest_sha256")]:
        if sha((base / name).read_bytes()) != protocol[key]:
            raise ValueError(f"Frozen contract changed: {name}")
    manifests = json.loads((base / "corpus-manifest.json").read_text())
    for corpus, manifest in manifests.items():
        for name, expected in manifest["files"].items():
            if sha((base / "corpora" / corpus / name).read_bytes()) != expected["sha256"]:
                raise ValueError(f"Corpus changed: {corpus}/{name}")


def run_queries(args) -> None:
    import importlib.metadata
    import tiktoken
    base = args.state.resolve()
    verify_frozen(base)
    verify_engine_sources(base, args.graphify_repo)
    if (base / "samples.jsonl").exists():
        raise ValueError("Never overwrite a measured run")
    tasks = json.loads((base / "tasks.json").read_text())
    env = clean_env(base)
    tokenizer = tiktoken.get_encoding("cl100k_base")
    write_json(base / "environment.json", {"platform": platform.platform(), "python": sys.version,
               "cpu_count": os.cpu_count(), "cpuinfo": Path("/proc/cpuinfo").read_text().split("\n\n")[0] if Path("/proc/cpuinfo").exists() else None,
               "packages": sorted(f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()),
               "graphify_source": GF_REV, "specgraph_source": SG_REV,
               "runner_sha256": sha(Path(__file__).read_bytes()),
               "tokenizer": {"package": importlib.metadata.version("tiktoken"), "encoding": "cl100k_base"},
               "cache": "Smoke query leaf on fixture precedes measured runs; no deliberate prewarm of other corpora; OS cache uncontrolled"})
    with (base / "samples.jsonl").open("x", encoding="utf-8") as log:
        for round_number in (1, 2):
            for i in range(100):
                task = tasks[i % len(tasks)]
                root = base / "corpora" / task["corpus"]
                order = ["specgraph", "graphify"] if (round_number + i) % 2 else ["graphify", "specgraph"]
                for engine in order:
                    result = measured(query_argv(engine, root, task["query"]), root, env)
                    output_id = sha(result["stdout"].encode())
                    (base / "outputs").mkdir(exist_ok=True)
                    (base / "outputs" / f"{output_id}.txt").write_text(result["stdout"], encoding="utf-8")
                    evaluation = score(engine, result, task)
                    row = {**result, "round": round_number, "sample": i + 1, "engine": engine,
                           "task": task["id"], "corpus": task["corpus"], "kind": task["kind"],
                           "output_sha256": output_id, "output_tokens": len(tokenizer.encode(result["stdout"])),
                           "score": evaluation}
                    row.pop("stdout")
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                if i % 20 == 0:
                    print(f"round {round_number}: {i + 1}/100 paired samples", flush=True)
    verify_frozen(base)
    summarize(base)


def summarize(base: Path) -> dict:
    rows = [json.loads(line) for line in (base / "samples.jsonl").read_text().splitlines()]
    expected = {(round_number, sample, engine) for round_number in (1, 2)
                for sample in range(1, 101) for engine in ("specgraph", "graphify")}
    if len(rows) != 400 or {(r['round'], r['sample'], r['engine']) for r in rows} != expected:
        raise ValueError("Incomplete or duplicate sample set; no qualified aggregate")
    summary = {"schema": "tare.tools/specgraph-graphify-results/1", "samples": len(rows), "engines": {}}
    for engine in ("specgraph", "graphify"):
        subset = [r for r in rows if r["engine"] == engine]
        rounds = []
        for number in (1, 2):
            rr = [r for r in subset if r["round"] == number]
            times = sorted(r["wall_ms"] for r in rr)
            rounds.append({"round": number, "samples": len(rr), "p50_ms": statistics.median(times),
                           "p95_ms": times[math.ceil(.95 * len(times)) - 1], "max_ms": max(times),
                           "mean_output_tokens": statistics.mean(r["output_tokens"] for r in rr),
                           "mean_output_bytes": statistics.mean(r["stdout_bytes"] for r in rr),
                           "errors": sum(not r["score"]["valid"] for r in rr),
                           "budget_breaches": sum(r["stdout_bytes"] > 8192 for r in rr)})
        by_task = {}
        for row in subset:
            by_task.setdefault(row["task"], []).append(row)
        quality = []
        for tid, runs in by_task.items():
            quality.append({"task": tid, "kind": runs[0]["kind"], "corpus": runs[0]["corpus"],
                            "observations": len(runs), "all_success": all(r["score"]["success"] for r in runs),
                            "all_within_budget": all(r["score"].get("success_within_budget", False) for r in runs),
                            "scores_consistent": len({json.dumps(r["score"], sort_keys=True) for r in runs}) == 1,
                            "first_score": runs[0]["score"], "output_sha256": runs[0]["output_sha256"]})
        summary["engines"][engine] = {"rounds": rounds, "quality": quality}
    write_json(base / "summary.json", summary)
    return summary


def snapshot(base: Path, corpus: str, engine: str) -> dict:
    root = base / "corpora" / corpus
    if engine == "graphify":
        return json.loads((root / "graphify-out/graph.json").read_text())
    from specgraph.storage import RepositoryIndex
    with RepositoryIndex(root, state_dir=base / "sg-state") as index:
        return index.snapshot_payload()


def verify_engine_sources(base: Path, graphify_repo: Path | None) -> None:
    """Do not label arbitrary installed packages with the pinned revision constants."""
    import graphify
    import specgraph
    if graphify_repo is None:
        raise ValueError("--graphify-repo is required to verify the installed engine bytes")
    actual_revision = git(graphify_repo, "rev-parse", "HEAD").decode().strip()
    if actual_revision != GF_REV:
        raise ValueError("Graphify source checkout does not match the protocol pin")
    expected_sg = json.loads((base / "corpus-manifest.json").read_text())["specgraph"]["files"]
    observed = []
    sg_root = Path(specgraph.__file__).parent
    for name, source in expected_sg.items():
        if name.startswith("src/specgraph/") and name.endswith(".py"):
            actual = sha((sg_root / name.removeprefix("src/specgraph/")).read_bytes())
            if actual != source["sha256"]:
                raise ValueError(f"Installed SpecGraph differs from the measured source pin: {name}")
            observed.append({"engine": "specgraph", "path": name, "sha256": actual})
    gf_root = Path(graphify.__file__).parent
    names = git(graphify_repo, "ls-tree", "-r", "--name-only", GF_REV, "graphify").decode().splitlines()
    for name in names:
        if name.endswith(".py"):
            expected = sha(git(graphify_repo, "show", f"{GF_REV}:{name}"))
            actual = sha((gf_root / name.removeprefix("graphify/")).read_bytes())
            if actual != expected:
                raise ValueError(f"Installed Graphify differs from the measured source pin: {name}")
            observed.append({"engine": "graphify", "path": name, "sha256": actual})
    write_json(base / "engine-source-integrity.json", {"status": "PASS", "files": observed,
        "scope": "Installed Python module bytes versus pinned Git source; dependencies listed separately in environment.json"})


def call_edges(engine: str, graph: dict) -> list[dict]:
    if engine == "specgraph":
        return [e for e in graph["edges"] if e["type"] == "DEPENDS_ON_STATIC" and e.get("reference_kind") == "call"]
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = []
    for edge in graph.get("edges", graph.get("links", [])):
        if edge.get("relation") != "calls":
            continue
        ends = []
        for key, marker in [("source", "_src"), ("target", "_tgt")]:
            n = nodes.get(edge.get(marker, edge[key]), {})
            ends.append(f"{n.get('source_file')}:{n.get('label', '').removesuffix('()')}")
        edges.append({"from": ends[0], "to": ends[1], "native": edge})
    return edges


def build_all(args) -> None:
    base = args.state.resolve()
    verify_frozen(base)
    for corpus in ("fixture", "specgraph", "os"):
        root = base / "corpora" / corpus
        for engine in ("graphify", "specgraph"):
            dst = base / "builds" / f"{corpus}-{engine}.json"
            if dst.exists():
                raise ValueError("Build receipt exists; use a fresh prepared run")
            argv = ([sys.executable, "-m", "graphify", "extract", ".", "--code-only", "--no-cluster", "--timing"]
                    if engine == "graphify" else
                    [sys.executable, "-m", "specgraph.cli", "--root", str(root), "index", "--format", "json"])
            actual = measured(argv, root, clean_env(base), 600)
            write_json(dst, actual)
            print(corpus, engine, actual["returncode"], round(actual["wall_ms"]), flush=True)


def structure_and_updates(args) -> None:
    base = args.state.resolve()
    verify_frozen(base)
    out = base / "structure"
    if out.exists():
        raise ValueError("Structural observation exists; do not overwrite")
    positives = [
        ("src/demo/main.py:entry", "src/demo/middle.py:middle"),
        ("src/demo/middle.py:middle", "src/demo/leaf.py:leaf"),
        ("src/ts/main.ts:entry", "src/ts/leaf.ts:checksum"),
        ("src/rust/main.rs:entry", "src/rust/leaf.rs:checksum"),
    ]
    negatives = [
        ("src/demo/middle.py:middle", "src/demo/other.py:leaf"),
        ("src/ts/main.ts:entry", "src/ts/other.ts:checksum"),
        ("src/demo/main.py:dynamic", "src/demo/leaf.py:leaf"),
        ("src/demo/leaf.py:leaf", "src/demo/middle.py:middle"),
    ]
    write_json(out / "oracle.json", {"positive_calls": positives, "forbidden_calls": negatives,
        "provenance": "Explicit source calls/imports of the prepared fixture; no graph-based answer generation",
        "not_blind": "Call oracle recorded after fixture smoke observation; diagnostic, not preregistered independent evaluation",
        "update_plan": ["no change", "add new function file", "rename function in that file", "delete that file"],
        "rounds": 2})
    results = {}
    for engine in ("specgraph", "graphify"):
        graph = snapshot(base, "fixture", engine)
        write_json(out / f"{engine}-snapshot.json", graph)
        edges = call_edges(engine, graph)
        observed = {(e["from"], e["to"]) for e in edges}
        results[engine] = {"positive_calls": [{"from": a, "to": b, "observed": (a, b) in observed} for a, b in positives],
                           "forbidden_calls": [{"from": a, "to": b, "incorrectly_observed": (a, b) in observed} for a, b in negatives],
                           "native_calls": edges}
        root = base / "corpora/fixture"
        selectors = (("src/demo/main.py:entry", "src/demo/leaf.py:leaf") if engine == "specgraph"
                     else ("src_demo_main_entry", "src_demo_leaf_leaf"))
        for direction, ends in [("forward", selectors), ("reverse", selectors[::-1])]:
            argv = ([sys.executable, "-m", "specgraph.cli", "--root", str(root), "path", *ends, "--format", "json"]
                    if engine == "specgraph" else [sys.executable, "-m", "graphify", "path", *ends, "--directed"])
            write_json(out / f"{engine}-path-{direction}.json", measured(argv, root, clean_env(base)))
        if engine == "specgraph":
            results[engine]["intent_edges"] = [e for e in graph["edges"] if e["type"] in ("CODE_IMPLEMENTS_SPEC", "SPEC_GOVERNED_BY_ADR")]
        else:
            results[engine]["document_intent"] = "NOT_EVALUATED: --code-only excludes semantic document pass"
    write_json(out / "results.json", results)
    updates = []
    root = base / "corpora/fixture"
    added = root / "src/demo/update_probe.py"
    if added.exists():
        raise ValueError("Update probe unexpectedly exists")
    try:
        for number in (1, 2):
            for step in ("noop", "add", "rename", "delete"):
                if step == "add":
                    added.write_text("def added_probe():\n    return 1\n", encoding="utf-8")
                elif step == "rename":
                    added.write_text("def renamed_probe():\n    return 2\n", encoding="utf-8")
                elif step == "delete":
                    added.unlink()
                for engine in ("specgraph", "graphify"):
                    argv = ([sys.executable, "-m", "specgraph.cli", "--root", str(root), "index", "--format", "json"]
                            if engine == "specgraph" else [sys.executable, "-m", "graphify", "update", ".", "--no-cluster"])
                    actual = measured(argv, root, clean_env(base), 600)
                    graph = snapshot(base, "fixture", engine)
                    native = (graph["symbols"] if engine == "specgraph" else graph["nodes"])
                    relevant = [n for n in native if n.get("file_path", n.get("source_file")) == "src/demo/update_probe.py"]
                    names = {n.get("name", n.get("label", "").removesuffix("()")) for n in relevant}
                    expected = {"added_probe"} if step == "add" else {"renamed_probe"} if step == "rename" else set()
                    observed = names & {"added_probe", "renamed_probe"}
                    updates.append({"round": number, "step": step, "engine": engine,
                                    "expected": sorted(expected), "observed": sorted(observed),
                                    "membership_correct": expected == observed, "native_nodes": relevant, **actual})
                    write_json(out / "updates.json", updates)
                print(f"updates round {number}: {step}", flush=True)
    finally:
        if added.exists():
            added.unlink()
    write_json(out / "updates.json", updates)
    verify_frozen(base)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["prepare", "build", "queries", "summarize", "structure", "verify-engines"])
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--specgraph-repo", type=Path)
    p.add_argument("--os-repo", type=Path)
    p.add_argument("--graphify-repo", type=Path)
    args = p.parse_args()
    if args.action == "prepare":
        prepare(args)
    elif args.action == "build":
        build_all(args)
    elif args.action == "queries":
        run_queries(args)
    elif args.action == "structure":
        structure_and_updates(args)
    elif args.action == "verify-engines":
        verify_engine_sources(args.state.resolve(), args.graphify_repo)
    else:
        summarize(args.state.resolve())


if __name__ == "__main__":
    main()
