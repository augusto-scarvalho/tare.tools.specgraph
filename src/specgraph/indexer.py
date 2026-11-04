"""Content-validated incremental extraction and atomic read-model publication."""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from .cache import IncrementalCacheEngine
from .config import SpecGraphConfig
from .graph import CausalTraceabilityMatrix, canonical_json
from .adr_parser import AdrNode, AdrParser
from .api_parser import ApiEndpoint, ApiAndMcpParser, McpToolContract
from .code_parser import CodeParser, CodeSymbol
from .schema_parser import JSONSchemaParser, SQLSchemaParser, SchemaContract, SchemaField, SchemaParser
from .spec_parser import SpecNode, SpecParser
from .test_parser import TestParser, TestSymbol
from .lexical import PROJECTION_VERSION
from .storage import (SCHEMA_VERSION, _initialize, _metadata, _publish, _validate_root,
                      index_path, repository_identity)

EXTRACTOR_VERSION = "python-ast-resolution-2-file-cache-4-crossfile-aliases"

_GROUP_TYPES = {"adrs": (AdrNode, "id", "ADR ID"), "specs": (SpecNode, "id", "Spec ID"),
                "schemas": (SchemaContract, "schema_id", "Schema ID"),
                "apis": (ApiEndpoint, "endpoint_id", "API Endpoint"),
                "mcp_tools": (McpToolContract, "tool_id", "MCP Tool ID"),
                "symbols": (CodeSymbol, "symbol_id", "Symbol ID"),
                "tests": (TestSymbol, "test_id", "Test ID")}


def _language_capabilities() -> dict[str, Any]:
    try:
        from .extractors import language_capabilities
    except ModuleNotFoundError:
        return {}
    return language_capabilities()


def _decode_record(group: str, value: dict[str, Any]) -> Any:
    value = dict(value)
    if group == "schemas":
        value["fields"] = [SchemaField(**item) for item in value.get("fields", [])]
    return _GROUP_TYPES[group][0](**value)


def _configured_files(root: Path, configured: list[str], files: dict[str, Any],
                      extensions: tuple[str, ...]) -> list[str]:
    """Select observed files; discovery and SHA observation happen once per pass."""
    scopes = []
    for path in configured:
        try:
            scopes.append((root / path).resolve().relative_to(root).as_posix())
        except ValueError:
            continue  # _observe/config validation reports paths outside the root.
    return [path for path in sorted(files)
            if Path(path).suffix.lower() in extensions
            and any(scope == "." or path == scope or path.startswith(scope + "/")
                    for scope in scopes)]


def _parse_extraction(root: Path, config: SpecGraphConfig, path: str,
                      extractor: str) -> dict[str, Any]:
    """Return unassembled per-file records, including negative/empty results."""
    source = root / path
    if extractor == "code":
        if source.suffix.lower() == ".py":
            symbols, errors = CodeParser.parse_file(source, root, source_roots=config.python_source_roots)
        else:
            try:
                from .extractors import TreeSitterCodeParser
                symbols, errors = TreeSitterCodeParser.parse_file(source, root)
            except ModuleNotFoundError:
                symbols, errors = [], [f"UNAVAILABLE language extractor for {path}"]
        groups = {"symbols": symbols}
    elif extractor == "api":
        apis, tools, errors = ApiAndMcpParser.parse_file(source, root)
        groups = {"apis": apis, "mcp_tools": tools}
    elif extractor == "test":
        tests, errors = TestParser.parse_file(source, root)
        groups = {"tests": tests}
    elif extractor == "spec":
        specs, errors = SpecParser.parse_file(source, root)
        groups = {"specs": specs}
    elif extractor == "adr":
        adrs, errors = AdrParser.parse_file(source, root)
        groups = {"adrs": adrs}
    else:
        parser = {".sql": SQLSchemaParser.parse_file, ".ddl": SQLSchemaParser.parse_file,
                  ".json": JSONSchemaParser.parse_file, ".py": SchemaParser.parse_python_models}
        schemas, errors = parser[source.suffix.lower()](source, root)
        groups = {"schemas": schemas}
    return {"groups": {group: [asdict(item) for item in items] for group, items in groups.items()},
            "errors": errors}


