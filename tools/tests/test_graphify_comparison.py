"""Protect comparison integrity against false successes, not engine internals."""
import importlib.util
import json
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "compare_graphify.py"
spec = importlib.util.spec_from_file_location("compare_graphify", path)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def result(text, **changes):
    return {"stdout": text, "stdout_bytes": len(text.encode()), "returncode": 0,
            "execution": "COMPLETED", **changes}


def task():
    return {"kind": "exact", "expected": [{"file": "src/a.py", "name": "leaf", "start": 4}]}


@pytest.mark.parametrize("file,name,line", [("src/other.py", "leaf", 4),
                                         ("src/a.py", "leaf", 9), ("src/a.py", "leafish", 4)])
def test_same_name_wrong_source_or_range_is_not_a_witness(file, name, line):
    native = f"Traversal: BFS\nNODE {name}() [src={file} loc=L{line} community=]\n"
    assert comparison.score("graphify", result(native), task())["success"] is False


def test_both_native_formats_must_deliver_the_definition():
    sg = json.dumps({"status": "HIT", "results": [{"file_path": "src/a.py", "name": "leaf", "start_line": 4}]})
    gf = "Traversal: BFS\nNODE leaf() [src=src/a.py loc=L4 community=]\n"
    assert comparison.score("specgraph", result(sg), task())["success"]
    assert comparison.score("graphify", result(gf), task())["success"]


@pytest.mark.parametrize("engine", ["specgraph", "graphify"])
def test_failed_or_malformed_output_cannot_pass_a_negative(engine):
    negative = {"kind": "negative", "expected": []}
    assert not comparison.score(engine, result(""), negative)["success"]
    assert not comparison.score(engine, result("", execution="TIMEOUT"), negative)["success"]
    assert not comparison.score(engine, result("", returncode=2), negative)["success"]


def test_output_budget_breach_retains_success_but_fails_bounded_success():
    native = "Traversal: BFS\nNODE leaf() [src=src/a.py loc=L4 community=]\n" + "a" * 8192
    actual = comparison.score("graphify", result(native), task())
    assert actual["success"]
    assert not actual["success_within_budget"]


def test_incomplete_sample_set_has_no_aggregate(tmp_path):
    (tmp_path / "samples.jsonl").write_text("")
    with pytest.raises(ValueError, match="Incomplete"):
        comparison.summarize(tmp_path)


def test_a_full_count_with_wrong_rounds_cannot_qualify(tmp_path):
    rows = [{"round": 99, "sample": i, "engine": "specgraph"} for i in range(400)]
    (tmp_path / "samples.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(ValueError, match="Incomplete"):
        comparison.summarize(tmp_path)


def test_unparseable_graphify_nodes_cannot_pass_absence():
    native = "Traversal: BFS | 5 nodes found\nNODE leaf without a source witness\n"
    assert not comparison.score("graphify", result(native), {"kind": "negative", "expected": []})["success"]


def test_specgraph_error_envelope_cannot_pass_absence():
    native = json.dumps({"status": "ERROR", "results": []})
    assert not comparison.score("specgraph", result(native), {"kind": "negative", "expected": []})["success"]


def test_corpus_mutation_is_detected_before_measurement(tmp_path):
    corpus = tmp_path / "corpora/demo"
    corpus.mkdir(parents=True)
    (corpus / "a.py").write_text("modified")
    comparison.write_json(tmp_path / "tasks.json", [])
    comparison.write_json(tmp_path / "corpus-manifest.json", {"demo": {"files": {
        "a.py": {"sha256": comparison.sha(b"original")}}}})
    comparison.write_json(tmp_path / "protocol.json", {
        "tasks_sha256": comparison.sha((tmp_path / "tasks.json").read_bytes()),
        "corpus_manifest_sha256": comparison.sha((tmp_path / "corpus-manifest.json").read_bytes())})
    with pytest.raises(ValueError, match="Corpus changed"):
        comparison.verify_frozen(tmp_path)
