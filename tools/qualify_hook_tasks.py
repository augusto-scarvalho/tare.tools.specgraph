"""Paired, actual-model microtasks; never substitute byte estimates for tokens.

Run with --execute and an explicit installed Claude CLI. The fixture is small,
tool access is disabled and the model returns a bounded JSON answer. This is an
exploratory retrieval/edit-proposal experiment, not production task success.
"""
from __future__ import annotations

import argparse
import ast
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fixture(root):
    files = {
        "specgraph.yaml": "project_name: paired-billing\nindexed_paths: [src]\ngoverned_paths: [src]\nspec_paths: [specs]\ntest_paths: [tests]\n",
        "specs/SPEC-BILLING.md": "---\nid: SPEC-BILLING\ntitle: Billing totals\nacceptance_criteria:\n  AC-01: The total equals quantity multiplied by unit_price.\n  AC-02: Issuing an invoice delegates total calculation to calculate_total.\n---\n",
        "src/billing.py": 'def calculate_total(quantity, unit_price):\n    """@spec SPEC-BILLING:AC-01"""\n    return quantity + unit_price\n\ndef issue_invoice(quantity, unit_price):\n    """@spec SPEC-BILLING:AC-02"""\n    return calculate_total(quantity, unit_price)\n',
        "tests/test_billing.py": 'from billing import calculate_total\n\ndef test_total_product():\n    """@verifies SPEC-BILLING:AC-01"""\n    assert calculate_total(3, 7) == 21\n',
    }
    # Additional actual code units in the same candidate file provide a modest
    # file-reading baseline. They are not fake model calls or oracle output.
    files["src/billing.py"] += "\n" + "\n".join(
        f'def currency_label_{i}(code):\n    """Render display variant {i}; unrelated to arithmetic."""\n    return str(code).upper()\n'
        for i in range(16))
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return files


TASKS = {
    "planner": ("List the complete acceptance_criteria of SPEC-BILLING, keyed by AC ID. Return {\"acceptance_criteria\":{...}}.",
                {"acceptance_criteria": {"AC-01": "The total equals quantity multiplied by unit_price.", "AC-02": "Issuing an invoice delegates total calculation to calculate_total."}}),
    "implementer": ("Propose the Python return expression that fixes calculate_total to satisfy AC-01. Use only quantity and unit_price. Return {\"expression\":\"...\"}.",
                    {"expression": "quantity * unit_price"}),
    "auditor": ("Does the supplied context prove successful test execution? List the exact test ID declaring verification of AC-01. Return {\"execution_proven\":false,\"declared_tests\":[...]}. Do not infer execution from source or declarations.",
                {"execution_proven": False, "declared_tests": ["tests/test_billing.py:test_total_product"]}),
    "explorer": ("List the exact symbol IDs of direct outgoing call dependencies of src/billing.py:issue_invoice. Return {\"outgoing_dependencies\":[...]}.",
                 {"outgoing_dependencies": ["src/billing.py:calculate_total"]}),
}


def score(role, answer):
    expected = TASKS[role][1]
    if role != "implementer":
        return type(answer) is dict and answer == expected
    if type(answer) is not dict or set(answer) != {"expression"} or not isinstance(answer["expression"], str):
        return False
    try:
        parsed = ast.parse(answer["expression"], mode="eval")
        return ast.dump(parsed) == ast.dump(ast.parse(expected["expression"], mode="eval"))
    except (SyntaxError, ValueError):
        return False


def parse_answer(result):
    value = result.get("structured_output")
    if isinstance(value, dict):
        return value
    body = result.get("result", "").strip()
    if body.startswith("```"):
        body = "\n".join(body.splitlines()[1:-1])
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return None


def measure_usage(result):
    usage = result.get("usage")
    if not isinstance(usage, dict) or not {"input_tokens", "output_tokens"} <= set(usage):
        return None
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
    if not all(type(usage.get(key, 0)) is int and usage.get(key, 0) >= 0 for key in fields):
        return None
    return {key: usage.get(key, 0) for key in fields} | {"total_tokens": sum(usage.get(key, 0) for key in fields)}


def execute_trial(trial, *, output, cli, model, effort, timeout):
    directory = output / trial["id"]
    directory.mkdir(exist_ok=True)
    argv = [cli, "--print", "--model", model, "--effort", effort, "--safe-mode",
            "--tools", "", "--permission-mode", "dontAsk", "--strict-mcp-config", "--no-chrome",
            "--disable-slash-commands", "--output-format", "stream-json", "--verbose"]
    launched = time.perf_counter()
    result = None
    raw = b""
    record = {key: value for key, value in trial.items() if key != "prompt"}
    record.update(model_requested=model, effort_requested=effort, argv=argv,
                  retries=0, interventions=0, evaluation_scope="ACTUAL_MODEL_NO_TOOL_MICROTASK")
    try:
        completed = subprocess.run(argv, input=trial["prompt"].encode(), capture_output=True,
                                   cwd=output / "fixture", timeout=timeout)
        raw = completed.stdout
        (directory / "stderr.txt").write_bytes(completed.stderr)
        record["exit_code"] = completed.returncode
    except subprocess.TimeoutExpired as exc:
        raw = exc.stdout or b""
        record.update(exit_code=None, timeout=True)
    except OSError as exc:
        record.update(exit_code=None, error=type(exc).__name__)
    record["wall_ms"] = (time.perf_counter() - launched) * 1000 + trial["context_wall_ms"]
    (directory / "stream.jsonl").write_bytes(raw)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if parsed.get("type") == "result":
            result = parsed
    answer = parse_answer(result) if result else None
    record.update(success=bool(record.get("exit_code") == 0 and result and not result.get("is_error") and score(trial["role"], answer)),
                  answer=answer, usage=measure_usage(result) if result else None,
                  cost_usd=result.get("total_cost_usd") if result else None,
                  observed_model_usage=result.get("modelUsage") if result else None,
                  stream_sha256=hashlib.sha256(raw).hexdigest())
    write_json(directory / "receipt.json", record)
    return record


