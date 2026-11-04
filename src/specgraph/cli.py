from __future__ import annotations
import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from specgraph import __version__ as VERSION
PASS = 0
FAIL = 1

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="specgraph",
        description="tare.tools Spec-Driven Development (SDD) & Causal Traceability Matrix Engine"
    )
    p.add_argument("--root", default=".", help="Project root directory (default: current working dir)")
    p.add_argument("--format", choices=["json", "yaml", "md", "table"], default="table", help="Output format")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    sp = p.add_subparsers(dest="cmd", required=True)

    select_p = sp.add_parser("recommend-tests", help="Recommend tests from JSON impact evidence; falls back conservatively")
    select_p.add_argument("evidence", help="JSON file: changed, impact, optional mandatory, config_or_environment_changed")
    capture_p = sp.add_parser("capture-tests", help="Run full pytest suite and capture local line execution/fingerprints")
    capture_p.add_argument("output", help="Output observation JSON")
    capture_p.add_argument("--test-path", action="append", help="Override default tests tools/tests (repeatable)")
    recommend_p = sp.add_parser("recommend-from-trace", help="Recommend candidates from a local trace; never claims qualification")
    recommend_p.add_argument("receipt")
    recommend_p.add_argument("changed", nargs="+", help="Changed repository paths or path:line")
    recommend_p.add_argument("--mandatory", action="append", default=[])
    compare_p = sp.add_parser("compare-tests", help="Compare real mutations in temporary copies against full suite")
    compare_p.add_argument("receipt")
    compare_p.add_argument("mutations", help="JSON list of {name,path,find,replace}")
    compare_p.add_argument("output", help="Comparison result JSON")
    compare_p.add_argument("--mandatory", action="append", default=[])
    # Init
    init_p = sp.add_parser("init", help="Initialize specgraph.yaml and specs/ scaffold in current repository")
    init_p.add_argument("--name", default="my-project", help="Project name")

    # Strict source validation is distinct from reading snapshot metadata.
    stale_p = sp.add_parser("check-stale", help="Validate current sources against .specgraph/cache.json")
    stale_p.add_argument("--format", choices=["json", "yaml", "md", "table"], help="Output format")

    # Sync Cache
    sync_p = sp.add_parser("sync-cache", help="Force rebuild and synchronize the .specgraph/cache.json manifest")

    # Trace
    tr_p = sp.add_parser("trace", help="Compute and display the complete Causal Traceability Matrix")
    tr_p.add_argument("--format", choices=["json", "yaml", "md", "table"], help="Output format")
    tr_p.add_argument("--kind", choices=["all", "specs", "adrs", "schemas", "apis", "mcp", "code", "tests"], default="all", help="Filter table/Markdown sections by artifact kind; JSON/YAML preserve the complete matrix")

    # Search
    search_p = sp.add_parser("search", help="Find indexed AST symbols by literal name, ID, or path")
    search_p.add_argument("query", help="Literal symbol, qualified name, ID, or path fragment")
    search_p.add_argument("--limit", type=int, default=20, help="Maximum results (1-100, default: 20)")
    search_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")
    search_p.add_argument("--live", action="store_true", help="Explicitly rebuild from live source instead of reading a published index")

    index_p = sp.add_parser("index", help="Build and atomically publish the local SQLite read model")
    index_p.add_argument("--full", action="store_true", help="Re-extract all files instead of reusing content-bound extraction records")
    index_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")
    reconcile_p = sp.add_parser("reconcile", help="Observe current sources, publish and acknowledge delivered edit hints")
    reconcile_p.add_argument("--full", action="store_true")
    reconcile_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")
    watch_p = sp.add_parser("watch", help="Run a foreground periodic reconciler with debounced edit hints")
    watch_p.add_argument("--interval-seconds", type=float, default=30)
    watch_p.add_argument("--debounce-seconds", type=float, default=.3)
    watch_p.add_argument("--max-cycles", type=int, help="Stop after this many reconciliation attempts")
    watch_p.add_argument("--format", choices=["json", "table"], help="JSON emits one receipt per attempt; table suppresses unchanged snapshots")
    cap_p = sp.add_parser("capabilities", help="Describe available capabilities and implementation limits")
    cap_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")
    for mode in ("query", "symbol", "refs", "neighbors", "path", "explain", "code-context"):
        query_p = sp.add_parser(mode, help="Read bounded project intelligence from the published snapshot")
        query_p.add_argument("query", help="Symbol name, exact identity, path or search text")
        if mode == "path":
            query_p.add_argument("target", help="Target symbol")
        query_p.add_argument("--limit", type=int, default=20)
        query_p.add_argument("--max-nodes", type=int, default=100)
        query_p.add_argument("--max-hops", type=int, default=3)
        query_p.add_argument("--max-bytes", type=int, default=8192)
        query_p.add_argument("--format", choices=["json", "table"], help="Output format")
    serve_p = sp.add_parser("serve", help="Run the minimal read-only MCP gateway over stdio")
    bench_p = sp.add_parser("bench", help="Measure complete indexed SDK queries on the selected repository")
    bench_p.add_argument("query", nargs="+", help="Representative literal queries")
    bench_p.add_argument("--samples", type=int, default=100)
    bench_p.add_argument("--rounds", type=int, default=2)
    bench_p.add_argument("--transport", choices=["sdk", "cli"], default="sdk")
    bench_p.add_argument("--format", choices=["json"], default="json")
    report_p = sp.add_parser("report", help="Export a bounded, interactive, offline graph snapshot")
    report_p.add_argument("--html", nargs="?", const="specgraph-report.html", help="Output file; omit to write HTML to stdout")
    report_p.add_argument("--max-nodes", type=int, default=200)
    report_p.add_argument("--max-edges", type=int, default=800)
    report_p.add_argument("--max-hops", type=int, default=6)
    report_p.add_argument("--max-bytes", type=int, default=2000000)
    review_p = sp.add_parser("review-bundle", help="Select a published context bundle by a real Git diff reference")
    review_p.add_argument("git_ref")
    review_p.add_argument("--max-bytes", type=int, default=8192)
    review_p.add_argument("--max-files", type=int, default=128)
    review_p.add_argument("--format", choices=["json"], default="json")
    hint_p = sp.add_parser("hook-context", help="Read bounded advisory context for an explicit agent role")
    hint_p.add_argument("--role", required=True, choices=["planner", "implementer", "auditor", "explorer"])
    hint_p.add_argument("--event", choices=["start", "pre", "post", "stop", "end", "delegate"], default="pre")
    hint_p.add_argument("--file", action="append", default=[])
    hint_p.add_argument("--spec")
    hint_p.add_argument("--symbol", help="Exact published symbol identity; does not rediscover files")
    hint_p.add_argument("--work")
    hint_p.add_argument("--query")
    hint_p.add_argument("--previous-digest")
    hint_p.add_argument("--max-bytes", type=int, default=4096)
    hint_p.add_argument("--timeout-ms", type=int, default=50)

    impact_p = sp.add_parser(
        "impact", help="Preview cached downstream impact for one exact path or symbol"
    )
    impact_p.add_argument("query", help="Repository path or symbol:<exact-node-id>")
    impact_p.add_argument(
        "--max-nodes", type=int, default=100, help="Maximum returned nodes (1-1000)"
    )
    impact_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")

    staleness_p = sp.add_parser(
        "staleness", help="Assess bounded cache staleness for tool-reported changed paths"
    )
    staleness_p.add_argument(
        "--changed-path", action="append", required=True, help="Exact changed path (repeatable)"
    )
    staleness_p.add_argument(
        "--max-nodes", type=int, default=100, help="Maximum returned nodes (1-1000)"
    )
    staleness_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")

    resolve_p = sp.add_parser("resolve", help="SpecGraph-first lookup with bounded traditional recovery")
    resolve_p.add_argument("query", help="Literal symbol, qualified name, ID, path, or text")
    resolve_p.add_argument("--limit", type=int, default=20, help="Maximum results (1-100, default: 20)")
    resolve_p.add_argument("--timeout-ms", type=int, default=2500, help="Total lookup budget (100-30000ms)")
    resolve_p.add_argument("--format", choices=["json", "yaml", "table"], help="Output format")

    telemetry_p = sp.add_parser("telemetry", help="Summarize local search recovery telemetry")
    telemetry_p.add_argument("--format", choices=["json", "yaml", "md", "table"], help="Output format")

    ground_p = sp.add_parser(
        "ground", help="Project bounded repository grounding from a host-generated sources manifest"
    )
    ground_p.add_argument("--repo", required=True, help="Exact target repository ID")
    ground_p.add_argument(
        "--library-manifest", required=True, help="Explicit v3 sources manifest path (historical flag name)"
    )
    ground_p.add_argument(
        "--max-bytes", type=int, default=8192, help="Output budget (1024-65536 bytes)"
    )
    ground_p.add_argument(
        "--include-id",
        action="append",
        default=[],
        help="Exact Library document ID required by the Work item (repeatable)",
    )
    ground_p.add_argument(
        "--expected-scope-sha256",
        help="Fail closed if the selected grounding scope changed",
    )
    ground_p.add_argument("--format", choices=["json"], default="json")
    ground_p.add_argument("--read-bodies", action="store_true", help="Acquire selected hash-verified Git blobs from explicit owners")
    ground_p.add_argument("--owner", action="append", default=[], metavar="REPOSITORY=PATH")

    # Drift Check
    dc_p = sp.add_parser("drift-check", help="Verify that 100%% of governed code, schemas, and APIs are verified without drift")
    dc_p.add_argument("--strict", action=argparse.BooleanOptionalAction, default=True, help="Enforce strict orphan code checking")
    dc_p.add_argument("--format", choices=["json", "yaml", "md", "table"], help="Output format")

    # Context
    ctx_p = sp.add_parser("context", help="Extract minimal token-efficient Context Envelope for an AI agent")
    ctx_p.add_argument("spec_id", help="Specification ID (e.g., SPEC-001)")
    ctx_p.add_argument("--max-bytes", type=int, default=8192, help="Complete output budget (1024-65536 UTF-8 bytes); oversize fails without truncation")

    # Doctor
    doc_p = sp.add_parser("doctor", help="Comprehensive health and coverage diagnostic of the project's SDD pipeline")
    doc_p.add_argument("--format", choices=["json", "yaml", "md", "table"], help="Output format")
    doc_p.add_argument("--deep", action="store_true", help="Build the live graph and validate governance; default only reads metadata")
    doc_p.add_argument("--effectiveness-report", help="Explicit bounded paired-task experiment; does not attest production effectiveness")


    # Bundle (Reviewer Context Bundle)
    bundle_p = sp.add_parser("bundle", help="Extract minimal Reviewer Context Bundle (ADR-022 / RFC-004)")
    bundle_p.add_argument("--spec", help="Specification ID (e.g., SPEC-001)")
    bundle_p.add_argument("--files", nargs="*", help="List of modified files")
    bundle_p.add_argument("--max-bytes", type=int, default=8192, help="Complete output budget (1024-65536 UTF-8 bytes); oversize fails without truncation")
    bundle_p.add_argument("--format", choices=["json", "yaml", "md", "table"], default="md", help="Output format")

    return p

