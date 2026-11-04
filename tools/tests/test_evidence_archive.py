"""Archive recovery must reject changed bytes, missing files and unsafe destinations."""
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.evidence_archive import materialize


@pytest.fixture
def archived(tmp_path):
    root = tmp_path / "repo"
    source = root / "docs/evidence/synthetic"
    source.mkdir(parents=True)
    data = b"historical receipt\r\n"
    (source / "receipt.txt").write_bytes(data)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=", "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "synthetic fixture"], check=True)
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    index = tmp_path / "index.json"
    manifest = {"schema": "tare.tools/specgraph-evidence-archive/1", "commit": commit,
                "files": {"docs/evidence/synthetic/receipt.txt": {
                    "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}}}
    index.write_text(json.dumps(manifest))
    return root, index, manifest, data


def test_recovers_exact_bytes_without_touching_current_checkout(archived, tmp_path):
    root, index, manifest, data = archived
    (root / "docs/evidence/synthetic/receipt.txt").write_bytes(b"current bytes differ")
    folder = materialize(tmp_path / "output", "synthetic", root=root, index=index)
    assert (folder / "receipt.txt").read_bytes() == data
    assert (root / "docs/evidence/synthetic/receipt.txt").read_bytes() == b"current bytes differ"


@pytest.mark.parametrize("mutation", ["hash", "size", "inventory"])
def test_rejects_manifest_mismatch_and_removes_incomplete_export(archived, tmp_path, mutation):
    root, index, manifest, data = archived
    row = manifest["files"]["docs/evidence/synthetic/receipt.txt"]
    if mutation == "hash":
        row["sha256"] = "0" * 64
    elif mutation == "size":
        row["bytes"] += 1
    else:
        manifest["files"]["docs/evidence/synthetic/missing.txt"] = dict(row)
    index.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="mismatch"):
        materialize(tmp_path / "output", "synthetic", root=root, index=index)
    assert not (tmp_path / "output/docs/evidence/synthetic").exists()


def test_does_not_overwrite_existing_export(archived, tmp_path):
    root, index, manifest, data = archived
    folder = tmp_path / "output/docs/evidence/synthetic"
    folder.mkdir(parents=True)
    (folder / "receipt.txt").write_bytes(b"keep")
    with pytest.raises(ValueError, match="already exists"):
        materialize(tmp_path / "output", "synthetic", root=root, index=index)
    assert (folder / "receipt.txt").read_bytes() == b"keep"


@pytest.mark.parametrize("group", ["../escape", "/absolute", "a/../escape", "a\\escape", "C:escape"])
def test_rejects_invalid_directory(archived, tmp_path, group):
    root, index, manifest, data = archived
    with pytest.raises(ValueError, match="invalid archive directory"):
        materialize(tmp_path / "output", group, root=root, index=index)


def test_rejects_mutable_revision(archived, tmp_path):
    root, index, manifest, data = archived
    manifest["commit"] = "main"
    index.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="immutable"):
        materialize(tmp_path / "output", "synthetic", root=root, index=index)


def test_support_uses_original_blob_when_git_archive_would_substitute_it(archived, tmp_path):
    root, index, manifest, data = archived
    original = b'commit = "$Format:%H$"\n'
    (root / "support.py").write_bytes(original)
    (root / ".gitattributes").write_text("support.py export-subst\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "core.hooksPath=", "-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "support fixture"], check=True)
    manifest["commit"] = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    manifest["support_files"] = {"support.py": {
        "bytes": len(original), "sha256": hashlib.sha256(original).hexdigest()}}
    index.write_text(json.dumps(manifest))
    output = tmp_path / "output"
    materialize(output, "synthetic", root=root, index=index, include_support=True)
    assert (output / "support.py").read_bytes() == original


def test_maintained_regression_inputs_keep_their_declared_historical_identity():
    root = Path(__file__).resolve().parents[2]
    provenance = json.loads((root / "tests/fixtures/historical/PROVENANCE.json").read_text())
    archive = json.loads((root / "docs/evidence/ARCHIVE.json").read_text())
    for name, reference in provenance["files"].items():
        original = archive["files"][reference["archive_path"]]
        assert reference["commit"] == archive["commit"]
        assert reference["sha256"] == original["sha256"]
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == original["sha256"]
