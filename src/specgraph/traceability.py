"""Exact, source-bound declarations for indexed code and schema traceability.

Mappings are declarations, never execution evidence or Authority. They do not
inherit across members, reinterpret names or repair stale source/criterion IDs.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

import yaml

SCHEMA = "tare.tools/traceability-mappings/1"
MAX_MAPPING_BYTES = 1024 * 1024
MAX_MAPPING_FILES = 16
MAX_MAPPINGS = 2048
MAX_SOURCE_BYTES = 262144
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 1024:
        raise ValueError("invalid bounded path")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value
            or str(path) != value or any(ord(c) < 32 for c in value)):
        raise ValueError("noncanonical or escaping path")
    return value


def mapping_files(root: Path, config: Any) -> tuple[dict[str, tuple[Path, str]], list[str]]:
    """Discover only explicitly selected mapping files, including missing errors."""
    files, errors = {}, []
    if len(config.intent_mapping_paths) > MAX_MAPPING_FILES:
        return {}, ["Traceability mapping file count exceeds limit"]
    for item in config.intent_mapping_paths:
        try:
            relative = _relative(item)
            path = root / relative
            if (path.is_symlink() or not path.resolve().is_relative_to(root.resolve())
                    or not path.is_file() or path.suffix.lower() != ".json"):
                raise ValueError("missing, non-JSON or unsafe mapping file")
            if config.is_path_excluded(relative, config.excluded_paths):
                raise ValueError("mapping file conflicts with excluded scope")
            if relative in files:
                raise ValueError("duplicate mapping file")
            if path.stat().st_size > MAX_MAPPING_BYTES:
                raise ValueError("mapping file exceeds byte limit")
            files[relative] = (path, "intent_mapping")
        except (OSError, ValueError) as exc:
            errors.append(f"Traceability mapping selection {item!r}: {exc}")
    return files, sorted(set(errors))


def _read(root: Path, path: str, inputs: dict[str, bytes] | None, maximum: int) -> bytes:
    if inputs is not None:
        raw = inputs[path]
    else:
        source = root / _relative(path)
        if source.is_symlink() or not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("source escapes selected owner")
        with source.open("rb") as stream:
            raw = stream.read(maximum + 1)
    if not isinstance(raw, bytes) or len(raw) > maximum:
        raise ValueError("source exceeds byte limit")
    return raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def apply_declared_mappings(matrix: Any, *, inputs: dict[str, bytes] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve exact repo:path:target and pinned source/AC into declared edges.

    @spec SPEC-SPECGRAPH-C5:AC-01
    """
    root, config = matrix.root_dir.resolve(), matrix.config
    selected, errors = mapping_files(root, config)
    edges: list[dict[str, Any]] = []
    if errors:
        return [], errors
    source_nodes = {f"{config.project_name}:{node.file_path}": node for node in matrix.specs.values()}
    source_cache: dict[str, tuple[str, Any]] = {}
    targets = {"code": (matrix.symbols, "CODE_IMPLEMENTS_SPEC"),
               "schema": (matrix.schemas, "SCHEMA_IMPLEMENTS_SPEC"),
               "test": (matrix.tests, "TEST_VERIFIES_SPEC")}
    known_test_files = {test.file_path for test in matrix.tests.values()}
    seen = set()
    total = 0
    for path in sorted(selected):
        try:
            raw = _read(root, path, inputs, MAX_MAPPING_BYTES)
            payload = json.loads(raw, object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or set(payload) != {"schema", "authority", "mappings"}
                    or payload["schema"] != SCHEMA or payload["authority"] != "NONE"):
                raise ValueError("unsupported mapping envelope")
            rows = payload["mappings"]
            if not isinstance(rows, list) or len(rows) > MAX_MAPPINGS or total + len(rows) > MAX_MAPPINGS:
                raise ValueError("mapping count exceeds limit")
            total += len(rows)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            errors.append(f"Traceability mapping {path}: invalid or unavailable envelope")
            continue
        manifest_sha = hashlib.sha256(raw).hexdigest()
        for position, row in enumerate(rows):
            try:
                if not isinstance(row, dict) or set(row) != {"source_ref", "source_sha256", "criteria", "target_kind", "target_id", "rationale", "test_refs"}:
                    raise ValueError("invalid mapping fields")
                reference, digest = row["source_ref"], row["source_sha256"]
                if not isinstance(reference, str) or reference not in source_nodes:
                    raise ValueError("source-qualified SPEC is absent from selected owner scope")
                if not isinstance(digest, str) or not _SHA.fullmatch(digest):
                    raise ValueError("source SHA-256 is required")
                spec = source_nodes[reference]
                if reference not in source_cache:
                    source = _read(root, spec.file_path, inputs, MAX_SOURCE_BYTES)
                    normalized = source.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                    from .spec_parser import SpecParser
                    match = SpecParser.FRONTMATTER_PATTERN.match(normalized)
                    if not match or yaml.safe_load(match.group(1)) != spec.raw_frontmatter or match.group(2).strip() != spec.body:
                        raise ValueError("source bytes disagree with parsed SPEC snapshot")
                    source_cache[reference] = (hashlib.sha256(source).hexdigest(), spec)
                if source_cache[reference][0] != digest:
                    raise ValueError("source hash mismatch; explicit revalidation required")
                criteria = row["criteria"]
                if (not isinstance(criteria, list) or not 1 <= len(criteria) <= 64
                        or any(not isinstance(ac, str) or ac not in spec.acceptance_criteria for ac in criteria)
                        or len(set(criteria)) != len(criteria)):
                    raise ValueError("missing, duplicate or unknown exact AC")
                kind, target = row["target_kind"], row["target_id"]
                if kind not in targets or not isinstance(target, str) or not target.startswith(config.project_name + ":"):
                    raise ValueError("target owner or kind mismatch")
                local = target[len(config.project_name) + 1:]
                records, relation = targets[kind]
                if local not in records:
                    raise ValueError("exact target absent; names/globs/rebinding are not supported")
                if not isinstance(row["rationale"], str) or not row["rationale"].strip() or len(row["rationale"].encode()) > 2048:
                    raise ValueError("bounded mapping rationale is required")
                refs = row["test_refs"]
                if not isinstance(refs, list) or len(refs) > 16:
                    raise ValueError("test references must be bounded declarations")
                for ref in refs:
                    _relative(ref)
                    if ref not in known_test_files:
                        raise ValueError(f"test reference absent from indexed test scope: {ref!r}; test_paths={config.test_paths!r}")
                if kind == "test" and records[local].file_path not in refs:
                    raise ValueError("test target requires its exact source file reference")
                identity = (reference, kind, target, tuple(sorted(criteria)))
                if identity in seen:
                    raise ValueError("duplicate exact mapping")
                seen.add(identity)
                for ac in sorted(criteria):
                    edges.append({"from": local, "to": spec.id, "type": relation,
                        "verified_criterion" if kind == "test" else "target_criterion": f"{spec.id}:{ac}",
                        "origin": "declared_exact_mapping",
                        "source_ref": reference, "source_sha256": digest, "target_ref": target,
                        "mapping_path": path, "mapping_sha256": manifest_sha,
                        "evidence_kind": "DECLARED_TEST_LINK" if kind == "test" else "DECLARATION_ONLY", "authority": "NONE"})
            except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, yaml.YAMLError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "invalid source or mapping shape"
                diagnostic = f"Traceability mapping {path}[{position}]: {reason}"
                encoded = diagnostic.encode("utf-8")
                if len(encoded) > 2048:
                    marker = " [diagnostic truncated]"
                    diagnostic = encoded[:2048 - len(marker)].decode("utf-8", errors="ignore") + marker
                errors.append(diagnostic)
    return edges, sorted(set(errors))
