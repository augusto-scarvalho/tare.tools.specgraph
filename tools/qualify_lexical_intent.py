"""Paired baseline/candidate CLI qualification on the frozen Graphify corpora.

No model calls. Run once per fresh --state; source copies and outputs are hashed.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import platform
import shutil
import statistics
import sys
from types import SimpleNamespace

import compare_graphify as bench


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--specgraph-repo", type=Path, required=True)
    parser.add_argument("--os-repo", type=Path, required=True)
    parser.add_argument("--baseline", default="0e9efb34ee7e79c919c8bc34d7ca32cd4c83409c")
    args = parser.parse_args()
    base = args.state.resolve()
    if base.exists():
        raise ValueError("Use a fresh state directory; measured runs are immutable")
    bench.prepare(SimpleNamespace(state=base / "inputs", specgraph_repo=args.specgraph_repo, os_repo=args.os_repo))
    tasks = json.loads((base / "inputs/tasks.json").read_text())
    integrity = {}
    for arm in ("baseline", "candidate"):
        dst = base / "engines" / arm / "src"
        dst.mkdir(parents=True)
        if arm == "baseline":
            paths = bench.git(args.specgraph_repo, "ls-tree", "-r", "--name-only", args.baseline).decode().splitlines()
            for path in paths:
                if path.startswith("src/specgraph/") and path.endswith(".py"):
                    target = dst / Path(path).relative_to("src")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bench.git(args.specgraph_repo, "show", f"{args.baseline}:{path}"))
        else:
            shutil.copytree(args.specgraph_repo / "src/specgraph", dst / "specgraph", ignore=shutil.ignore_patterns("__pycache__"))
        integrity[arm] = {p.relative_to(dst).as_posix(): bench.sha(p.read_bytes()) for p in sorted(dst.rglob("*.py"))}
    bench.write_json(base / "source-integrity.json", integrity)
    bench.write_json(base / "protocol.json", {
        "schema": "tare.tools/lexical-intent-qualification/1", "baseline_commit": args.baseline,
        "candidate": "Exact Python module bytes in source-integrity.json; working tree before delivery commit",
        "samples": "Two rounds of 100 complete CLI calls per arm; alternating order; no discarded observations",
        "budget": {"max_bytes": 8192, "limit": 10, "model_calls": 0},
        "corpora": "Same frozen source bytes as original local Graphify comparison; see inputs/protocol.json for corpus preparation only. Graphify is NOT executed in this run.",
        "limitations": ["Author-selected tasks reused for diagnosis, not blind or representative", "SG corpus retains mapping errors; OS and fixture READY", "One WSL host, OS cache uncontrolled", "No agent task success or total token savings measured"],
        "environment": {"python": sys.version, "platform": platform.platform()},
        "runner_sha256": bench.sha(Path(__file__).read_bytes()),
        "tasks_sha256": bench.sha((base / "inputs/tasks.json").read_bytes()),
        "source_integrity_sha256": bench.sha((base / "source-integrity.json").read_bytes()),
    })
    def env(arm):
        value = bench.clean_env(base)
        value.update({"PYTHONPATH": str(base / "engines" / arm / "src"),
                      "TARE_SPECGRAPH_STATE_DIR": str(base / "state" / arm)})
        return value
    for arm in ("baseline", "candidate"):
        for corpus in ("specgraph", "os", "fixture"):
            root = base / "inputs/corpora" / corpus
            result = bench.measured([sys.executable, "-m", "specgraph.cli", "--root", str(root), "index", "--format", "json"], root, env(arm))
            bench.write_json(base / "builds" / f"{arm}-{corpus}.json", result)
            metadata = json.loads(result["stdout"])
            assert metadata["status"] in ("READY", "PARTIAL"), result
    rows = []
    (base / "outputs").mkdir()
    with (base / "samples.jsonl").open("x", encoding="utf-8") as log:
        for round_number in (1, 2):
            for i in range(100):
                task = tasks[i % len(tasks)]
                root = base / "inputs/corpora" / task["corpus"]
                for arm in (("baseline", "candidate") if (i + round_number) % 2 else ("candidate", "baseline")):
                    result = bench.measured(bench.query_argv("specgraph", root, task["query"]), root, env(arm))
                    evaluation = bench.score("specgraph", result, task)
                    digest = bench.sha(result["stdout"].encode())
                    (base / "outputs" / f"{digest}.txt").write_text(result["stdout"], encoding="utf-8")
                    result.pop("stdout")
                    row = result | {"arm": arm, "round": round_number, "sample": i + 1,
                                    "task": task["id"], "kind": task["kind"], "score": evaluation, "output_sha256": digest}
                    log.write(json.dumps(row, ensure_ascii=False) + "\n")
                    log.flush()
                    rows.append(row)
                if i % 25 == 0:
                    print(f"round={round_number} paired_samples={i+1}", flush=True)
    bench.verify_frozen(base / "inputs")
    summary = {}
    for arm in ("baseline", "candidate"):
        selected = [r for r in rows if r["arm"] == arm]
        summary[arm] = {"rounds": {}, "tasks": {}}
        for round_number in (1, 2):
            sample = [r for r in selected if r["round"] == round_number]
            latency = sorted(r["wall_ms"] for r in sample)
            summary[arm]["rounds"][str(round_number)] = {
                "count": len(sample), "p50_ms": statistics.median(latency), "p95_ms": latency[math.ceil(.95 * len(latency))-1],
                "max_ms": max(latency), "mean_bytes": statistics.mean(r["stdout_bytes"] for r in sample),
                "budget_breaches": sum(r["stdout_bytes"] > 8192 for r in sample),
                "invalid": sum(not r["score"]["valid"] for r in sample),
                "by_class": {kind: {"count": len(group), "p50_ms": statistics.median(r["wall_ms"] for r in group)}
                             for kind in ("exact", "language", "negative")
                             if (group := [r for r in sample if r["kind"] == kind])}}
        for task in tasks:
            samples = [r for r in selected if r["task"] == task["id"]]
            summary[arm]["tasks"][task["id"]] = {"kind": task["kind"], "consistent": all(r["score"] == samples[0]["score"] for r in samples), **samples[0]["score"]}
    bench.write_json(base / "summary.json", summary)
    print(json.dumps({arm: data["rounds"] for arm, data in summary.items()}, indent=2))


if __name__ == "__main__":
    main()