def handle_init(root_dir: Path, project_name: str) -> int:
    from .config import SpecGraphConfig
    cfg_file = root_dir / "specgraph.yaml"
    specs_dir = root_dir / "specs"

    if cfg_file.exists():
        print(f"specgraph.yaml already exists at {cfg_file}")
    else:
        cfg = SpecGraphConfig(project_name=project_name)
        cfg_file.write_text(cfg.dump_yaml(), encoding="utf-8")
        print(f"Created {cfg_file}")

    specs_dir.mkdir(parents=True, exist_ok=True)
    sample_spec = specs_dir / "SPEC-001_core_architecture.md"
    if not sample_spec.exists():
        sample_content = """---
id: SPEC-001
title: Core Engine Architecture
status: ACCEPTED
implements:
  - REQ-01
target_symbols:
  - src/core.py:main
acceptance_criteria:
  AC-01: Engine boots deterministically with zero runtime errors
  AC-02: State updates emit content-addressed digests
---

# SPEC-001: Core Engine Architecture

## 1. Intent
Defines the core operational primitives and state immutability guarantees.

## 2. Verification
All mutations must be tested against AC-01 and AC-02 using `@pytest.mark.verifies("SPEC-001", "AC-01")`.
"""
        sample_spec.write_text(sample_content, encoding="utf-8")
        print(f"Created sample spec at {sample_spec}")

    print("SpecGraph initialized successfully.")
    return PASS