def _extract_matrix(root: Path, config: SpecGraphConfig, files: dict[str, Any],
                    connection: sqlite3.Connection, *, incremental: bool,
                    language_capabilities: dict[str, Any] | None = None) -> tuple[CausalTraceabilityMatrix, dict[str, Any]]:
    """Reuse extraction, then rebuild relations against the complete current set.

    Cache records precede cross-file binding, so edits to exports can invalidate
    resolved references without reparsing unchanged consumers. Parsing is delta
    bounded; discovery, hashing, deserialization and relation assembly are not.
    """
    connection.execute("""CREATE TABLE IF NOT EXISTS extractions (
        file_path TEXT NOT NULL, extractor TEXT NOT NULL, source_sha256 TEXT NOT NULL,
        config_digest TEXT NOT NULL, extractor_version TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY(file_path, extractor))""")
    config_digest = hashlib.sha256(canonical_json({"config": asdict(config),
        "language_capabilities": language_capabilities or {}}).encode("utf-8")).hexdigest()
    previous_keys = set(connection.execute("SELECT file_path, extractor FROM extractions"))
    active_keys: set[tuple[str, str]] = set()
    parsed_paths: set[str] = set()
    reused_paths: set[str] = set()
    counts = {"parsed_extractions": 0, "reused_extractions": 0, "invalid_cache_entries": 0}
    matrix = CausalTraceabilityMatrix(config=config, root_dir=root, parse_errors=list(config.config_errors))
    roles = (("adr", config.adr_paths, (".md", ".markdown")),
             ("spec", config.spec_paths, (".md", ".markdown")),
             ("schema", config.schema_paths, (".py", ".json", ".sql", ".ddl")),
             ("api", config.api_paths, (".py",)),
             ("code", config.indexed_paths if config.indexed_paths is not None else config.governed_paths,
              (".py", ".ts", ".tsx", ".rs")),
             ("test", config.test_paths, (".py",)))
    # Parsers consume captured immutable input, never the live worktree. This
    # binds extracted records to the observed SHA even across transient ABA edits.
    # Source stability checks still reject different observations before/after;
    # the repository as a whole is not claimed to be an atomic Git snapshot.
    with tempfile.TemporaryDirectory(prefix="specgraph-extraction-") as staging:
        staging_root = Path(staging)
        staged_files: set[str] = set()
        for extractor, paths, extensions in roles:
            expected_groups = {"adr": {"adrs"}, "spec": {"specs"}, "schema": {"schemas"},
                               "api": {"apis", "mcp_tools"}, "code": {"symbols"}, "test": {"tests"}}[extractor]
            for path in _configured_files(root, paths, files, extensions):
                if config.is_path_excluded(path, config.excluded_paths):
                    continue
                active_keys.add((path, extractor))
                cached = connection.execute(
                    "SELECT payload FROM extractions WHERE file_path=? AND extractor=? "
                    "AND source_sha256=? AND config_digest=? AND extractor_version=?",
                    (path, extractor, files[path]["sha256"], config_digest, EXTRACTOR_VERSION)
                ).fetchone() if incremental else None
                extraction = None
                decoded = None
                if cached:
                    try:
                        candidate = json.loads(cached[0])
                        # Error outcomes are retried, including transient parser I/O
                        # failures; old symbols are never carried into a broken file.
                        if not candidate["errors"]:
                            if set(candidate["groups"]) != expected_groups:
                                raise ValueError("Incomplete extraction cache groups")
                            decoded = {group: [_decode_record(group, item) for item in items]
                                       for group, items in candidate["groups"].items()}
                            if any(record.file_path != path for records in decoded.values() for record in records):
                                raise ValueError("Extraction cache belongs to another file")
                            extraction = candidate
                    except (ValueError, TypeError, KeyError, AttributeError):
                        counts["invalid_cache_entries"] += 1
                if extraction is None:
                    if path not in staged_files:
                        raw = (root / path).read_bytes()
                        if hashlib.sha256(raw).hexdigest() != files[path]["sha256"]:
                            raise SourceChangedError(f"Source changed before extraction capture: {path}")
                        target = staging_root / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(raw)
                        staged_files.add(path)
                    extraction = _parse_extraction(staging_root, config, path, extractor)
                    decoded = {group: [_decode_record(group, item) for item in items]
                               for group, items in extraction["groups"].items()}
                    connection.execute("INSERT OR REPLACE INTO extractions VALUES (?, ?, ?, ?, ?, ?)",
                                       (path, extractor, files[path]["sha256"], config_digest,
                                        EXTRACTOR_VERSION, canonical_json(extraction)))
                    counts["parsed_extractions"] += 1
                    parsed_paths.add(path)
                else:
                    counts["reused_extractions"] += 1
                    reused_paths.add(path)
                matrix.parse_errors.extend(extraction["errors"])
                for group, records in decoded.items():
                    destination = getattr(matrix, group)
                    _, identity, label = _GROUP_TYPES[group]
                    for record in records:
                        key = getattr(record, identity)
                        if key in destination:
                            location = record.file_path
                            if group in ("apis", "symbols", "tests"):
                                location += f":{record.start_line}"
                            warning = f"Duplicate {label} '{key}' in '{location}'"
                            if group == "specs":
                                warning += f" (overwriting '{destination[key].file_path}')"
                            matrix.duplicate_warnings.append(warning)
                        destination[key] = record
    for key in sorted(previous_keys - active_keys):
        connection.execute("DELETE FROM extractions WHERE file_path=? AND extractor=?", key)
    # Mapping manifests and their source documents participate in the exact
    # observed snapshot even when symbol extraction was reused. Never read a
    # second live source silently while interpreting a cached declaration.
    mapping_inputs: dict[str, bytes] = {}
    if config.intent_mapping_paths:
        from .traceability import MAX_MAPPING_BYTES, MAX_SOURCE_BYTES
        selected_paths = set(config.intent_mapping_paths) | {spec.file_path for spec in matrix.specs.values()}
        for path in sorted(selected_paths):
            if path not in files:
                continue  # Missing/unsafe mapping selection is diagnosed below.
            maximum = MAX_MAPPING_BYTES if path in config.intent_mapping_paths else MAX_SOURCE_BYTES
            with (root / path).open("rb") as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                continue  # Bounded mapping consumer reports missing/oversize input.
            if hashlib.sha256(raw).hexdigest() != files[path]["sha256"]:
                raise SourceChangedError(f"Mapping/source changed before capture: {path}")
            mapping_inputs[path] = raw
    matrix._assemble_and_validate_edges(mapping_inputs=mapping_inputs)
    return matrix, counts | {"strategy": "INCREMENTAL" if incremental else "FULL",
                            "parsed_files": len(parsed_paths), "reused_files": len(reused_paths),
                            "removed_extractions": len(previous_keys - active_keys),
                            "relation_assembly": "GLOBAL", "source_observation": "FULL_SHA256"}


