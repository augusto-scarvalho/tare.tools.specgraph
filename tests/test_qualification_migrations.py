"""The install-time migration experiment is executable against the SDK too."""
import importlib.util
from pathlib import Path


def test_historical_format_and_current_generation_compatibility(tmp_path):
    spec = importlib.util.spec_from_file_location("qualify_migrations", Path(__file__).parents[1] / "tools/qualify_migrations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = module.qualify(tmp_path)
    assert receipt["status"] == "PASS"
    assert len(receipt["checks"]) == 9
    assert receipt["current_revision"] > receipt["initial_revision"]
