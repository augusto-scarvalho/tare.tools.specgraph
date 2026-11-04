"""Explicit owner/pin acquisition and source-qualified mapping; never a catalog writer.

Metadata grounding remains separate from acquisition. Git blobs are read only
after a caller selects both the Library manifest and each owner checkout.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
from typing import Any

import yaml

from .ground import ground_repository
from .storage import RepositoryIndex

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._/-]*)@([0-9a-f]{40}|[0-9a-f]{64}):(.+)$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_DOCUMENT_BYTES = 262_144


def serialize_federation(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _fit(value: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 65536:
        raise ValueError("max_bytes must be an integer between 1024 and 65536")
    if len(serialize_federation(value).encode("utf-8")) <= max_bytes:
        return value
    return {"schema": value["schema"], "status": "BUDGET_EXCEEDED", "authority": "NONE",
            "reason": "Required complete source/criteria units exceed max_bytes; narrow the selection",
            "revision_seq": value.get("revision_seq"), "content_hash": value.get("content_hash"),
            "body_delivered": False, "truncated": True}


def _safe_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ValueError("Invalid bounded source path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Source path escapes repository or is not a POSIX relative path")
    return value


def _git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=10, check=False)
    if result.returncode:
        raise ValueError("Pinned Git object unavailable in the explicitly selected owner")
    return result.stdout


def read_pinned_document(owner_root: Path | str, revision: str, path: str, expected_sha256: str) -> bytes:
    """Acquire one size-bounded immutable blob, never the owner's working file."""
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision) or not _SHA.fullmatch(expected_sha256):
        raise ValueError("Full commit and lowercase SHA-256 are required")
    path = _safe_path(path)
    root = Path(owner_root).resolve()
    if _git(root, "cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("Document revision is not a Git commit")
    object_id = _git(root, "rev-parse", "--verify", f"{revision}:{path}").strip().decode("ascii")
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", object_id):
        raise ValueError("Invalid pinned object identity")
    if _git(root, "cat-file", "-t", object_id).strip() != b"blob":
        raise ValueError("Pinned source is not a document blob")
    if not 0 <= int(_git(root, "cat-file", "-s", object_id)) <= MAX_DOCUMENT_BYTES:
        raise ValueError("Pinned document exceeds acquisition byte limit")
    raw = _git(root, "cat-file", "blob", object_id)
    if len(raw) > MAX_DOCUMENT_BYTES or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Pinned document hash mismatch")
    return raw


def _criteria(text: str) -> tuple[str | None, dict[str, str]]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return None, {}
    metadata = yaml.safe_load(match.group(1))
    if not isinstance(metadata, dict):
        raise ValueError("Document frontmatter must be an object")
    criteria = metadata.get("acceptance_criteria", metadata.get("criteria", {}))
    if not isinstance(criteria, dict) or len(criteria) > 64:
        raise ValueError("Admission requires explicit acceptance criterion IDs")
    if not all(isinstance(key, str) and isinstance(value, str) and key and value for key, value in criteria.items()):
        raise ValueError("Acceptance criteria must map nonempty strings to strings")
    return metadata.get("id"), criteria


def acquire_federated_documents(manifest_path: Path | str, repository: str,
                               owner_roots: dict[str, Path | str], *, include_ids: list[str] | tuple[str, ...],
                               max_bytes: int = 8192) -> dict[str, Any]:
    """Read selected pinned bodies; READY metadata alone never sets body_read.

@spec SPEC-SPECGRAPH-C5
    """
    _fit({"schema": "validation"}, max_bytes)
    if not include_ids or len(include_ids) > 32:
        raise ValueError("Select between 1 and 32 explicit document IDs")
    grounding = ground_repository(manifest_path, repository, include_ids=include_ids, max_bytes=65536)
    envelope: dict[str, Any] = {"schema": "tare.tools/federated-source-acquisition/1", "authority": "NONE",
                               "status": grounding.status, "metadata_only": True, "body_read": False,
                               "body_delivered": False, "manifest_sha256": grounding.manifest_sha256,
                               "grounding_scope_sha256": grounding.grounding_scope_sha256,
                               "repository": repository, "documents": []}
    if grounding.status != "READY":
        envelope["reason"] = grounding.reason_code
        return _fit(envelope, max_bytes)
    for entry in grounding.entries:
        match = _PIN.fullmatch(entry.relative_path)
        if not match:
            envelope.update(status="UNPINNED_SOURCE", reason="Federated acquisition requires repository@full_commit:path")
            envelope["documents"] = []
            return _fit(envelope, max_bytes)
        owner, revision, path = match.groups()
        if owner != repository or entry.semantic_document_id != f"{owner}:{path}":
            envelope.update(status="SOURCE_MISMATCH", reason="Repository-qualified identity disagrees with owner/pin")
            envelope["documents"] = []
            return _fit(envelope, max_bytes)
        if owner not in owner_roots:
            envelope.update(status="UNAVAILABLE", reason="Explicit owner checkout is missing")
            envelope["documents"] = []
            return _fit(envelope, max_bytes)
        try:
            raw = read_pinned_document(owner_roots[owner], revision, path, entry.sha256)
            text = raw.decode("utf-8")
            semantic_id, criteria = _criteria(text)
        except (ValueError, OSError, subprocess.SubprocessError, UnicodeError, yaml.YAMLError):
            envelope.update(status="SOURCE_INVALID", reason="Pinned body could not be acquired and verified")
            envelope["documents"] = []  # Never present a partial set as complete intent.
            return _fit(envelope, max_bytes)
        envelope["documents"].append({"semantic_document_id": entry.semantic_document_id,
                                      "declared_id": semantic_id, "repository": owner, "path": path,
                                      "revision": revision, "sha256": entry.sha256, "body": text,
                                      "body_read": True, "body_delivered": True, "acceptance_criteria": criteria,
                                      "criterion_sha256": {key: hashlib.sha256(value.encode()).hexdigest() for key, value in criteria.items()},
                                      "verification": "PINNED_GIT_BLOB_SHA256", "authority_state": entry.authority_state})
    envelope.update(status="READY", metadata_only=False, body_read=True, body_delivered=True)
    return _fit(envelope, max_bytes)


def _verified_documents(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(documents) > 32:
        raise ValueError("At most 32 acquired documents are supported")
    indexed = {}
    for doc in documents:
        reference = doc.get("semantic_document_id")
        text = doc.get("body")
        if (not isinstance(reference, str) or reference in indexed or not isinstance(text, str)
                or not doc.get("body_read") or not doc.get("body_delivered")
                or hashlib.sha256(text.encode()).hexdigest() != doc.get("sha256")):
            raise ValueError("Acquired source is missing, duplicated or hash-mismatched")
        _, criteria = _criteria(text)
        if criteria != doc.get("acceptance_criteria"):
            raise ValueError("Acquired acceptance criteria differ from source body")
        if reference != f"{doc.get('repository')}:{doc.get('path')}":
            raise ValueError("Source identity is not repository-qualified")
        if (not isinstance(doc.get("revision"), str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", doc["revision"])
                or doc.get("verification") != "PINNED_GIT_BLOB_SHA256"):
            raise ValueError("Source requires an explicit pinned acquisition receipt")
        _safe_path(doc["path"])
        indexed[reference] = doc
    return indexed


def resolve_intent_mappings(root: Path | str, mappings: list[dict[str, Any]],
                            documents: list[dict[str, Any]], *, repository: str,
                            state_dir: Path | str | None = None, max_bytes: int = 8192) -> dict[str, Any]:
    """Bind explicit source/AC/hash to exact symbol IDs; names yield candidates only.

    This returns declared mapping proposals. It neither mutates indexed edges nor
    approves admission; Work ownership and final acceptance remain external.

@spec SPEC-SPECGRAPH-C5
    """
    _fit({"schema": "validation"}, max_bytes)
    if not isinstance(mappings, list) or not 1 <= len(mappings) <= 128:
        raise ValueError("Mappings must contain 1..128 explicit records")
    docs = _verified_documents(documents)
    results = []
    with RepositoryIndex(root, state_dir=state_dir) as index:
        metadata = index.metadata()
        for mapping in mappings:
            if not isinstance(mapping, dict) or set(mapping) != {"source_ref", "source_sha256", "criteria", "symbol_id"}:
                raise ValueError("Mapping requires source_ref/source_sha256/criteria/symbol_id only")
            source = docs.get(mapping["source_ref"])
            criteria = mapping["criteria"]
            selector = mapping["symbol_id"]
            if not isinstance(criteria, list) or not all(isinstance(c, str) for c in criteria) or len(set(criteria)) != len(criteria):
                raise ValueError("Mapping criteria must be unique explicit IDs")
            if not isinstance(selector, str) or not selector or len(selector.encode()) > 512:
                raise ValueError("Bounded symbol selector required")
            record = {"mapping": mapping, "relation": "DECLARED_IMPLEMENTS", "authority": "NONE"}
            if source is None or source["sha256"] != mapping["source_sha256"]:
                record.update(status="SOURCE_MISMATCH", candidates=[])
            elif any(c not in source["acceptance_criteria"] for c in criteria):
                record.update(status="AC_MISMATCH", candidates=[])
            else:
                prefix = repository + ":"
                if selector.count(":") >= 2 and not selector.startswith(prefix):
                    record.update(status="REPOSITORY_MISMATCH", candidates=[])
                    results.append(record)
                    continue
                local_id = selector[len(prefix):] if selector.startswith(prefix) else selector
                hit = index.connection.execute("SELECT symbol_id FROM symbols WHERE symbol_id=?", (local_id,)).fetchone()
                if hit is not None:
                    record.update(status="BOUND", symbol_id=f"{repository}:{hit[0]}", source_revision=source["revision"])
                else:
                    hits = index.search(local_id, limit=10)
                    record.update(status="CANDIDATES" if hits else "UNRESOLVED", candidates=[f"{repository}:{h['symbol_id']}" for h in hits],
                                  rebind_applied=False)
            results.append(record)
        return _fit({"schema": "tare.tools/intent-mapping-projection/1", "authority": "NONE",
                     "status": "READY" if all(r["status"] == "BOUND" for r in results) else "NEEDS_REVIEW",
                     "revision_seq": metadata["revision_seq"], "content_hash": metadata["content_hash"],
                     "freshness": metadata["freshness"], "repository": repository, "mappings": results}, max_bytes)


def check_work_source_binding(work: dict[str, Any], source: dict[str, Any], criteria: list[str], *,
                              expected_source_sha256: str | None = None) -> dict[str, Any]:
    """Read-only admission prerequisite; validates source/AC identity, not Authority."""
    verified = _verified_documents([source])
    reference = source["semantic_document_id"]
    scopes = work.get("repository_scopes", [])
    matching = [scope for scope in scopes if scope.get("repository") == source["repository"]]
    reasons = []
    if expected_source_sha256 is None:
        reasons.append("WORK_SOURCE_PIN_MISSING")
    elif expected_source_sha256 != source["sha256"]:
        reasons.append("WORK_SOURCE_HASH_MISMATCH")
    if len(matching) != 1 or reference not in matching[0].get("grounding_refs", []):
        reasons.append("WORK_SOURCE_MISMATCH")
    if work.get("spec_ref") and work["spec_ref"] != reference:
        reasons.append("WORK_SPEC_MISMATCH")
    if not criteria or any(c not in verified[reference]["acceptance_criteria"] for c in criteria):
        reasons.append("AC_MISMATCH")
    return {"schema": "tare.tools/work-source-binding/1", "status": "REFUSED" if reasons else "MATCHED",
            "reason_codes": reasons, "work_id": work.get("id"), "source_ref": reference,
            "source_sha256": source["sha256"], "source_revision": source["revision"],
            "criteria": criteria, "authority": "NONE", "work_modified": False,
            "note": "Source identity prerequisite only; final admission and Work status remain with their owners"}
