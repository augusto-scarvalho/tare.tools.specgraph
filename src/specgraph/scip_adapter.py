"""Read-only adapter for the explicit SCIP protobuf-JSON occurrence subset.

Protocol source: https://github.com/sourcegraph/scip/blob/main/scip.proto
The protocol enum currently has only version 0 (UnspecifiedProtocolVersion).
This is distinct from an indexer/CLI release such as 0.6. Binary protobuf input,
symbol grammar parsing, signature documents and relationship enrichment are not
implemented here and are never silently promoted into the operational index.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

POSITION_ENCODINGS = {
    1: "UTF8CodeUnitOffsetFromLineStart", 2: "UTF16CodeUnitOffsetFromLineStart",
    3: "UTF32CodeUnitOffsetFromLineStart",
}


def _field(mapping: dict[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    if camel in mapping and snake in mapping:
        raise ValueError(f"Duplicate JSON aliases for {camel}")
    return mapping.get(camel, mapping.get(snake, default))


def import_scip_json(payload: dict[str, Any], root: Path | str, *, max_occurrences: int = 100_000,
                     max_bytes: int = 16_000_000) -> dict[str, Any]:
    """Validate and project v0 symbol/occurrence facts, without changing an index.

    Accepts standard camelCase protobuf JSON names or preserved snake_case names.
    Local SCIP symbols are document-qualified. Ranges retain their original
    coordinate encoding and are not misrepresented as UTF-8 byte columns.

@spec SPEC-SPECGRAPH-C6
    """
    if not isinstance(payload, dict):
        raise ValueError("Expected SCIP protobuf-JSON object; binary SCIP is not supported")
    if not 1 <= max_occurrences <= 1_000_000 or not 1024 <= max_bytes <= 64_000_000:
        raise ValueError("Invalid adapter budget")
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(serialized) > max_bytes:
        raise ValueError("SCIP input exceeds max_bytes")
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("SCIP metadata is required")
    protocol = metadata.get("version", 0)
    if isinstance(protocol, bool) or protocol not in (0, "UnspecifiedProtocolVersion"):
        raise ValueError("Only SCIP ProtocolVersion 0 / UnspecifiedProtocolVersion is supported")
    root = Path(root).resolve()
    provided = _field(metadata, "projectRoot", "project_root")
    if not isinstance(provided, str):
        raise ValueError("metadata.projectRoot file URI is required")
    uri, expected = urlsplit(provided), urlsplit(root.as_uri())
    actual_path, expected_path = unquote(uri.path).rstrip("/"), unquote(expected.path).rstrip("/")
    if os.name == "nt":
        actual_path, expected_path = actual_path.casefold(), expected_path.casefold()
    if uri.scheme != "file" or uri.netloc not in ("", "localhost") or uri.query or uri.fragment or actual_path != expected_path:
        raise ValueError("SCIP project root does not match the selected repository")
    documents = payload.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("SCIP documents must be a list")
    occurrences, symbols, seen_paths, omissions = [], [], set(), set()
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError("SCIP document must be an object")
        path = _field(document, "relativePath", "relative_path")
        if not isinstance(path, str) or not path or "\\" in path or "\x00" in path or ":" in path or any(part in ("", ".", "..") for part in path.split("/")):
            raise ValueError("SCIP relativePath must be a canonical repository-relative path")
        if path in seen_paths:
            raise ValueError(f"Duplicate SCIP document: {path}")
        seen_paths.add(path)
        try:
            (root / path).resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("SCIP document escapes repository") from exc
        if (root / path).is_symlink():
            raise ValueError("SCIP documents cannot be symbolic links")
        encoding = _field(document, "positionEncoding", "position_encoding")
        if isinstance(encoding, bool):
            raise ValueError("Invalid positionEncoding")
        if isinstance(encoding, int):
            encoding = POSITION_ENCODINGS.get(encoding)
        if encoding not in POSITION_ENCODINGS.values():
            raise ValueError("Explicit supported document.positionEncoding is required")
        information = document.get("symbols", [])
        if not isinstance(information, list):
            raise ValueError("Document symbols must be a list")
        for item in information:
            if not isinstance(item, dict) or not isinstance(item.get("symbol"), str) or not item["symbol"]:
                raise ValueError("SymbolInformation requires a nonempty symbol")
            local = item["symbol"].startswith("local ")
            symbols.append({"symbol": item["symbol"], "document_scope": path if local else None,
                            "documentation": item.get("documentation", []), "kind": item.get("kind", 0),
                            "file_path": path, "origin": "scip_indexer"})
            if item.get("relationships"):
                omissions.add("symbol_relationships")
            if "signatureDocumentation" in item or "signature_documentation" in item:
                omissions.add("signature_documents")
        items = document.get("occurrences", [])
        if not isinstance(items, list):
            raise ValueError("Document occurrences must be a list")
        for occurrence in items:
            if not isinstance(occurrence, dict):
                raise ValueError("Occurrence must be an object")
            symbol, positions = occurrence.get("symbol"), occurrence.get("range")
            if not isinstance(symbol, str) or not symbol:
                raise ValueError("Occurrence requires a nonempty symbol")
            if not isinstance(positions, list) or len(positions) not in (3, 4) or any(type(value) is not int or value < 0 for value in positions):
                raise ValueError("Occurrence range must have 3 or 4 nonnegative integers")
            start_line, start_column = positions[:2]
            end_line, end_column = (start_line, positions[2]) if len(positions) == 3 else positions[2:]
            if (end_line, end_column) < (start_line, start_column):
                raise ValueError("Occurrence range ends before it starts")
            roles = _field(occurrence, "symbolRoles", "symbol_roles", 0)
            if type(roles) is not int or roles < 0:
                raise ValueError("symbolRoles must be a nonnegative bitset")
            occurrences.append({"symbol": symbol, "document_scope": path if symbol.startswith("local ") else None,
                                "file_path": path, "language": document.get("language", ""),
                                "range": [start_line, start_column, end_line, end_column], "line_base": 0,
                                "position_encoding": encoding, "symbol_roles": roles,
                                "is_definition": bool(roles & 1), "origin": "scip_indexer", "source_verified": False})
            if len(occurrences) > max_occurrences:
                raise ValueError("SCIP input exceeds max_occurrences; no partial import was produced")
    if payload.get("externalSymbols") or payload.get("external_symbols"):
        omissions.add("external_symbols")
    return {"schema": "tare.tools/specgraph-scip/1", "protocol_version": 0,
            "representation": "protobuf_json", "subset": "symbols_and_occurrences_v1",
            "root": str(root), "input_sha256": hashlib.sha256(serialized).hexdigest(),
            "indexer": _field(metadata, "toolInfo", "tool_info", {}), "source_verification": "NOT_PERFORMED",
            "semantic_completeness": "NOT_CLAIMED", "runtime_proof": False, "index_mutated": False,
            "symbols": symbols, "occurrences": occurrences, "omitted": sorted(omissions)}