def summarize(records):
    by_role = {}
    for role in TASKS:
        arms = {}
        for arm in ("file_context", "hook_context"):
            values = [row for row in records if row["role"] == role and row["arm"] == arm]
            measured = bool(values) and all(row.get("usage") is not None for row in values)
            arms[arm] = {"attempts": len(values), "successes": sum(row["success"] for row in values),
                         "total_tokens": sum(row["usage"]["total_tokens"] for row in values) if measured else None,
                         "wall_ms": sum(row["wall_ms"] for row in values), "retries": sum(row["retries"] for row in values),
                         "interventions": sum(row["interventions"] for row in values)}
        baseline, candidate = arms["file_context"], arms["hook_context"]
        comparable = (baseline["attempts"] == candidate["attempts"] > 0
                      and baseline["total_tokens"] is not None and candidate["total_tokens"] is not None)
        useful = (comparable and baseline["successes"] == baseline["attempts"] == candidate["successes"]
                  and candidate["total_tokens"] < baseline["total_tokens"]
                  and candidate["wall_ms"] <= baseline["wall_ms"])
        by_role[role] = {"arms": arms, "token_reduction_fraction": 1 - candidate["total_tokens"] / baseline["total_tokens"] if comparable and baseline["total_tokens"] else None,
                         "recommendation": "KEEP_SCOPED_ADVISORY" if useful else "ON_DEMAND_UNTIL_BETTER_END_TO_END_EVIDENCE",
                         "basis": "EXPLORATORY_FIXTURE_ONLY; do not automatically override project policy"}
    return by_role


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude-cli", required=True)
    parser.add_argument("--model", default="claude-fable-5-1")
    parser.add_argument("--effort", default="xhigh")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 4 or not 1 <= args.timeout <= 300:
        parser.error("rounds 1..4 and timeout 1..300 required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "protocol.json").exists():
        parser.error("Use a fresh output directory; previous trials must be preserved")
    from specgraph.hook_context import context_hint
    from specgraph.indexer import index_repository
    root = output / "fixture"
    files = fixture(root)
    metadata = index_repository(root, state_dir=output / "state")
    files_to_read = ["specs/SPEC-BILLING.md", "src/billing.py", "tests/test_billing.py"]
    trials = []
    for round_number in range(args.rounds):
        for role, (task, _) in TASKS.items():
            for arm in (("file_context", "hook_context") if round_number % 2 == 0 else ("hook_context", "file_context")):
                started = time.perf_counter()
                if arm == "file_context":
                    context = "\n\n".join(name + "\n" + (root / name).read_text() for name in files_to_read)
                else:
                    hint = context_hint(root, role=role, event="pre", files=("src/billing.py",),
                                        symbol_id="src/billing.py:" + ("issue_invoice" if role == "explorer" else "calculate_total"),
                                        spec_id="SPEC-BILLING", max_bytes=4096, timeout_ms=1000, state_dir=output / "state")
                    context = json.dumps(hint, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                context_wall_ms = (time.perf_counter() - started) * 1000
                prompt = f"You are acting as {role} on a small code-review task. Use only the supplied context. No execution results are supplied. Return only the requested JSON object.\nTASK: {task}\nCONTEXT:\n{context}\n"
                trial = {"id": f"r{round_number + 1}-{role}-{arm}", "round": round_number + 1, "role": role, "arm": arm,
                         "context_bytes": len(context.encode()), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                         "context_wall_ms": context_wall_ms, "prompt": prompt}
                (output / (trial["id"] + ".txt")).write_text(prompt, encoding="utf-8")
                trials.append(trial)
    protocol = {"schema": "tare.tools/hook-paired-protocol/1", "authority": "NONE", "model_requested": args.model,
                "effort_requested": args.effort, "rounds": args.rounds, "snapshot": metadata["content_hash"],
                "fixture_sha256": {name: hashlib.sha256(body.encode()).hexdigest() for name, body in files.items()},
                "arms": ["known file reads: spec + implementation + declared tests", "published exact-symbol role hook context"],
                "tokens": "Provider-reported input + cache write + cache read + output; includes observed model overhead; no byte-to-token estimate",
                "excluded_costs": ["one-time indexing", "development of this tool", "production multi-agent orchestration not exercised"],
                "task_scope": "Exploratory information retrieval and arithmetic edit proposal; not an end-to-end agent benchmark",
                "missing_usage": "Comparison unqualified; never count missing calls as free", "automatic_policy_change": False}
    write_json(output / "protocol.json", protocol)
    if not args.execute:
        print(json.dumps({"status": "PREPARED_NOT_EXECUTED", "trials": len(trials)}))
        return
    # Two independent arms overlap provider wait, not heavy local computation.
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda trial: execute_trial(trial, output=output, cli=args.claude_cli,
                            model=args.model, effort=args.effort, timeout=args.timeout), trials))
    report = {"schema": "tare.tools/hook-paired-evaluation/1", "authority": "NONE", "status": "OBSERVED",
              "protocol_sha256": hashlib.sha256((output / "protocol.json").read_bytes()).hexdigest(),
              "roles": summarize(records), "trials": records, "production_effectiveness": "NOT_ESTABLISHED",
              "universal_65_percent_savings": "NOT_CLAIMED"}
    write_json(output / "evaluation.json", report)
    print(json.dumps({"status": "OBSERVED", "trials": len(records), "successes": sum(r["success"] for r in records), "roles": report["roles"]}))


if __name__ == "__main__":
    main()
