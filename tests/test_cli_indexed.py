import json
import os
from pathlib import Path
import subprocess
import sys

from specgraph.benchmark import benchmark_queries
from specgraph.indexer import index_repository


def test_actual_cli_query_serializes_utf8_within_budget(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text('def café():\n    return "ação"\n', encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("TARE_SPECGRAPH_STATE_DIR", str(state))
    index_repository(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-m", "specgraph.cli", "--root", str(tmp_path),
                             "query", "café", "--format", "json", "--max-bytes", "4096"],
                            env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.endswith(b"\n") and len(result.stdout) <= 4096
    assert json.loads(result.stdout)["results"][0]["name"] == "café"
    benchmark = benchmark_queries(tmp_path, ["café"], samples=1, rounds=1, state_dir=state)
    assert benchmark["rounds"][0]["mean_output_bytes"] == len(result.stdout.replace(b'"max_bytes":4096', b'"max_bytes":8192'))
    assert benchmark["release_qualification"] == "NOT_ESTABLISHED"


def test_read_only_query_import_does_not_load_project_parsers():
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run([sys.executable, "-c",
        "import sys; from specgraph.query import project_query; "
        "assert 'specgraph.graph' not in sys.modules; "
        "assert 'specgraph.code_parser' not in sys.modules; "
        "from specgraph import CausalTraceabilityMatrix; "
        "assert callable(CausalTraceabilityMatrix.build)"], env=env, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
