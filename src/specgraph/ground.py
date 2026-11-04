"""Bounded repository grounding projected from an explicit Library manifest."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MIN_OUTPUT_BYTES = 1_024
MAX_OUTPUT_BYTES = 65_536
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_LOWER_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COLLECTIONS = ("specs", "adrs", "experiments", "post_mortems")
_TYPE_ORDER = {"spec": 0, "adr": 1, "experiment": 2, "post_mortem": 3}
_ACTIVE_AUTHORITY_STATES = {"DECLARED_ACTIVE", "UNMANAGED_ACTIVE"}
_AUTHORITY_STATES = _ACTIVE_AUTHORITY_STATES | {"CONFLICT", "EXCLUDED"}


@dataclass(frozen=True)
class GroundingSource:
    relative_path: str
    semantic_document_id: str
    authority_state: str
    editorial_status: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class GroundingEntry:
    id: str
    semantic_document_id: str
    doc_type: str
    title: str
    relative_path: str
    sha256: str
    authority_state: str
    source_paths: tuple[GroundingSource, ...]
    acceptance_criteria: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_paths"] = [source.to_dict() for source in self.source_paths]
        data["acceptance_criteria"] = list(self.acceptance_criteria)
        return data


@dataclass(frozen=True)
class GroundingReport:
    schema: str
    status: str
    reason_code: str
    repository: str
    manifest_sha256: str
    manifest_version: str
    grounding_scope_sha256: str
    requested_ids: tuple[str, ...]
    matched_count: int
    returned_count: int
    omitted_count: int
    entries: tuple[GroundingEntry, ...]
    byte_count: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["requested_ids"] = list(self.requested_ids)
        data["entries"] = [entry.to_dict() for entry in self.entries]
        return data


def ground_repository(
    manifest_path: Path | str,
    repository: str,
    *,
    max_bytes: int = 8_192,
    include_ids: tuple[str, ...] | list[str] = (),
    expected_scope_sha256: str | None = None,
) -> GroundingReport:
    """Project a bounded, deterministic grounding envelope for one repository."""

    repo = _bounded_text(repository, "repository", 256)
    requested_ids = _requested_ids(include_ids)
    if not MIN_OUTPUT_BYTES <= max_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError(
            f"max_bytes must be between {MIN_OUTPUT_BYTES} and {MAX_OUTPUT_BYTES}"
        )
    if expected_scope_sha256 is not None and not _LOWER_SHA256.fullmatch(
        expected_scope_sha256
    ):
        raise ValueError("expected_scope_sha256 must be a lowercase SHA-256 digest")

    path = Path(manifest_path).expanduser().resolve()
    try:
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return _empty_report(
                "SOURCE_INVALID", "MANIFEST_TOO_LARGE", repo, requested_ids=requested_ids
            )
        raw = path.read_bytes()
    except OSError:
        return _empty_report(
            "UNAVAILABLE", "MANIFEST_UNAVAILABLE", repo, requested_ids=requested_ids
        )

    digest = hashlib.sha256(raw).hexdigest()
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("manifest root must be an object")
        version = _bounded_text(payload.get("version"), "manifest version", 64)
        selected = _select_entries(payload, repo, requested_ids, version)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        return _empty_report(
            "SOURCE_INVALID",
            "INVALID_MANIFEST_CONTRACT",
            repo,
            manifest_sha256=digest,
            requested_ids=requested_ids,
        )

    selected.sort(
        key=lambda entry: (
            _TYPE_ORDER.get(entry.doc_type, 99),
            entry.semantic_document_id.casefold(),
            entry.relative_path.casefold(),
        )
    )
    scope_sha256 = _grounding_scope_sha256(repo, requested_ids, selected)

    selected_ids = {
        identity
        for entry in selected
        for identity in (entry.id, entry.semantic_document_id)
    }
    if requested_ids and not set(requested_ids).issubset(selected_ids):
        return _finalize(
            status="INCOMPLETE_MAPPING",
            reason_code="REQUIRED_ID_NOT_MAPPED",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=len(selected),
            entries=(),
            omitted_count=len(selected),
        )

    if expected_scope_sha256 is not None and expected_scope_sha256 != scope_sha256:
        return _finalize(
            status="DRIFT",
            reason_code="GROUNDING_SCOPE_CHANGED",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=len(selected),
            entries=(),
            omitted_count=len(selected),
        )

    if not selected:
        return _finalize(
            status="NOT_MAPPED",
            reason_code="NO_TARGET_MAPPING",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=0,
            entries=(),
            omitted_count=0,
        )

    id_counts = Counter(entry.id for entry in selected)
    duplicate_ids = {entry_id for entry_id, count in id_counts.items() if count > 1}
    if duplicate_ids:
        return _finalize(
            status="AMBIGUOUS_SSOT",
            reason_code="DUPLICATE_DOCUMENT_ID",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=len(selected),
            entries=(),
            omitted_count=len(selected),
        )

    hash_counts = Counter(entry.sha256 for entry in selected)
    if any(count > 1 for count in hash_counts.values()):
        return _finalize(
            status="AMBIGUOUS_SSOT",
            reason_code="DUPLICATE_CONTENT_PAYLOAD",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=len(selected),
            entries=(),
            omitted_count=len(selected),
        )

    full = _finalize(
        status="READY",
        reason_code="OK",
        repository=repo,
        manifest_sha256=digest,
        manifest_version=version,
        grounding_scope_sha256=scope_sha256,
        requested_ids=requested_ids,
        matched_count=len(selected),
        entries=tuple(selected),
        omitted_count=0,
    )
    if full.byte_count <= max_bytes:
        return full

    included: list[GroundingEntry] = []
    for entry in selected:
        candidate = _finalize(
            status="READY_TRUNCATED",
            reason_code="OUTPUT_TRUNCATED",
            repository=repo,
            manifest_sha256=digest,
            manifest_version=version,
            grounding_scope_sha256=scope_sha256,
            requested_ids=requested_ids,
            matched_count=len(selected),
            entries=tuple(included + [entry]),
            omitted_count=len(selected) - len(included) - 1,
        )
        if candidate.byte_count > max_bytes:
            break
        included.append(entry)

    return _finalize(
        status="READY_TRUNCATED",
        reason_code="OUTPUT_TRUNCATED",
        repository=repo,
        manifest_sha256=digest,
        manifest_version=version,
        grounding_scope_sha256=scope_sha256,
        requested_ids=requested_ids,
        matched_count=len(selected),
        entries=tuple(included),
        omitted_count=len(selected) - len(included),
    )


def encode_grounding_report(report: GroundingReport) -> bytes:
    """Return the exact compact UTF-8 payload measured by ``byte_count``."""

    return json.dumps(
        report.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _select_entries(
    payload: dict[str, Any],
    repository: str,
    requested_ids: tuple[str, ...],
    version: str,
) -> list[GroundingEntry]:
    selected: list[GroundingEntry] = []
    requested = set(requested_ids)
    is_projection_v3 = version.startswith("3.")
    if is_projection_v3 and payload.get("authority_vocabulary") != "authority_state/v1":
        raise ValueError("unsupported authority vocabulary")
    for collection in _COLLECTIONS:
        values = payload.get(collection, [])
        if not isinstance(values, list):
            raise ValueError(f"{collection} must be a list")
        for raw_entry in values:
            if not isinstance(raw_entry, dict):
                raise ValueError(f"{collection} entries must be objects")
            targets = raw_entry.get("target_repositories", [])
            if not isinstance(targets, list) or not all(
                isinstance(item, str) for item in targets
            ):
                raise ValueError("target_repositories must be a string list")
            authority_state = raw_entry.get(
                "authority_state", "LEGACY_ACTIVE" if not is_projection_v3 else None
            )
            if is_projection_v3:
                if authority_state not in _AUTHORITY_STATES:
                    raise ValueError("unknown authority state")
                if authority_state not in _ACTIVE_AUTHORITY_STATES:
                    if targets:
                        raise ValueError("inactive authority must not target repositories")
                    continue
            if repository not in targets:
                continue
            criteria = raw_entry.get("acceptance_criteria", [])
            if not isinstance(criteria, list) or len(criteria) > 64:
                raise ValueError("acceptance_criteria must be a bounded list")
            entry_id = _bounded_text(raw_entry.get("id"), "entry id", 256)
            semantic_id = _bounded_text(
                raw_entry.get("semantic_document_id", entry_id),
                "semantic document id",
                512,
            )
            if requested and not requested.intersection({entry_id, semantic_id}):
                continue
            digest = _digest(raw_entry.get("sha256"))
            if is_projection_v3 and entry_id != digest:
                raise ValueError("projection entry id must equal sha256")
            relative_path = _bounded_text(
                raw_entry.get("relative_path"), "relative path", 1_024
            )
            sources = _grounding_sources(
                raw_entry.get("source_paths"),
                relative_path=relative_path,
                semantic_document_id=semantic_id,
                authority_state=str(authority_state),
                editorial_status=_bounded_text(
                    raw_entry.get("status", "LEGACY"), "editorial status", 256
                ),
                required=is_projection_v3,
            )
            selected.append(
                GroundingEntry(
                    id=entry_id,
                    semantic_document_id=semantic_id,
                    doc_type=_bounded_text(
                        raw_entry.get("doc_type"), "document type", 64
                    ),
                    title=_bounded_text(raw_entry.get("title"), "title", 2_048),
                    relative_path=relative_path,
                    sha256=digest,
                    authority_state=str(authority_state),
                    source_paths=sources,
                    acceptance_criteria=tuple(
                        _bounded_text(item, "acceptance criterion", 2_048)
                        for item in criteria
                    ),
                )
            )
    return selected


def _grounding_sources(
    value: Any,
    *,
    relative_path: str,
    semantic_document_id: str,
    authority_state: str,
    editorial_status: str,
    required: bool,
) -> tuple[GroundingSource, ...]:
    if not required:
        return (
            GroundingSource(
                relative_path=relative_path,
                semantic_document_id=semantic_document_id,
                authority_state=authority_state,
                editorial_status=editorial_status,
            ),
        )
    if not isinstance(value, list) or not value or len(value) > 512:
        raise ValueError("source_paths must be a bounded non-empty list")
    sources: list[GroundingSource] = []
    for raw_source in value:
        if not isinstance(raw_source, dict):
            raise ValueError("source_paths entries must be objects")
        source_state = raw_source.get("authority_state")
        if source_state not in _AUTHORITY_STATES:
            raise ValueError("unknown source authority state")
        sources.append(
            GroundingSource(
                relative_path=_bounded_text(
                    raw_source.get("relative_path"), "source relative path", 1_024
                ),
                semantic_document_id=_bounded_text(
                    raw_source.get("semantic_document_id"),
                    "source semantic document id",
                    512,
                ),
                authority_state=str(source_state),
                editorial_status=_bounded_text(
                    raw_source.get("editorial_status"),
                    "source editorial status",
                    256,
                ),
            )
        )
    paths = [source.relative_path for source in sources]
    if paths != sorted(paths, key=lambda path: path.encode("utf-8")):
        raise ValueError("source_paths must be deterministically sorted")
    if len(paths) != len(set(paths)) or relative_path not in paths:
        raise ValueError("source_paths must be unique and include relative_path")
    return tuple(sources)


def _requested_ids(value: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or len(value) > 128:
        raise ValueError("include_ids must be a bounded string sequence")
    normalized = tuple(_bounded_text(item, "include id", 256) for item in value)
    if len(set(normalized)) != len(normalized):
        raise ValueError("include_ids must not contain duplicates")
    return tuple(sorted(normalized, key=lambda item: (item.casefold(), item)))


def _grounding_scope_sha256(
    repository: str,
    requested_ids: tuple[str, ...],
    entries: list[GroundingEntry],
) -> str:
    scope = {
        "schema": "tare.tools/repository-grounding-scope/1",
        "repository": repository,
        "requested_ids": list(requested_ids),
        "entries": [entry.to_dict() for entry in entries],
    }
    encoded = json.dumps(
        scope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_text(value: Any, field: str, max_bytes: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    normalized = value.strip()
    if len(normalized.encode("utf-8")) > max_bytes:
        raise ValueError(f"{field} exceeds {max_bytes} bytes")
    return normalized


def _digest(value: Any) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("entry sha256 must be 64 hexadecimal characters")
    return value.lower()


def _empty_report(
    status: str,
    reason_code: str,
    repository: str,
    *,
    manifest_sha256: str = "",
    requested_ids: tuple[str, ...] = (),
) -> GroundingReport:
    return _finalize(
        status=status,
        reason_code=reason_code,
        repository=repository,
        manifest_sha256=manifest_sha256,
        manifest_version="",
        grounding_scope_sha256="",
        requested_ids=requested_ids,
        matched_count=0,
        entries=(),
        omitted_count=0,
    )


def _finalize(
    *,
    status: str,
    reason_code: str,
    repository: str,
    manifest_sha256: str,
    manifest_version: str,
    grounding_scope_sha256: str,
    requested_ids: tuple[str, ...],
    matched_count: int,
    entries: tuple[GroundingEntry, ...],
    omitted_count: int,
) -> GroundingReport:
    byte_count = 0
    for _ in range(8):
        report = GroundingReport(
            schema="tare.tools/repository-grounding/1",
            status=status,
            reason_code=reason_code,
            repository=repository,
            manifest_sha256=manifest_sha256,
            manifest_version=manifest_version,
            grounding_scope_sha256=grounding_scope_sha256,
            requested_ids=requested_ids,
            matched_count=matched_count,
            returned_count=len(entries),
            omitted_count=omitted_count,
            entries=entries,
            byte_count=byte_count,
        )
        measured = len(encode_grounding_report(report))
        if measured == byte_count:
            return report
        byte_count = measured
    raise RuntimeError("grounding byte_count did not converge")