def dump_trace(matrix: CausalTraceabilityMatrix, fmt: str, kind: str = "all") -> None:
    import yaml
    from .serialization import json_default
    data = matrix.to_dict()
    if fmt == "json":
        print(json.dumps(data, ensure_ascii=False, indent=2, default=json_default))
    elif fmt == "yaml":
        print(yaml.dump(data, sort_keys=False, indent=2))
    elif fmt in ("md", "table"):
        stats = data["summary"]
        print(f"# SpecGraph Causal Traceability Matrix ({matrix.config.project_name})")
        print(f"**Generation ID:** `{data['generation_id'][:16]}` | **ADRs:** {stats['total_adrs']} | **Specs:** {stats['total_specs']} | **Schemas:** {stats['total_schemas']} | **APIs:** {stats['total_apis']} | **MCP Tools:** {stats['total_mcp_tools']}")
        print(f"**Governed Code Symbols:** {stats['total_governed_symbols']} | **Tests:** {stats['total_tests']} | **Causal Edges:** {stats['total_causal_edges']}")
        print(f"**Governed Code Coverage:** `{stats['governed_code_coverage_pct']}%` | **Spec Verification Coverage:** `{stats['spec_verification_coverage_pct']}%`\n")

        if kind in ("all", "adrs") and data.get("adrs"):
            print("## Architecture Decision Records (ADRs & RFCs)")
            print("| ADR ID | Title | Status | Superseded By | Governed Specs | File |")
            print("|---|---|---|---|---|---|")
            for a in data["adrs"]:
                sup = f"`{a['superseded_by']}`" if a["superseded_by"] else "-"
                gov = ", ".join([f"`{g}`" for g in a["governed_specs"]]) if a["governed_specs"] else "-"
                print(f"| **`{a['id']}`** | {a['title']} | `{a['status']}` | {sup} | {gov} | `{a['file_path']}` |")
            print("")

        if kind in ("all", "specs"):
            print("## Specification Traceability Table")
            print("| Spec ID | Title | Status | Implements | Schemas/APIs | Linked Code Symbols | Verifying Tests |")
            print("|---|---|---|---|---|---|---|")
            for s in data["specs"]:
                s_id = s["id"]
                code_syms = [e["from"] for e in data["edges"] if e["to"] == s_id and e["type"] == "CODE_IMPLEMENTS_SPEC"]
                schemas = [e["from"] for e in data["edges"] if e["to"] == s_id and e["type"] == "SCHEMA_IMPLEMENTS_SPEC"]
                apis = [e["from"] for e in data["edges"] if e["to"] == s_id and e["type"] in ("API_IMPLEMENTS_SPEC", "MCP_TOOL_IMPLEMENTS_SPEC")]
                tests = [e["from"] for e in data["edges"] if e["to"] == s_id and e["type"] == "TEST_VERIFIES_SPEC"]

                contracts = schemas + apis
                contracts_str = "<br>".join([f"`{c.split(':')[-1]}`" for c in contracts]) if contracts else "-"
                code_str = "<br>".join([f"`{c.split(':')[-1]}`" for c in code_syms]) if code_syms else "*None (Orphan Spec)*"
                test_str = "<br>".join([f"`{t.split(':')[-1]}`" for t in tests]) if tests else "❌ *Unverified*"
                req_str = ", ".join([f"`{r}`" for r in s["implements"]]) if s["implements"] else "-"

                print(f"| **`{s_id}`** | {s['title']} | `{s['status']}` | {req_str} | {contracts_str} | {code_str} | {test_str} |")
            print("")

        if kind in ("all", "schemas") and data.get("schemas"):
            print("## Data Schemas & Contracts (SQL, JSON Schema, Pydantic)")
            print("| Schema ID | Kind | Linked Specs | File | Fields |")
            print("|---|---|---|---|---|")
            for sch in data["schemas"]:
                specs_str = ", ".join([f"`{sp}`" for sp in sch["linked_specs"]]) if sch["linked_specs"] else "*Orphan Schema*"
                fields_count = len(sch["fields"])
                print(f"| **`{sch['name']}`** | `{sch['kind']}` | {specs_str} | `{sch['file_path']}` | {fields_count} fields |")
            print("")

        if kind in ("all", "code") and data.get("symbols"):
            print("## Code Symbols")
            print("| Symbol ID | Name | Kind | File:Line |")
            print("|---|---|---|---|")
            for symbol in data["symbols"]:
                print(f"| **`{symbol['symbol_id']}`** | `{symbol['name']}` | `{symbol['kind']}` | `{symbol['file_path']}:{symbol['start_line']}` |")
            print("")

        if kind in ("all", "tests") and data.get("tests"):
            print("## Tests")
            print("| Test ID | Name | File:Line |")
            print("|---|---|---|")
            for test in data["tests"]:
                print(f"| **`{test['test_id']}`** | `{test['name']}` | `{test['file_path']}:{test['start_line']}` |")
            print("")

        if kind in ("all", "apis", "mcp") and (data.get("apis") or data.get("mcp_tools")):
            print("## APIs & MCP Tool Contracts")
            print("| Interface ID | Type | Method/Tool | Linked Specs | File:Line |")
            print("|---|---|---|---|---|")
            for ep in (data.get("apis", []) if kind in ("all", "apis") else []):
                specs_str = ", ".join([f"`{sp}`" for sp in ep["linked_specs"]]) if ep["linked_specs"] else "*Orphan API*"
                print(f"| **`{ep['endpoint_id']}`** | HTTP Route | `{ep['method']} {ep['path']}` | {specs_str} | `{ep['file_path']}:{ep['start_line']}` |")
            for tool in (data.get("mcp_tools", []) if kind in ("all", "mcp") else []):
                specs_str = ", ".join([f"`{sp}`" for sp in tool["linked_specs"]]) if tool["linked_specs"] else "*Orphan MCP Tool*"
                print(f"| **`{tool['tool_id']}`** | MCP Agent Tool | `{tool['name']}({', '.join(tool['parameters'])})` | {specs_str} | `{tool['file_path']}:{tool['start_line']}` |")
            print("")

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(getattr(args, "root", ".")).resolve()
    fmt = getattr(args, "format", "table") or "table"
    if fmt == "yaml":
        import yaml

    if args.cmd in {"capture-tests", "recommend-from-trace", "compare-tests"}:
        from .test_selection import capture, recommend_from_receipt, compare
        try:
            if args.cmd == "capture-tests":
                payload = capture(root, Path(args.output), pytest_args=args.test_path)
                print(json.dumps({"receipt": args.output, "tests": len(payload['tests']),
                                  "capture_seconds": payload['capture_seconds'], "suite_returncode": payload['suite_returncode']}))
                return PASS if payload['suite_returncode'] == 0 else FAIL
            if args.cmd == "recommend-from-trace":
                try:
                    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    receipt = {}
            else:
                receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
            if args.cmd == "compare-tests":
                mutations = json.loads(Path(args.mutations).read_text(encoding="utf-8"))
                payload = compare(root, receipt, mutations, mandatory=args.mandatory)
                Path(args.output).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            else:
                payload = recommend_from_receipt(args.changed, receipt, root=root, mandatory=args.mandatory)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        if args.cmd == "recommend-from-trace":
            payload = {**payload, "full_suite_count": len(payload['tests'])}
            if payload['mode'] == 'FULL_SUITE':
                payload.pop('tests')  # candidates are not the complete-suite inventory
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return PASS

    if args.cmd == "recommend-tests":
        from .test_selection import recommend_tests
        try:
            data = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
            result = recommend_tests(data.get("changed", []), data.get("impact", {}),
                                     mandatory=data.get("mandatory", []),
                                     config_or_environment_changed=data.get("config_or_environment_changed", False))
        except (OSError, ValueError, TypeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return PASS

    if args.cmd == "init":
        return handle_init(root, getattr(args, "name", "my-project"))

    elif args.cmd in {"index", "reconcile", "capabilities"} or (args.cmd == "doctor" and not args.deep):
        from .capabilities import capability_catalog, project_doctor
        import sqlite3
        try:
            if args.cmd in {"index", "reconcile"}:
                from .lifecycle import reconcile_repository
                payload = reconcile_repository(root, incremental=not args.full, reason=args.cmd)
            elif args.cmd == "capabilities":
                payload = capability_catalog()
            else:
                payload = project_doctor(root, effectiveness_report=args.effectiveness_report)
        except (ValueError, OSError, sqlite3.DatabaseError, RuntimeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        if fmt == "yaml":
            print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        elif fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return FAIL if payload.get("status") in {"NOT_CONFIGURED", "INVALID_CONFIGURATION", "PARTIAL", "BUSY"} else PASS

    elif args.cmd == "watch":
        from .lifecycle import watch_repository
        import sqlite3
        worker = watch_repository(root, interval_seconds=args.interval_seconds,
                                  debounce_seconds=args.debounce_seconds, max_cycles=args.max_cycles)
        failed = False
        try:
            for payload in worker:
                failed = payload.get("status") != "READY"
                if fmt == "json":
                    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
                elif payload.get("publication") != "UNCHANGED":
                    print(f"{payload.get('status')} {payload.get('publication', '')} {payload.get('reason', '')}", flush=True)
        except KeyboardInterrupt:
            pass
        except (ValueError, OSError, sqlite3.DatabaseError, RuntimeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        finally:
            worker.close()
        return FAIL if failed else PASS

    elif args.cmd == "serve":
        from .mcp_server import main as serve_main
        return serve_main(["--root", str(root)])

    elif args.cmd == "report":
        from .report import report_html
        import sqlite3
        import tempfile
        try:
            html = report_html(root, max_nodes=args.max_nodes, max_edges=args.max_edges,
                               max_hops=args.max_hops, max_bytes=args.max_bytes)
            if args.html:
                output = Path(args.html).resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".specgraph-report-", delete=False) as stream:
                        temporary = Path(stream.name)
                        stream.write(html.encode("utf-8"))
                    os.replace(temporary, output)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                print(str(output))
            else:
                sys.stdout.buffer.write(html.encode("utf-8"))
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        return PASS

    elif args.cmd == "review-bundle":
        from .review import review_bundle
        from .serialization import canonical_json
        try:
            payload = review_bundle(root, args.git_ref, max_bytes=args.max_bytes, max_files=args.max_files)
        except (ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        sys.stdout.buffer.write((canonical_json(payload) + "\n").encode("utf-8"))
        return PASS if payload.get("status") in {"READY", "EMPTY"} else FAIL

    elif args.cmd == "hook-context":
        from .hook_context import context_hint
        from .serialization import canonical_json
        try:
            payload = context_hint(root, role=args.role, event=args.event, files=tuple(args.file),
                                   spec_id=args.spec, work_id=args.work, query=args.query, symbol_id=args.symbol,
                                   previous_digest=args.previous_digest, max_bytes=args.max_bytes,
                                   timeout_ms=args.timeout_ms)
        except (ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        if payload.get("status") != "SILENT":
            sys.stdout.buffer.write((canonical_json(payload) + "\n").encode("utf-8"))
        return PASS

    elif args.cmd == "bench":
        from .benchmark import benchmark_queries
        try:
            payload = benchmark_queries(root, args.query, samples=args.samples, rounds=args.rounds, transport=args.transport)
        except (ValueError, OSError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return PASS

    elif args.cmd in {"query", "symbol", "refs", "neighbors", "path", "explain", "code-context"}:
        from .query import project_query, serialize_query
        import sqlite3
        try:
            payload = project_query(root, args.query,
                mode={"query": "search", "code-context": "context"}.get(args.cmd, args.cmd),
                target=getattr(args, "target", None), limit=args.limit,
                max_nodes=args.max_nodes, max_hops=args.max_hops, max_bytes=args.max_bytes)
            raw = serialize_query(payload).encode("utf-8")
        except (ValueError, OSError, sqlite3.DatabaseError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        sys.stdout.buffer.write(raw)
        return PASS if payload.get("status") in {"HIT", "OK", "READY"} else FAIL

    elif args.cmd == "search":
        from .storage import IndexUnavailableError, RepositoryIndex
        import sqlite3
        metadata = None
        try:
            if args.live:
                raise IndexUnavailableError("Explicit live read")
            with RepositoryIndex(root) as index:
                results = index.search(args.query, args.limit)
                observed = index.metadata()
                metadata = {key: observed[key] for key in (
                    "repository_root", "revision_seq", "content_hash", "indexed_at", "freshness")}
                metadata["coverage"] = {key: observed["coverage"][key] for key in (
                    "status", "file_count", "symbol_count", "languages")}
            source = "published_snapshot"
        except IndexUnavailableError:
            # Preserve old search behavior for unindexed repositories. The
            # provenance makes this rebuild visible; `query` requires an index.
            try:
                # Validate before paying the cost of a live reconstruction.
                RepositoryIndex._validate_query(args.query, args.limit)
                from .graph import CausalTraceabilityMatrix
                from .search import search_symbols
                matrix = CausalTraceabilityMatrix.build(root_dir=root)
                results = [item.to_dict() for item in search_symbols(list(matrix.symbols.values()), args.query, limit=args.limit)]
            except (ValueError, OSError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return FAIL
            source = "live_rebuild"
        except (ValueError, sqlite3.DatabaseError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        payload = {"query": args.query, "count": len(results), "results": results, "source": source,
                   "snapshot": metadata}
        if fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
        else:
            print(f"Source: {source}")
            for result in results:
                print(f"{result['file_path']}:{result['start_line']} {result['name']}")
        return PASS

    elif args.cmd == "check-stale":
        from .cache import IncrementalCacheEngine
        report = IncrementalCacheEngine.detect_staleness(root_dir=root)
        if fmt == "json":
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(report.to_dict(), sort_keys=False, indent=2))
        else:
            if report.status == "FRESH":
                print(f"✅ [FRESH] Index is fully synchronized ({report.checked_files_count} files verified in {report.duration_ms:.2f}ms)")
                print(f"Generation Digest: {report.generation_digest[:16]}...")
            else:
                print(f"⚠️ [STALE] Codebase has diverged from .specgraph/cache.json ({report.duration_ms:.2f}ms)")
                if report.modified_files:
                    print(f"  Modified Files ({len(report.modified_files)}):")
                    for m in report.modified_files:
                        print(f"    ~ {m}")
                if report.added_files:
                    print(f"  Added Files ({len(report.added_files)}):")
                    for a in report.added_files:
                        print(f"    + {a}")
                if report.deleted_files:
                    print(f"  Deleted Files ({len(report.deleted_files)}):")
                    for d in report.deleted_files:
                        print(f"    - {d}")
                if report.impacted_nodes:
                    print(f"  Causal Invalidation Blast Radius ({len(report.impacted_nodes)} nodes):")
                    for node in report.impacted_nodes:
                        print(f"    * {node}")
                print("\nRun 'specgraph sync-cache' or 'specgraph drift-check' to reconcile.")
        return PASS if report.status == "FRESH" else FAIL

    elif args.cmd == "sync-cache":
        from .graph import CausalTraceabilityMatrix
        matrix = CausalTraceabilityMatrix.build(root_dir=root, auto_cache=True)
        print(f"✅ [SYNCED] Updated .specgraph/cache.json ({len(matrix.specs)} specs, {len(matrix.symbols)} symbols in digest {matrix.canonical_digest()[:16]})")
        return PASS

    elif args.cmd == "resolve":
        from .resolve import resolve_repository
        try:
            report = resolve_repository(
                root,
                args.query,
                limit=args.limit,
                timeout_ms=args.timeout_ms,
            )
        except ValueError as exc:
            parser.error(str(exc))
        payload = report.to_dict()
        if fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(payload, sort_keys=False, indent=2))
        else:
            print(
                f"[{report.status}] source={report.source} "
                f"primary={report.primary_status} action={report.recovery_action}"
            )
            for result in report.results:
                line = result.get("start_line", result.get("line", "?"))
                name = result.get("name", result.get("excerpt", ""))
                print(f"{result.get('file_path', '?')}:{line} {name}")
        return PASS if report.status == "HIT" else FAIL

    elif args.cmd == "impact":
        from .impact import preview_impact
        try:
            report = preview_impact(root, args.query, max_nodes=args.max_nodes)
        except ValueError as exc:
            parser.error(str(exc))
        payload = report.to_dict()
        if fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(payload, sort_keys=False, indent=2))
        else:
            print(
                f"[{report.status}] source={report.source_kind or '-'} "
                f"nodes={report.total_impacted_nodes} truncated={report.truncated}"
            )
            if report.reason:
                print(report.reason)
            for node in report.impacted_nodes:
                print(node)
        return PASS if report.status == "HIT" else FAIL

    elif args.cmd == "staleness":
        from .staleness import assess_changed_paths
        try:
            report = assess_changed_paths(
                root, args.changed_path, max_nodes=args.max_nodes
            )
        except ValueError as exc:
            parser.error(str(exc))
        payload = report.to_dict()
        if fmt == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(payload, sort_keys=False, indent=2))
        else:
            print(
                f"[{report.status}] changed={len(report.observed_changed_paths)} "
                f"nodes={report.total_impacted_nodes} truncated={report.truncated}"
            )
            if report.reason:
                print(report.reason)
            for node in report.impacted_nodes:
                print(node)
        return FAIL if report.status == "UNAVAILABLE" else PASS

    elif args.cmd == "ground":
        from .ground import ground_repository, encode_grounding_report
        if args.read_bodies:
            from .federation import acquire_federated_documents, serialize_federation
            from .ontology import parse_owner_roots, OntologyFederationError
            try:
                owners = parse_owner_roots(args.owner, root)
                payload = acquire_federated_documents(args.library_manifest, args.repo, owners,
                                                       include_ids=args.include_id, max_bytes=args.max_bytes)
            except (ValueError, OSError, OntologyFederationError) as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return FAIL
            sys.stdout.buffer.write(serialize_federation(payload).encode("utf-8"))
            return PASS if payload.get("status") == "READY" else FAIL
        try:
            report = ground_repository(
                args.library_manifest,
                args.repo,
                max_bytes=args.max_bytes,
                include_ids=args.include_id,
                expected_scope_sha256=args.expected_scope_sha256,
            )
        except ValueError as exc:
            parser.error(str(exc))
        sys.stdout.buffer.write(encode_grounding_report(report))
        return PASS if report.status in {"READY", "READY_TRUNCATED", "NOT_MAPPED"} else FAIL

    elif args.cmd == "telemetry":
        from .resolve import telemetry_summary
        summary = telemetry_summary(root)
        if fmt == "json":
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(summary, sort_keys=False, indent=2))
        elif fmt == "md":
            print("# SpecGraph search recovery telemetry")
            print(f"\n- Events: {summary['event_count']}")
            print(f"- Traditional recovery rate: {summary['rates_pct']['traditional_recovery']}%")
            print(f"- Validated reuse rate: {summary['rates_pct']['validated_reuse']}%")
            print(f"- Stale rate: {summary['rates_pct']['stale']}%")
            print(f"- Timeout rate: {summary['rates_pct']['timeout']}%")
            print(f"- Unavailable rate: {summary['rates_pct']['unavailable']}%")
            print(f"- Latency p50 / p95: {summary['latency_ms']['p50']} / {summary['latency_ms']['p95']} ms")
        else:
            print("Metric | Value")
            print("---|---:")
            print(f"Events | {summary['event_count']}")
            for name, value in summary["rates_pct"].items():
                print(f"{name} | {value}%")
            print(f"latency_p50_ms | {summary['latency_ms']['p50']}")
            print(f"latency_p95_ms | {summary['latency_ms']['p95']}")
        return PASS

    from .graph import CausalTraceabilityMatrix, canonical_json, json_default
    from .context import ContextPackager, encode_context_output
    import yaml
    matrix = CausalTraceabilityMatrix.build(root_dir=root)

    if args.cmd == "trace":
        dump_trace(matrix, fmt, kind=getattr(args, "kind", "all"))
        return PASS

    elif args.cmd == "drift-check":
        strict_val = getattr(args, "strict", True)
        res = matrix.drift_check(strict_override=strict_val)
        if fmt == "json":
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump(res, sort_keys=False, indent=2))
        else:
            if res["status"] == "PASS":
                print("✅ [PASS] No documentation or implementation drift detected!")
                print(f"Governed Code Coverage: {res['stats']['governed_code_coverage_pct']}% | Spec Verification: {res['stats']['spec_verification_coverage_pct']}%")
            else:
                print("❌ [DRIFT DETECTED] Violations found in codebase:")
                for v in res["violations"]:
                    print(f"  - {v}")
                if res["parse_errors"]:
                    print("\nParsing Failures:")
                    for pe in res["parse_errors"]:
                        print(f"  ! {pe}")
                if res["orphan_code"]:
                    print("\nOrphan Code Symbols (Missing @spec annotation):")
                    for s in res["orphan_code"]:
                        print(f"  * {s['symbol']} ({s['file']}:{s['line']})")
                if res["unverified_specs"]:
                    print("\nUnverified Specs (Code exists, but 0 verifying tests):")
                    for us in res["unverified_specs"]:
                        print(f"  * [{us['id']}] {us['title']} ({us['file']})")
        return PASS if res["status"] == "PASS" else FAIL


    elif args.cmd == "bundle":
        spec_arg = getattr(args, "spec", None)
        files_arg = getattr(args, "files", None)
        try:
            bundle_text = ContextPackager.generate_reviewer_bundle(
                matrix=matrix,
                files=files_arg,
                spec_id=spec_arg,
                root_dir=root,
                max_bytes=args.max_bytes,
            )
            output = json.dumps({"bundle": bundle_text, "spec": spec_arg, "files": files_arg}, ensure_ascii=False, indent=2) if fmt == "json" else bundle_text
            raw = encode_context_output(output + "\n", args.max_bytes)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        sys.stdout.buffer.write(raw)
        return PASS

    elif args.cmd == "context":
        try:
            envelope = ContextPackager.generate_spec_context(matrix, args.spec_id, root_dir=root, max_bytes=args.max_bytes)
            raw = encode_context_output(envelope + "\n", args.max_bytes)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return FAIL
        sys.stdout.buffer.write(raw)
        return PASS

    elif args.cmd == "doctor":
        strict_val = getattr(args, "strict", True)
        res = matrix.drift_check(strict_override=strict_val)
        stats = matrix.summary_stats()
        if fmt == "json":
            print(json.dumps({"status": res["status"], "stats": stats, "violations": res["violations"]}, ensure_ascii=False, indent=2))
        elif fmt == "yaml":
            print(yaml.dump({"status": res["status"], "stats": stats, "violations": res["violations"]}, sort_keys=False, indent=2))
        else:
            print(f"=== SpecGraph Doctor Diagnostic ({matrix.config.project_name}) ===")
            print(f"Status: {res['status']}")
            print(f"ADRs / RFCs: {stats['total_adrs']}")
            print(f"Specs Discovered: {stats['total_specs']}")
            print(f"Data Schemas (SQL/JSON/Pydantic): {stats['total_schemas']}")
            print(f"APIs & MCP Tools: {stats['total_apis'] + stats['total_mcp_tools']}")
            print(f"Governed Code Symbols: {stats['total_governed_symbols']}")
            print(f"Unit/Integration Tests: {stats['total_tests']}")
            print(f"Causal Edges: {stats['total_causal_edges']}")
            print(f"Governed Code Bound to Specs: {stats['governed_code_coverage_pct']}%")
            print(f"Specs Verified with Tests: {stats['spec_verification_coverage_pct']}%")
            print(f"Generation Content-Digest: {matrix.canonical_digest()}")
        return PASS if res["status"] == "PASS" else FAIL

    return PASS

if __name__ == "__main__":
    sys.exit(main())