class SourceChangedError(RuntimeError):
    """Source observation changed during extraction; no revision was published."""


def _observe(root: Path, config: SpecGraphConfig) -> tuple[dict[str, dict[str, Any]], list[str]]:
    discovered, errors = IncrementalCacheEngine._discover_all_project_files(config, root)
    # Retrieval-only code still belongs to the source observation, even when
    # the legacy governance cache does not discover it.
    for configured in config.indexed_paths if config.indexed_paths is not None else config.governed_paths:
        candidate = (root / configured).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            errors.append(f"Indexed path escapes repository: {configured}")
            continue
        if config.is_path_excluded(relative, config.excluded_paths):
            continue
        if candidate.is_dir():
            IncrementalCacheEngine._fast_scan_tree(root, candidate, (".py", ".ts", ".tsx", ".rs"), discovered,
                                                   "code", errors, config.excluded_paths)
        elif candidate.is_file() and candidate.suffix.lower() in (".py", ".ts", ".tsx", ".rs"):
            discovered.setdefault(relative, (candidate, "code"))
    for configured in config.search_paths:
        candidate = (root / configured).resolve()
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            errors.append(f"Search path escapes repository: {configured}")
            continue
        if config.is_path_excluded(relative, config.excluded_paths):
            continue
        extensions = (".md", ".markdown", ".mdown", ".txt", ".rst")
        if candidate.is_dir():
            IncrementalCacheEngine._fast_scan_tree(root, candidate, extensions, discovered,
                                                   "document", errors, config.excluded_paths)
        elif candidate.is_file() and candidate.suffix.lower() in extensions:
            discovered.setdefault(relative, (candidate, "document"))
    # Both names matter: introducing specgraph.yaml changes which config is used.
    for name in ("specgraph.yaml", ".specgraph.yaml"):
        candidate = root / name
        if candidate.is_file():
            discovered[name] = (candidate, "config")
    files = {}
    for relative, (path, kind) in sorted(discovered.items()):
        try:
            path.resolve().relative_to(root)
            content = path.read_bytes()
            files[relative] = {"sha256": hashlib.sha256(content).hexdigest(),
                               "byte_size": len(content), "artifact_kind": kind}
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot observe {relative}: {exc}")
    return files, sorted(set(errors))


