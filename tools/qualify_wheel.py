"""Build or select one wheel and qualify a clean native-platform installation.

No editable installation, checkout imports, compiler dependency, publishing or
host reload is implied. Run once per native OS against the same wheel SHA.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def qualify(repository, output, wheel=None):
    output.mkdir(parents=True, exist_ok=True)
    platform_name = "windows" if os.name == "nt" else "linux"
    logs = output / platform_name
    logs.mkdir(exist_ok=False)
    env = dict(os.environ)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1"

    def run(name, args, *, cwd, input=None):
        completed = subprocess.run([str(arg) for arg in args], cwd=cwd, env=env, input=input,
                                   text=True, encoding="utf-8", capture_output=True, timeout=240)
        (logs / (name + ".stdout")).write_text(completed.stdout, encoding="utf-8")
        (logs / (name + ".stderr")).write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{name} exited {completed.returncode}: {completed.stderr[-2000:]}")
        return completed.stdout

    with tempfile.TemporaryDirectory(prefix="specgraph-c8-clean-wheel-") as directory:
        tmp = Path(directory)
        if wheel is None:
            stage = tmp / "build-source"
            stage.mkdir()
            for name in ("pyproject.toml", "README.md", "LICENSE"):
                if (repository / name).is_file():
                    shutil.copy2(repository / name, stage / name)
            shutil.copytree(repository / "src/specgraph", stage / "src/specgraph",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            source_manifest = {p.relative_to(stage).as_posix(): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
            run("build", ["uv", "build", "--wheel", "--out-dir", output / "wheels", stage], cwd=tmp)
            wheel = next((output / "wheels").glob("*.whl"))
            # Ensure the frozen source did not drift across the actual build.
            for name, expected in source_manifest.items():
                if digest(repository / name) != expected:
                    raise AssertionError(f"Working source changed while wheel built: {name}")
            (output / "source-manifest.json").write_text(json.dumps({"authority": "exact copied working bytes, not upstream HEAD",
                                                                     "files": source_manifest}, indent=2) + "\n", encoding="utf-8")
        wheel = wheel.resolve()
        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            required = ("storage.py", "indexer.py", "query.py", "report.py", "mcp_server.py", "lifecycle.py",
                        "hook_context.py", "executor_evidence.py", "serialization.py", "extractors/tree_sitter.py")
            missing = [name for name in required if "specgraph/" + name not in names]
            if missing:
                raise AssertionError(f"Wheel missing expected modules: {missing}")
            assert not any("editable" in name.casefold() for name in names)
        venv = tmp / "installed"
        if os.name == "nt":
            run("venv", [sys.executable, "-m", "venv", venv], cwd=tmp)
            python, cli = venv / "Scripts/python.exe", venv / "Scripts/specgraph.exe"
            installer = [python, "-m", "pip", "install", "--only-binary=:all:"]
        else:
            run("venv", ["uv", "venv", venv, "--python", sys.executable], cwd=tmp)
            python, cli = venv / "bin/python", venv / "bin/specgraph"
            installer = ["uv", "pip", "install", "--python", python, "--only-binary", ":all:"]
        run("install", installer + ["tare-tools-specgraph[multilanguage] @ " + wheel.as_uri()], cwd=tmp)
        fixture = tmp / "fixture"
        (fixture / "src").mkdir(parents=True)
        (fixture / "src/main.py").write_text("def leaf():\n    return 1\ndef top():\n    return leaf()\n", encoding="utf-8")
        (fixture / "src/widget.ts").write_text("export function widget() { return 1; }\n", encoding="utf-8")
        (fixture / "src/lib.rs").write_text("pub fn item() -> u32 { 1 }\n", encoding="utf-8")
        env["TARE_SPECGRAPH_STATE_DIR"] = str(tmp / "state")
        code = '''import sys,json,importlib.metadata,pathlib
import specgraph
assert 'specgraph.graph' not in sys.modules
from specgraph import project_query, RepositoryIndex, serialize_query
assert 'specgraph.graph' not in sys.modules
assert 'site-packages' in str(pathlib.Path(specgraph.__file__))
dist=importlib.metadata.distribution('tare-tools-specgraph')
direct=json.loads(dist.read_text('direct_url.json'))
assert not direct.get('dir_info',{}).get('editable',False)
from specgraph import language_capabilities
assert all(v['status']=='AVAILABLE' for v in language_capabilities().values())
print(json.dumps({'package_file':specgraph.__file__,'direct_url':direct,'dependencies':{x:importlib.metadata.version(x) for x in ['tare-tools-specgraph','PyYAML','tree-sitter','tree-sitter-typescript','tree-sitter-rust']},'lazy_query_import':True}))'''
        sdk = json.loads(run("sdk", [python, "-I", "-c", code], cwd=fixture))
        indexed = json.loads(run("index", [cli, "--root", fixture, "index", "--format", "json"], cwd=fixture))
        for query in ("leaf", "widget", "item"):
            result = json.loads(run("query-" + query, [cli, "--root", fixture, "query", query, "--format", "json"], cwd=fixture))
            assert result["results"] and result["content_hash"] == indexed["content_hash"]
        html = logs / "report.html"
        run("report", [cli, "--root", fixture, "report", "--html", html], cwd=fixture)
        assert "<!doctype html>" in html.read_text(encoding="utf-8")
        assert indexed["content_hash"] in html.read_text(encoding="utf-8")
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "wheel-qualification", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "query_causal_graph", "arguments": {"query": "leaf"}}},
        ]
        responses = [json.loads(line) for line in run("serve", [cli, "--root", fixture, "serve"], cwd=fixture,
                     input="".join(json.dumps(item) + "\n" for item in messages)).splitlines()]
        assert {response["id"] for response in responses} == {1, 2, 3}
        rpc = next(response for response in responses if response["id"] == 3)
        query = json.loads(rpc["result"]["content"][0]["text"])
        assert query["content_hash"] == indexed["content_hash"] and query["results"]
        migration_script = logs / "qualify_migrations.py"
        shutil.copy2(Path(__file__).with_name("qualify_migrations.py"), migration_script)
        migrations = json.loads(run("migrations", [python, "-I", migration_script], cwd=fixture))
        receipt = {"schema": "tare.tools/specgraph-c8-clean-wheel/1", "status": "PASS", "platform": platform_name,
                   "wheel": str(wheel), "wheel_sha256": digest(wheel), "source_manifest": "../source-manifest.json",
                   "clean_install": True, "editable": False, "compiler_used": False,
                   "dependency_install_policy": "only-binary :all:", "sdk": sdk, "migrations": migrations,
                   "checks": ["expected new modules packaged", "isolated SDK import from site-packages",
                              "lazy query does not import graph", "CLI Python/TS/Rust index and queries",
                              "CLI offline HTML export", "MCP stdio initialize/list/query with coherent snapshot"],
                   "content_hash": indexed["content_hash"], "logs": {p.name: digest(p) for p in logs.iterdir() if p.is_file()},
                   "limits": ["Qualifies this wheel SHA only", "No release publication or IDE/agent daemon reload asserted",
                              "MCP protocol subprocess is not an IDE host integration test"]}
        (logs / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wheel", type=Path)
    args = parser.parse_args()
    receipt = qualify(args.repository.resolve(), args.output.resolve(), args.wheel)
    print(json.dumps({"status": receipt["status"], "wheel_sha256": receipt["wheel_sha256"], "platform": receipt["platform"]}))
