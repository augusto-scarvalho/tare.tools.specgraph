"""Reviewer bundles selected by a real Git diff, read from one published index.

The Git ref selects changed tracked paths. It does not select an historical
SpecGraph generation: this bundle describes the current published snapshot.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

from .federation import _fit, _safe_path, _verified_documents, serialize_federation
from .storage import RepositoryIndex


def _source_hash(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Selected file exceeds 2 MiB source-check limit")
    return hashlib.sha256(raw).hexdigest()


def _git_output(root: Path, arguments: list[str], maximum: int) -> bytes:
    # A file-backed output cap avoids buffering an unbounded diff in memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.run(["git", "-C", str(root), *arguments], stdout=output, stderr=errors,
                                 check=False, timeout=10)
        if process.returncode:
            raise ValueError("Git selector unavailable for the explicit repository/ref")
        output.seek(0)
        raw = output.read(maximum + 1)
        if len(raw) > maximum:
            raise ValueError("Git selector exceeds byte budget; narrow the change")
        return raw


def review_bundle(root: Path | str, git_ref: str, *, state_dir: Path | str | None = None,
                  max_bytes: int = 8192, max_files: int = 128,
                  federated_documents: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Bundle whole source/intent units or explicitly refuse the output budget.

    File hashes are checked before/after selection against the read snapshot.
    This detects observed drift, not arbitrary ABA races. For a stronger source
    boundary the caller should use an immutable checkout. Untracked paths are
    explicitly excluded because Git diff cannot describe their committed base.

@spec SPEC-SPECGRAPH-C5
    """
    _fit({"schema": "validation"}, max_bytes)
    acquired = _verified_documents(federated_documents or [])
    if type(max_files) is not int or not 1 <= max_files <= 256:
        raise ValueError("max_files must be between 1 and 256")
    if not isinstance(git_ref, str) or not git_ref or git_ref.startswith("-") or len(git_ref.encode()) > 256:
        raise ValueError("Explicit bounded Git ref required")
    root = Path(root).resolve()
    commit = _git_output(root, ["rev-parse", "--verify", "--end-of-options", f"{git_ref}^{{commit}}"], 128).strip().decode("ascii")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        raise ValueError("Git ref did not resolve to a full commit")
    def changed_files():
        raw = _git_output(root, ["diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z", commit, "--"], max_files * 1025)
        paths = sorted({_safe_path(p.decode("utf-8")) for p in raw.split(b"\0") if p})
        if len(paths) > max_files:
            raise ValueError("Changed path count exceeds max_files")
        return paths
    paths = changed_files()
    with RepositoryIndex(root, state_dir=state_dir) as index:
        metadata = index.metadata()
        report: dict[str, Any] = {"schema": "tare.tools/review-bundle/1", "status": "READY",
                                  "authority": "NONE", "root": str(root), "revision_seq": metadata["revision_seq"],
                                  "content_hash": metadata["content_hash"], "selector": {"git_ref": git_ref, "commit": commit,
                                  "comparison": "commit_to_current_worktree", "untracked_files": "NOT_SELECTED",
                                  "historical_graph": False}, "files": [], "symbols": [], "documents": [],
                                  "test_relations": [], "coverage": metadata["coverage"],
                                  "freshness": {"status": "SELECTED_FILES_CHECKED", "other_files": "NOT_CHECKED",
                                                "atomic_source_snapshot": False}, "omitted": []}
        observed = {}
        mismatch = []
        for path in paths:
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Diff source escapes explicit repository")
            row = index.connection.execute("SELECT sha256, byte_size FROM files WHERE file_path=?", (path,)).fetchone()
            if not target.exists():
                if row:
                    mismatch.append({"path": path, "reason": "DELETED_AFTER_INDEX"})
                else:
                    report["files"].append({"path": path, "status": "DELETED", "historical_symbols": "NOT_AVAILABLE"})
                observed[path] = None
                continue
            if target.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Selected file exceeds 2 MiB source-check limit")
            observed[path] = _source_hash(target)
            if row is None:
                mismatch.append({"path": path, "reason": "NOT_INDEXED"})
            elif observed[path] != row[0]:
                mismatch.append({"path": path, "reason": "CONTENT_DIFFERS_FROM_SNAPSHOT"})
            else:
                report["files"].append({"path": path, "sha256": row[0], "status": "MATCHED_INDEXED_SOURCE"})
        if mismatch:
            report.update(status="SOURCE_MISMATCH", mismatches=mismatch, symbols=[], documents=[], test_relations=[])
            return _fit(report, max_bytes)
        if paths:
            placeholders = ",".join("?" for _ in paths)
            rows = index.connection.execute(f"SELECT payload FROM symbols WHERE file_path IN ({placeholders}) ORDER BY symbol_id LIMIT 513", paths).fetchall()
            if len(rows) > 512:
                report.update(status="SELECTION_TOO_LARGE", reason="Changed source contains more than 512 symbols")
                return _fit(report, max_bytes)
            from .query import _unit
            symbols = [json.loads(row[0]) for row in rows]
            report["symbols"] = [_unit(index, s, "git_diff_selected_indexed_source", details=True) for s in symbols]
            symbol_ids = [s["symbol_id"] for s in symbols]
            spec_ids = set()
            if symbol_ids:
                args = ",".join("?" for _ in symbol_ids)
                for row in index.connection.execute(f"SELECT target FROM edges WHERE source IN ({args}) AND relation='CODE_IMPLEMENTS_SPEC'", symbol_ids):
                    spec_ids.add(row[0])
            doc_rows = index.connection.execute(f"SELECT payload FROM documents WHERE file_path IN ({placeholders}) ORDER BY document_id", paths).fetchall()
            selected_docs = {json.loads(row[0])["document_id"]: json.loads(row[0]) for row in doc_rows}
            for spec_id in sorted(spec_ids):
                row = index.connection.execute("SELECT payload FROM documents WHERE document_id=?", (f"spec:{spec_id}",)).fetchone()
                if row:
                    doc = json.loads(row[0]); selected_docs[doc["document_id"]] = doc
            spec_ids.update(doc["node"]["id"] for doc in selected_docs.values() if doc.get("kind") == "spec" and "node" in doc)
            for spec_id in sorted(spec_ids):
                relation_rows = index.connection.execute("SELECT payload FROM edges WHERE (source=? OR target=?) ORDER BY source,relation,target LIMIT 513", (spec_id, spec_id)).fetchall()
                if len(relation_rows) > 512:
                    report.update(status="SELECTION_TOO_LARGE", reason="Intent relation count exceeds 512")
                    return _fit(report, max_bytes)
                for row in relation_rows:
                    edge = json.loads(row[0])
                    if edge["type"] == "TEST_VERIFIES_SPEC":
                        report["test_relations"].append(edge | {"evidence_kind": "DECLARED_VERIFIES", "execution_observed": False})
                    for node_id in (edge["from"], edge["to"]):
                        adr = index.connection.execute("SELECT payload FROM documents WHERE document_id=?", (f"adr:{node_id}",)).fetchone()
                        if adr:
                            doc = json.loads(adr[0]); selected_docs[doc["document_id"]] = doc
            for doc in sorted(selected_docs.values(), key=lambda d: d["document_id"]):
                hash_row = index.connection.execute("SELECT sha256 FROM files WHERE file_path=?", (doc["file_path"],)).fetchone()
                report["documents"].append({"document_id": doc["document_id"], "file_path": doc["file_path"],
                                            "source_root": str(root), "sha256": hash_row[0] if hash_row else None,
                                            "body": doc["body"], "acceptance_criteria": doc.get("node", {}).get("acceptance_criteria", {}),
                                            "source": "PERSISTED_SNAPSHOT", "current_body_read": False})
        # Recheck the selected bytes and path set; do not append facts after drift.
        for path, expected in observed.items():
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                raise ValueError("Selected source escaped root during review")
            actual = _source_hash(target) if target.is_file() else None
            if actual != expected:
                report.update(status="SOURCE_MISMATCH", reason="SELECTED_SOURCE_CHANGED_DURING_REVIEW", symbols=[], documents=[], test_relations=[])
                return _fit(report, max_bytes)
        if changed_files() != paths:
            report.update(status="SOURCE_MISMATCH", reason="DIFF_PATH_SET_CHANGED_DURING_REVIEW", symbols=[], documents=[], test_relations=[])
        elif any(f["status"] == "DELETED" for f in report["files"]):
            report["status"] = "PARTIAL"
            report["omitted"].append({"reason": "Deleted source has no historical graph in K=1 current snapshot"})
        if report["status"] in {"READY", "PARTIAL"}:
            for reference, doc in sorted(acquired.items()):
                report["documents"].append({"document_id": reference, "file_path": doc["path"],
                                            "repository": doc["repository"], "revision": doc["revision"],
                                            "sha256": doc["sha256"], "body": doc["body"],
                                            "acceptance_criteria": doc["acceptance_criteria"],
                                            "source": "CALLER_SELECTED_PINNED_OWNER_INTENT",
                                            "verification": doc["verification"], "current_body_read": False})
        return _fit(report, max_bytes)


serialize_review_bundle = serialize_federation