def _payload(matrix: CausalTraceabilityMatrix) -> dict[str, Any]:
    groups = ("adrs", "specs", "schemas", "apis", "mcp_tools", "symbols", "tests")
    payload = {name: [asdict(record) for _, record in sorted(getattr(matrix, name).items())]
               for name in groups}
    payload.update({"schema_version": "1.0", "config": asdict(matrix.config),
                    "summary": matrix.summary_stats(),
                    "edges": sorted(matrix.edges, key=canonical_json),
                    "parse_errors": sorted(matrix.parse_errors),
                    "duplicate_warnings": sorted(matrix.duplicate_warnings),
                    "dangling_references": sorted(matrix.dangling_spec_refs),
                    "invalid_criteria": sorted(matrix.invalid_criteria_refs)})
    return payload


def _documents(root: Path, payload: dict[str, Any], files: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    documents = []
    errors = []
    represented = set()
    for group, kind in (("adrs", "adr"), ("specs", "spec")):
        for node in payload[group]:
            documents.append({"document_id": f"{kind}:{node['id']}", "kind": kind,
                              "file_path": node["file_path"], "title": node["title"],
                              "body": node["body"], "node": node})
            represented.add(node["file_path"])
    for path, info in sorted(files.items()):
        if info["artifact_kind"] == "document" and path not in represented:
            # Strict decode failure is an explicit partial-coverage diagnostic.
            try:
                raw = (root / path).read_bytes()
                if hashlib.sha256(raw).hexdigest() != info["sha256"]:
                    raise SourceChangedError(f"Document changed before capture: {path}")
                body = raw.decode("utf-8")
            except UnicodeError as exc:
                errors.append(f"Document decoding failure in {path}: {exc}")
                continue
            documents.append({"document_id": f"file:{path}", "kind": "document",
                              "file_path": path, "title": Path(path).stem, "body": body})
    return sorted(documents, key=lambda item: item["document_id"]), errors


def index_repository(root: Path | str, *, state_dir: Path | str | None = None,
                     incremental: bool = True, reuse_snapshot: bool = False) -> dict[str, Any]:
    """Publish a complete read model or leave the previous revision intact.

    Unchanged file/extractor results are reused; full mode bypasses extraction
    reuse. Both modes use the same assembly/publication path. Pre/post content
    checks detect observed worktree races, but do not establish
    a Git/atomic source snapshot or rule out transient ABA edits. Strong callers
    must index an immutable checkout. Governance failure does not veto faithful
    publication; extraction errors are visible as PARTIAL coverage.

@spec SPEC-SPECGRAPH-C2
@spec SPEC-SPECGRAPH-C3
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Repository root is not a directory: {root}")
    destination = index_path(root, state_dir=state_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(destination, isolation_level=None, timeout=5.0)
    try:
        fts5 = _initialize(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
        previous = _metadata(connection) if row else None
        if previous:
            _validate_root(previous, root)
        config = SpecGraphConfig.load(root)
        before, observation_errors = _observe(root, config)
        language_capabilities = _language_capabilities()
        configuration_digest = hashlib.sha256(canonical_json(asdict(config)).encode()).hexdigest()
        if (reuse_snapshot and incremental and previous and previous["status"] == "READY"
                and not observation_errors and previous.get("configuration_digest") == configuration_digest
                and previous["extractor_version"] == EXTRACTOR_VERSION
                and previous["capabilities"]["fts5"] == fts5
                and previous.get("retrieval_projection_version") == PROJECTION_VERSION
                and previous["coverage"].get("language_extractors") == language_capabilities):
            published_files = {path: {"sha256": sha, "byte_size": size, "artifact_kind": kind}
                               for path, sha, size, kind in connection.execute(
                                   "SELECT file_path,sha256,byte_size,artifact_kind FROM files")}
            if before == published_files:
                after_config = SpecGraphConfig.load(root)
                after, after_errors = _observe(root, after_config)
                if (before != after or asdict(config) != asdict(after_config) or after_errors
                        or language_capabilities != _language_capabilities()):
                    raise SourceChangedError("Repository changed during observation; previous snapshot retained")
                connection.execute("COMMIT")
                return previous | {"publication": "UNCHANGED", "index_run": {
                    "strategy": "SOURCE_VERIFIED_NOOP", "parsed_files": 0,
                    "relation_assembly": "NOT_RUN", "source_observation": "FULL_SHA256_BEFORE_AFTER"}}
        matrix, index_run = _extract_matrix(root, config, before, connection,
                                            incremental=incremental, language_capabilities=language_capabilities)
        payload = _payload(matrix)
        sources: dict[str, str] = {}
        source_errors: list[str] = []
        source_paths = {symbol["file_path"] for symbol in payload["symbols"]}
        source_paths.update(test["file_path"] for test in payload["tests"])
        for path in sorted(source_paths):
            try:
                cached_source = connection.execute(
                    "SELECT body FROM sources WHERE file_path=? AND sha256=?",
                    (path, before.get(path, {}).get("sha256", ""))).fetchone() if incremental else None
                if cached_source:
                    sources[path] = cached_source[0]
                    continue
                raw = (root / path).read_bytes()
                if path not in before or hashlib.sha256(raw).hexdigest() != before[path]["sha256"]:
                    raise SourceChangedError(f"Source changed before capture: {path}")
                sources[path] = raw.decode("utf-8")
            except (OSError, UnicodeError) as exc:
                source_errors.append(f"Source capture failure in {path}: {exc}")
        documents, document_errors = _documents(root, payload, before)
        coverage_errors = sorted(set(matrix.parse_errors + matrix.duplicate_warnings
                                     + observation_errors + document_errors + source_errors))
        governance = matrix.drift_check()
        after_config = SpecGraphConfig.load(root)
        after, after_errors = _observe(root, after_config)
        if (before != after or asdict(config) != asdict(after_config) or observation_errors != after_errors
                or language_capabilities != _language_capabilities()):
            raise SourceChangedError("Repository changed during indexing; previous snapshot retained, retry on stable sources")
        content = {"graph": payload, "files": before, "documents": documents,
                   "coverage_errors": coverage_errors, "store_schema": SCHEMA_VERSION,
                   "extractor_version": EXTRACTOR_VERSION, "source_paths": sorted(sources),
                   "language_capabilities": language_capabilities,
                   "retrieval_projection_version": PROJECTION_VERSION}
        content_hash = hashlib.sha256(canonical_json(content).encode("utf-8")).hexdigest()
        if (previous and previous["content_hash"] == content_hash and previous["capabilities"]["fts5"] == fts5
                and previous.get("retrieval_projection_version") == PROJECTION_VERSION):
            previous = previous | {"configuration_digest": configuration_digest}
            connection.execute("UPDATE metadata SET value=? WHERE key='snapshot'", (canonical_json(previous),))
            connection.execute("COMMIT")
            return previous | {"publication": "UNCHANGED", "index_run": index_run}
        metadata = {
            "schema_version": SCHEMA_VERSION, "repository_root": repository_identity(root),
            "retrieval_projection_version": PROJECTION_VERSION,
            "extractor_version": EXTRACTOR_VERSION,
            "configuration_digest": configuration_digest,
            "revision_seq": previous["revision_seq"] + 1 if previous else 1,
            "content_hash": content_hash, "status": "PARTIAL" if coverage_errors else "READY",
            "indexed_at": datetime.now(timezone.utc).isoformat(), "index_integrity": "PASS",
            "coverage": {"status": "PARTIAL" if coverage_errors else "COMPLETE_WITHIN_SCOPE",
                         "file_count": len(before), "symbol_count": len(payload["symbols"]),
                         "errors": coverage_errors, "languages": ["python"] + [
                             language for language, info in sorted(language_capabilities.items())
                             if info.get("status") == "AVAILABLE"],
                         "language_extractors": language_capabilities,
                         "indexed_paths": config.indexed_paths if config.indexed_paths is not None else config.governed_paths,
                         "spec_paths": config.spec_paths, "excluded_paths": config.excluded_paths,
                         "python_source_roots": config.python_source_roots,
                         "governed_paths": config.governed_paths, "search_paths": config.search_paths,
                         "scope": "configured paths and supported extractor file types"},
            "governance": {"status": governance["status"], "violations": governance["violations"]},
            "freshness": {"status": "SOURCE_SNAPSHOT", "current_worktree": "NOT_CHECKED_ON_READ",
                          "check_method": "content_hash_before_after_build", "atomic_source_snapshot": False},
            "capabilities": {"fts5": fts5, "document_search": "AVAILABLE" if fts5 else "UNAVAILABLE_FTS5",
                             "code_lexical_search": "AVAILABLE" if fts5 else "UNAVAILABLE_FTS5",
                             "source_ranges": True,
                             "indexing": "INCREMENTAL_EXTRACTION_GLOBAL_REBIND",
                             "watcher": False, "read_path_source_scans": False},
        }
        _publish(connection, payload, metadata, before, documents, fts5=fts5, sources=sources)
        connection.execute("COMMIT")
        # A pinned reader must not add seconds to indexing. Try TRUNCATE with
        # no busy wait, and fall back to PASSIVE if it cannot checkpoint now.
        connection.execute("PRAGMA busy_timeout=0")
        try:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint[0]:
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.OperationalError:
            pass  # Publication already committed; maintenance is best effort.
        return metadata | {"publication": "PUBLISHED", "index_run": index_run}
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()
