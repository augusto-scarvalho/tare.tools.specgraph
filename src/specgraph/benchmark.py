"""Measure complete SDK calls, keeping release claims separate from timings."""
from __future__ import annotations

import math
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from typing import Any

from .query import project_query, serialize_query
from .storage import RepositoryIndex


def benchmark_queries(root: Path | str, queries: list[str], *, samples: int = 100,
                      rounds: int = 2, state_dir: Path | str | None = None,
                      transport: str = "sdk") -> dict[str, Any]:
    """Time open/read/assemble/serialize/close, never just the SQL statement.

    @spec SPEC-SPECGRAPH-C0 [AC-02, AC-03]
    @spec SPEC-SPECGRAPH-C4 [AC-05]
    """
    if not queries or any(not query.strip() for query in queries):
        raise ValueError("At least one nonempty query is required")
    if not 1 <= samples <= 10000 or not 1 <= rounds <= 10:
        raise ValueError("samples must be 1..10000 and rounds 1..10")
    if transport not in {"sdk", "cli"}:
        raise ValueError("transport must be sdk or cli")
    root = Path(root).resolve()
    with RepositoryIndex(root, state_dir=state_dir) as index:
        metadata = index.metadata()
    results = []
    identities = set()
    for number in range(rounds):
        durations = []
        sizes = []
        statuses: dict[str, int] = {}
        for i in range(samples):
            started = time.perf_counter_ns()
            query = queries[i % len(queries)]
            if transport == "cli":
                env = dict(os.environ)
                if state_dir is not None:
                    env["TARE_SPECGRAPH_STATE_DIR"] = str(Path(state_dir).resolve())
                completed = subprocess.run(
                    [sys.executable, "-m", "specgraph.cli", "--root", str(root), "query", query, "--format", "json"],
                    env=env, capture_output=True, timeout=30)
                if completed.returncode not in (0, 1) or not completed.stdout:
                    raise ValueError("CLI benchmark request failed: " + completed.stderr.decode("utf-8", errors="replace")[:300])
                wire = completed.stdout
                response = json.loads(wire)
            else:
                response = project_query(root, query, state_dir=state_dir)
                wire = serialize_query(response).encode("utf-8")
            durations.append((time.perf_counter_ns() - started) / 1_000_000)
            sizes.append(len(wire))
            statuses[response["status"]] = statuses.get(response["status"], 0) + 1
            snapshot = response.get("snapshot", {})
            identities.add(str(snapshot.get("content_hash", response.get("content_hash"))))
        ordered = sorted(durations)
        results.append({"round": number + 1, "samples": samples,
                        "p50_ms": round(statistics.median(ordered), 3),
                        "p95_ms": round(ordered[math.ceil(.95 * samples) - 1], 3),
                        "p99_ms": round(ordered[math.ceil(.99 * samples) - 1], 3),
                        "max_ms": round(max(ordered), 3),
                        "mean_output_bytes": round(statistics.mean(sizes)), "statuses": statuses})
    return {"schema": "tare.tools/specgraph-benchmark/1", "root": str(root),
            "profile": "CURRENT_REPOSITORY", "platform": platform.platform(),
            "python": sys.version.split()[0], "executable": sys.executable,
            "content_hash": metadata["content_hash"], "coverage": metadata["coverage"],
            "transport": transport,
            "measurement": ("Complete CLI subprocess wall time including interpreter startup and serialization" if transport == "cli"
                            else "SDK wall time including SQLite open/read/assemble/serialize/close"),
            "cache_state": "OS_CACHE_UNCONTROLLED; first sample included; no prewarm",
            "excluded_costs": (["interpreter startup"] if transport == "sdk" else []) + ["index publication", "MCP framing", "agent reasoning"],
            "queries": queries, "rounds": results,
            "consistent_observed_snapshot": identities == {metadata["content_hash"]},
            "release_qualification": "NOT_ESTABLISHED",
            "reference_profile": "NOT_ASSERTED", "token_savings": "NOT_MEASURED"}
