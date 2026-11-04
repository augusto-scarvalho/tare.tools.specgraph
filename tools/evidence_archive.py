"""Read exact historical evidence from pinned Git objects, without model calls."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs/evidence/ARCHIVE.json"
VERIFIERS = (
    "2026-09-08-x1", "2026-09-08-response-contract",
    "2026-09-08-native-edit", "2026-09-08-new-task-pilot",
)


def materialize(destination: Path, group: str, *, root: Path = ROOT,
                index: Path = INDEX, include_support: bool = False) -> Path:
    """Export one declared directory and verify every byte before using it."""
    manifest = json.loads(index.read_text(encoding="utf-8"))
    if manifest.get("schema") != "tare.tools/specgraph-evidence-archive/1":
        raise ValueError("unsupported archive manifest")
    commit = manifest["commit"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("archive requires a full immutable commit")
    relative = PurePosixPath("docs/evidence") / group
    if (not group or "\\" in group or ":" in group or group.startswith("/")
            or any(part in {".", ".."} for part in group.split("/"))):
        raise ValueError("invalid archive directory")
    prefix = relative.as_posix() + "/"
    expected = {name: row for name, row in manifest["files"].items()
                if name.startswith(prefix)}
    if not expected:
        raise ValueError("directory is not declared in the archive")
    support = manifest.get("support_files", {}) if include_support else {}
    destination = Path(destination).resolve()
    target = destination / relative
    if not target.resolve().is_relative_to(destination):
        raise ValueError("archive destination escapes its root")
    if target.exists():
        raise ValueError("archive destination already exists")
    destination.mkdir(parents=True, exist_ok=True)
    # A shallow checkout must explicitly acquire the old commit; never fetch implicitly.
    subprocess.run(["git", "-C", str(root), "cat-file", "-e", commit + "^{commit}"],
                   check=True, stdout=subprocess.DEVNULL)
    seen = set()
    created = []
    target.mkdir(parents=True)
    try:
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(
                ["git", "-C", str(root), "archive", "--format=tar", commit, "--",
                 relative.as_posix()],
                stdout=subprocess.PIPE, stderr=errors)
            try:
                with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                    for member in archive:
                        path = destination / member.name
                        if (not path.resolve().is_relative_to(destination)
                                or "\\" in member.name or ":" in member.name):
                            raise ValueError("archive path escapes destination")
                        if member.isdir():
                            continue
                        row = expected.get(member.name)
                        if not member.isfile() or row is None or member.name in seen:
                            raise ValueError("undeclared or non-regular archive entry")
                        if member.size != row["bytes"]:
                            raise ValueError("archive size mismatch: " + member.name)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        digest = hashlib.sha256()
                        with archive.extractfile(member) as source, path.open("xb") as output:
                            created.append(path)
                            while chunk := source.read(1024 * 1024):
                                digest.update(chunk)
                                output.write(chunk)
                        if digest.hexdigest() != row["sha256"]:
                            raise ValueError("archive hash mismatch: " + member.name)
                        seen.add(member.name)
                if process.wait() != 0:
                    errors.seek(0)
                    raise ValueError("Git archive failed: " + errors.read().decode(errors="replace"))
                if seen != expected.keys():
                    raise ValueError("archive file inventory mismatch")
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
        # git archive applies export-subst to package version files. Support
        # imports require the original committed blobs, as in a Git checkout.
        for name, row in support.items():
            path = destination / name
            if (not path.resolve().is_relative_to(destination)
                    or "\\" in name or ":" in name):
                raise ValueError("support path escapes destination")
            data = subprocess.check_output(["git", "-C", str(root), "cat-file", "blob", commit + ":" + name])
            if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
                raise ValueError("support hash or size mismatch: " + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as output:
                created.append(path)
                output.write(data)
    except BaseException:
        for path in created:
            if not path.is_relative_to(target):
                path.unlink(missing_ok=True)
        # Only this call's newly created, root-contained directory is removed.
        shutil.rmtree(target)
        raise
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=VERIFIERS)
    parser.add_argument("--output", type=Path, help="Materialize only; do not run the historical verifier")
    args = parser.parse_args(argv)
    if args.output is not None:
        print(materialize(args.output, args.group))
        return 0
    with tempfile.TemporaryDirectory(prefix="specgraph-historical-") as directory:
        folder = materialize(Path(directory), args.group, include_support=True)
        # Original frozen verifier and receipts retain their historical scoring semantics.
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join(
            (str(Path(directory) / "src"), directory))}
        return subprocess.run([sys.executable, str(folder / "verify_evidence.py")],
                              cwd=directory, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
