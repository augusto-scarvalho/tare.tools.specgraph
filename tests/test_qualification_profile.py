"""Contract checks for the independent C8 experiment runner."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("qualify_benchmark", Path(__file__).parents[1] / "tools/qualify_benchmark.py")
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def test_percentiles_retain_outlier_and_count_misses():
    rows = [{"sample": i, "wall_ms": 1 if i < 100 else 900, "output_bytes": 100,
             "status": "MISS" if i % 2 else "HIT"} for i in range(1, 101)]
    summary = profile.summarize(rows)
    assert summary["samples"] == 100
    assert summary["p95_ms"] == 1
    assert summary["max_ms"] == 900
    assert summary["mean_ms"] == 9.99
    assert summary["outlier_samples"] == [100]
    assert summary["statuses"] == {"MISS": 50, "HIT": 50}


def test_insufficient_profile_rejected_without_creating_output(tmp_path):
    output = tmp_path / "invalid"
    with pytest.raises(ValueError, match=">=100"):
        profile.run_profile(tmp_path, output, samples=99)
    assert not output.exists()
    with pytest.raises(ValueError, match=">=2"):
        profile.run_profile(tmp_path, output, rounds=1)


def test_existing_checkout_never_committed_and_fixture_bytes_reproducible(tmp_path):
    left, right = tmp_path / "left", tmp_path / "right"
    profile.generate_corpus(left, modules=2, functions=3)
    profile.generate_corpus(right, modules=2, functions=3)
    assert profile.manifest(left) == profile.manifest(right)
    assert all(b"\r\n" not in path.read_bytes() for path in left.rglob("*") if path.is_file())
    (left / ".git").mkdir()
    with pytest.raises(ValueError, match="existing checkout"):
        profile.pin(left, "must not commit")
