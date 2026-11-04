"""Reproducible two-round alias/publication diagnostics, without source vendoring.

Run with a fresh native --state. The real corpus is git-show extracted at a fixed
revision; only derived facts, hashes and measurements are publishable artifacts.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from time import perf_counter

BASELINE = "5dbc1014806d812551beb0869f0bf7e625f87a1a"
REAL_REVISION = "60942fc49cc0f08277bab64d3f310bce49b66e71"
POSITIVES = [
    ("src/demo/main.py:entry", "src/demo/middle.py:middle"),
    ("src/demo/middle.py:middle", "src/demo/leaf.py:leaf"),
    ("src/ts/main.ts:entry", "src/ts/leaf.ts:checksum"),
    ("src/rust/main.rs:entry", "src/rust/leaf.rs:checksum"),
]
FORBIDDEN = [
    ("src/demo/middle.py:middle", "src/demo/other.py:leaf"),
    ("src/ts/main.ts:entry", "src/ts/other.ts:checksum"),
    ("src/demo/main.py:dynamic", "src/demo/leaf.py:leaf"),
    ("src/demo/leaf.py:leaf", "src/demo/middle.py:middle"),
]
STAGES = ["full", "noop", "unexport", "restore_exports", "rename", "restore_names", "delete"]
CONFIG = b"project_name: aliases-qualification\nindexed_paths: [src]\nspec_paths: [docs/specs]\nadr_paths: [docs/adr]\n"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def worker(root, state, stage, corpus):
    from specgraph.indexer import index_repository
    from specgraph.storage import RepositoryIndex
    from specgraph.extractors import tree_sitter
    rebind_ms = []
    parsed_multilanguage_files = []
    original_parse = tree_sitter.TreeSitterCodeParser.parse_file
    def observed_parse(cls, path, root_dir="."):
        parsed_multilanguage_files.append(Path(path).resolve().relative_to(Path(root_dir).resolve()).as_posix())
        return original_parse(path, root_dir)
    tree_sitter.TreeSitterCodeParser.parse_file = classmethod(observed_parse)
    if hasattr(tree_sitter, "bind_imports"):
        binder = tree_sitter.bind_imports
        def timed(symbols):
            start = perf_counter()
            try:
                return binder(symbols)
            finally:
                rebind_ms.append((perf_counter() - start) * 1000)
        tree_sitter.bind_imports = timed
    started = perf_counter()
    metadata = index_repository(root, state_dir=state, incremental=stage != "full", reuse_snapshot=stage == "noop")
    publication_ms = (perf_counter() - started) * 1000
    with RepositoryIndex(root, state_dir=state) as index:
        snapshot = index.snapshot_payload()
    references = [{"owner": s["symbol_id"], "source_file": s["file_path"], "language": s["language"], **r}
                  for s in snapshot["symbols"] for r in s.get("references", [])]
    if corpus == "real":
        for reference in references:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:\.|::)[A-Za-z_][A-Za-z0-9_]*)*", reference["name"]):
                reference["name_sha256"] = sha(reference["name"].encode())
                reference["name"] = None
                reference["name_redacted"] = "Complex callee expression retained only as hash; no GPL source snippets in published evidence"
    calls = [e for e in snapshot["edges"] if e["type"] == "DEPENDS_ON_STATIC"]
    facts = {"symbol_count": len(snapshot["symbols"]), "reference_count": len(references),
             "reference_statuses": dict(Counter(r["status"] for r in references)),
             "reference_reasons": dict(Counter(r["reason"] for r in references)),
             "crossfile_calls": sum(e.get("resolution_reason") == "declared_crossfile_import" for e in calls),
             "samefile_calls": sum(e.get("resolution_reason") == "unshadowed_same_file_function" for e in calls)}
    print(json.dumps({"metadata": metadata, "publication_ms": publication_ms,
                      "crossfile_rebind_ms": rebind_ms, "facts": facts, "call_edges": calls,
                      "references": references, "parsed_multilanguage_files": parsed_multilanguage_files}, ensure_ascii=False))


def manifest(root):
    return {p.relative_to(root).as_posix(): {"sha256": sha(p.read_bytes()), "bytes": p.stat().st_size}
            for p in sorted(root.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--specgraph-repo", type=Path, required=True)
    parser.add_argument("--real-repo", type=Path, required=True)
    args = parser.parse_args()
    base = args.state.resolve()
    if base.exists():
        raise ValueError("Fresh state required: existing receipts must not be overwritten")
    base.mkdir(parents=True)
    import compare_graphify
    fixture = base / "frozen/fixture"
    for path, source in compare_graphify.FIXTURE.items():
        target = fixture / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    (fixture / "specgraph.yaml").write_bytes(CONFIG)
    real = base / "frozen/real"
    real.mkdir(parents=True)
    real_paths = git(args.real_repo, "ls-tree", "-r", "--name-only", REAL_REVISION).decode().splitlines()
    for path in real_paths:
        if path.startswith("src/") and path.endswith(".rs"):
            target = real / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git(args.real_repo, "show", f"{REAL_REVISION}:{path}"))
    (real / "specgraph.yaml").write_bytes(CONFIG)
    integrity = {}
    for arm in ("baseline", "candidate"):
        destination = base / "engines" / arm / "src/specgraph"
        destination.mkdir(parents=True)
        if arm == "baseline":
            for path in git(args.specgraph_repo, "ls-tree", "-r", "--name-only", BASELINE).decode().splitlines():
                if path.startswith("src/specgraph/") and path.endswith(".py"):
                    target = base / "engines" / arm / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(git(args.specgraph_repo, "show", f"{BASELINE}:{path}"))
        else:
            shutil.copytree(args.specgraph_repo / "src/specgraph", destination, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        integrity[arm] = manifest(destination)
    write(base / "source-integrity.json", integrity)
    frozen = {"fixture": manifest(fixture), "real": manifest(real)}
    write(base / "corpus-manifest.json", frozen)
    protocol = {"schema": "tare.tools/crossfile-aliases-qualification/1", "baseline_revision": BASELINE,
                "real_repository": "https://github.com/augusto-scarvalho/image-curation-gui",
                "real_revision": REAL_REVISION, "real_license": "GPL-3.0; sources stay in local run state, not evidence artifacts",
                "real_scope": "All twelve pinned src/**/*.rs bytes; generated scope config, no source edits, no compilation",
                "fixture": "Original compare_graphify.FIXTURE bytes plus generated scope config",
                "oracle": {"positives": POSITIVES, "forbidden": FORBIDDEN},
                "stages": STAGES, "rounds": 2, "order": "Baseline/candidate first round, reversed second round",
                "measurement": "Fresh Python subprocess per stage; inner publication and crossfile binder measured separately; snapshot collection outside publication timer",
                "real_expectation": "Observe supported facts and unknowns; crate/grouped imports are outside declared subset; no positive real-crossfile assumption",
                "real_reference_export": "Simple callee names, coordinates/status/target are facts; complex callee expressions are replaced by SHA256 to avoid copying source snippets",
                "limits": ["Two observations per stage/arm are descriptive, no p95 or general SLO", "Developer-authored diagnostic, not blind", "No agent task or retrieval byte-budget claims", "No LLM/compiler calls; OS cache uncontrolled"],
                "runner_sha256": sha(Path(__file__).read_bytes()), "source_integrity_sha256": sha((base / "source-integrity.json").read_bytes()),
                "corpus_manifest_sha256": sha((base / "corpus-manifest.json").read_bytes()),
                "python": sys.version, "platform": platform.platform()}
    write(base / "protocol.json", protocol)  # Freeze before the first measured publication.
    rows = []
    for round_number in (1, 2):
        for arm in (("baseline", "candidate") if round_number == 1 else ("candidate", "baseline")):
            for corpus in ("fixture", "real"):
                root = base / "runs" / f"round-{round_number}" / arm / corpus
                shutil.copytree(base / "frozen" / corpus, root)
                state = base / "state" / f"round-{round_number}" / arm / corpus
                for stage in (STAGES if corpus == "fixture" else ["full", "noop"]):
                    if corpus == "fixture":
                        for rel in ("src/ts/leaf.ts", "src/rust/leaf.rs"):
                            path = root / rel
                            renamed = path.with_name("renamed" + path.suffix)
                            if stage == "unexport":
                                path.write_text(path.read_text().replace("export ", "").replace("pub ", ""), encoding="utf-8")
                            elif stage == "restore_exports":
                                path.write_bytes((fixture / rel).read_bytes())
                            elif stage == "rename":
                                path.rename(renamed)
                            elif stage == "restore_names":
                                renamed.rename(path)
                            elif stage == "delete":
                                path.unlink()
                    environment = os.environ.copy()
                    environment.update(PYTHONPATH=str(base / "engines" / arm / "src"), PYTHONDONTWRITEBYTECODE="1")
                    start = perf_counter()
                    completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "_worker", str(root), str(state), stage, corpus],
                                               cwd=root, env=environment, capture_output=True, text=True, timeout=120)
                    wall_ms = (perf_counter() - start) * 1000
                    if completed.returncode:
                        write(base / "failure.json", {"arm": arm, "corpus": corpus, "stage": stage, "stdout": completed.stdout, "stderr": completed.stderr})
                        raise RuntimeError("Worker failed; preserve run state")
                    output = json.loads(completed.stdout)
                    receipt = f"round-{round_number}-{arm}-{corpus}-{stage}.json"
                    write(base / "receipts" / receipt, output | {"subprocess_wall_ms": wall_ms, "stderr": completed.stderr})
                    observed = {(e["from"], e["to"]) for e in output["call_edges"]}
                    rows.append({"round": round_number, "arm": arm, "corpus": corpus, "stage": stage,
                                 "receipt": "receipts/" + receipt, "publication_ms": output["publication_ms"],
                                 "subprocess_wall_ms": wall_ms, "rebind_ms": output["crossfile_rebind_ms"],
                                 "publication": output["metadata"]["publication"], "index_run": output["metadata"]["index_run"],
                                 "parsed_multilanguage_files": output["parsed_multilanguage_files"],
                                 "status": output["metadata"]["status"], "facts": output["facts"],
                                 "positive_calls": [{"from": a, "to": b, "observed": (a, b) in observed} for a, b in POSITIVES] if corpus == "fixture" else [],
                                 "forbidden_observed": [list(pair) for pair in FORBIDDEN if pair in observed] if corpus == "fixture" else []})
                    print(round_number, arm, corpus, stage, round(output["publication_ms"], 2), flush=True)
    assert frozen == {"fixture": manifest(fixture), "real": manifest(real)}, "Frozen corpus changed"
    assert integrity == {arm: manifest(base / "engines" / arm / "src/specgraph") for arm in integrity}, "Frozen engines changed"
    write(base / "summary.json", {"rows": rows, "frozen_sources_verified": True,
                                  "receipt_count": len(rows), "no_p95_claim": True})


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "_worker":
        worker(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4], sys.argv[5])
    else:
        main()
