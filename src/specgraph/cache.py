from __future__ import annotations
import collections
from collections import deque
import hashlib
import json
import os
import re
import time
import threading
import weakref
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import SpecGraphConfig
from .spec_parser import SpecParser, SpecNode
from .code_parser import CodeParser, CodeSymbol
from .test_parser import TestParser, TestSymbol
from .schema_parser import SchemaParser, SchemaContract, SQLSchemaParser, JSONSchemaParser
from .api_parser import ApiAndMcpParser, ApiEndpoint, McpToolContract
from .adr_parser import AdrParser, AdrNode

CACHE_DIR_NAME = ".specgraph"
CACHE_FILE_NAME = "cache.json"
CACHE_SCHEMA_VERSION = "1.0.0"

# Only registry lookup is global; unrelated cache destinations never share a writer lock.
_manifest_locks_guard = threading.Lock()
_manifest_locks = weakref.WeakValueDictionary()

@dataclass
class FileFingerprint:
    """Cryptographic and filesystem metadata for a single tracked file."""
    rel_path: str
    mtime_ns: int
    byte_size: int
    sha256: str
    artifact_kind: str  # "spec", "code", "test", "schema", "api", "adr"
    extracted_nodes: list[str] = field(default_factory=list)
    last_parsed_at: str = ""

@dataclass
class CacheManifest:
    """Persisted audit manifest of indexed files and causal reverse dependencies."""
    schema_version: str = CACHE_SCHEMA_VERSION
    project_name: str = "tare.tools.project"
    last_indexed_at: str = ""
    generation_digest: str = ""
    files: dict[str, FileFingerprint] = field(default_factory=dict)
    reverse_deps: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_name": self.project_name,
            "last_indexed_at": self.last_indexed_at,
            "generation_digest": self.generation_digest,
            "files": {k: asdict(v) for k, v in sorted(self.files.items())},
            "reverse_deps": {k: sorted(v) for k, v in sorted(self.reverse_deps.items())}
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheManifest:
        if not isinstance(data, dict):
            raise ValueError("Manifest data must be a dictionary")
        if data.get("schema_version") != CACHE_SCHEMA_VERSION:
            raise ValueError(f"Schema version mismatch: expected {CACHE_SCHEMA_VERSION}, got {data.get('schema_version')}")

        files_raw = data.get("files")
        if not isinstance(files_raw, dict):
            raise ValueError("Manifest 'files' field must be a dictionary")

        files_dict = {}
        for k, v in files_raw.items():
            if not isinstance(v, dict):
                raise ValueError(f"File fingerprint entry '{k}' must be a dictionary")
            nodes_raw = v.get("extracted_nodes", [])
            if not isinstance(nodes_raw, list):
                nodes_raw = []
            files_dict[str(k)] = FileFingerprint(
                rel_path=str(v.get("rel_path", k)),
                mtime_ns=int(v.get("mtime_ns", 0)),
                byte_size=int(v.get("byte_size", 0)),
                sha256=str(v.get("sha256", "")),
                artifact_kind=str(v.get("artifact_kind", "unknown")),
                extracted_nodes=[str(n) for n in nodes_raw],
                last_parsed_at=str(v.get("last_parsed_at", ""))
            )

        rev_raw = data.get("reverse_deps")
        if not isinstance(rev_raw, dict):
            raise ValueError("Manifest 'reverse_deps' field must be a dictionary")

        rev_deps: dict[str, list[str]] = {}
        for k, v in rev_raw.items():
            if not isinstance(v, list):
                raise ValueError(f"Reverse dependency list for key '{k}' must be a list")
            rev_deps[str(k)] = [str(x) for x in v]

        return cls(
            schema_version=data.get("schema_version", CACHE_SCHEMA_VERSION),
            project_name=str(data.get("project_name", "tare.tools.project")),
            last_indexed_at=str(data.get("last_indexed_at", "")),
            generation_digest=str(data.get("generation_digest", "")),
            files=files_dict,
            reverse_deps=rev_deps
        )

@dataclass
class StalenessReport:
    """Diagnostic report detailing codebase synchronization and causal blast radius."""
    status: str  # "FRESH" | "STALE"
    is_stale: bool
    checked_files_count: int
    modified_files: list[str] = field(default_factory=list)
    added_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    impacted_nodes: list[str] = field(default_factory=list)  # Transitive causal blast radius
    duration_ms: float = 0.0
    generation_digest: str = ""
    discovery_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "is_stale": self.is_stale,
            "duration_ms": round(self.duration_ms, 2),
            "checked_files_count": self.checked_files_count,
            "modified_files": self.modified_files,
            "added_files": self.added_files,
            "deleted_files": self.deleted_files,
            "impacted_nodes": self.impacted_nodes,
            "generation_digest": self.generation_digest,
            "discovery_errors": self.discovery_errors
        }

class IncrementalCacheEngine:
    """High-performance filesystem indexer, hash cache, and causal reverse dependency tracker."""

    @staticmethod
    def get_cache_path(root_dir: Path | str = ".") -> Path:
        root = Path(root_dir).resolve()
        cache_dir = root / CACHE_DIR_NAME
        return cache_dir / CACHE_FILE_NAME

    @classmethod
    def load_manifest(cls, root_dir: Path | str = ".") -> CacheManifest | None:
        p = cls.get_cache_path(root_dir)
        if not p.exists():
            return None
        try:
            raw = p.read_text(encoding="utf-8", errors="strict")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            if data.get("schema_version") != CACHE_SCHEMA_VERSION:
                return None
            return CacheManifest.from_dict(data)
        except Exception:
            return None

    @classmethod
    def save_manifest(cls, manifest: CacheManifest, root_dir: Path | str = ".") -> Path:
        root = Path(root_dir).resolve()
        cache_path = cls.get_cache_path(root)
        clean_cache_str = str(cache_path.resolve()).replace("\\\\?\\", "").replace("\\", "/")
        clean_root_str = str(root).replace("\\\\?\\", "").replace("\\", "/")
        if not clean_cache_str.startswith(clean_root_str):
            raise ValueError(f"Security Violation: Cache destination '{clean_cache_str}' escapes project root '{clean_root_str}'")

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        data = manifest.to_dict()
        raw_json = json.dumps(data, indent=2, ensure_ascii=False)

        # Local writers queue before starting the bounded inter-process wait.
        # Keep a strong reference through release; idle destinations leave the registry.
        with _manifest_locks_guard:
            local_lock = _manifest_locks.setdefault(cache_path, threading.Lock())
        with local_lock:
            lock_file = cache_path.parent / "sync.lock"
            lock_acquired = False
            my_pid = str(os.getpid())
            for _ in range(50):
                try:
                    fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                except FileExistsError:
                    time.sleep(0.02)
                    continue
                try:
                    try:
                        os.write(fd, my_pid.encode("utf-8"))
                    finally:
                        os.close(fd)
                except BaseException:
                    # O_EXCL created this file; no external holder is being removed.
                    lock_file.unlink()
                    raise
                lock_acquired = True
                break

            if not lock_acquired:
                raise TimeoutError("Could not acquire sync.lock: another process is updating the cache manifest")

            try:
                # Resilient atomic write with UUID + PID, fsync and retry on Windows [WinError 5]
                unique_suffix = f"{os.getpid()}_{uuid.uuid4().hex}"
                temp_path = cache_path.with_name(f"{cache_path.name}.{unique_suffix}.tmp")
                try:
                    with open(temp_path, "w", encoding="utf-8") as f:
                        f.write(raw_json)
                        f.flush()
                        os.fsync(f.fileno())

                    for attempt in range(5):
                        try:
                            os.replace(temp_path, cache_path)
                            break
                        except PermissionError:
                            if attempt == 4:
                                raise
                            time.sleep(0.01 * (2 ** attempt))
                    return cache_path
                finally:
                    if temp_path.exists():
                        try:
                            temp_path.unlink()
                        except Exception:
                            pass
            finally:
                if lock_acquired and lock_file.exists():
                    try:
                        if lock_file.read_text(encoding="utf-8").strip() == my_pid:
                            lock_file.unlink()
                    except Exception:
                        pass

    @classmethod
    def compute_sha256(cls, file_path: Path) -> str:
        """Compute SHA-256 in 64 KiB chunks for high memory efficiency."""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    @classmethod
    def _fast_scan_tree(
        cls,
        root: Path,
        dir_path: Path,
        allowed_exts: list[str] | tuple[str, ...],
        out_dict: dict[str, tuple[Path, str]],
        kind: str,
        disc_errs: list[str],
        excluded_paths: list[str] | None = None
    ) -> None:
        """Scan a directory tree with os.scandir, filtering allowed extensions and evaluating precise relative path exclusions."""
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith("."):
                        continue
                    try:
                        full_p = Path(entry.path)
                        rel_p = str(full_p.relative_to(root)).replace("\\", "/")
                        if excluded_paths and SpecGraphConfig.is_path_excluded(rel_p, excluded_paths):
                            continue

                        if entry.is_dir(follow_symlinks=False):
                            cls._fast_scan_tree(root, full_p, allowed_exts, out_dict, kind, disc_errs, excluded_paths)
                        elif entry.is_file(follow_symlinks=False):
                            if any(rel_p.lower().endswith(ext) for ext in allowed_exts):
                                out_dict[rel_p] = (full_p, kind)
                    except OSError as entry_err:
                        disc_errs.append(f"Scan entry error on '{entry.path}': {entry_err}")
        except OSError as dir_err:
            disc_errs.append(f"Directory scan error on '{dir_path}': {dir_err}")

    @classmethod
    def _discover_all_project_files(cls, config: SpecGraphConfig, root: Path) -> tuple[dict[str, tuple[Path, str]], list[str]]:
        """Fast pruned scan of all configured directories to discover candidate governed files."""
        files: dict[str, tuple[Path, str]] = {}
        disc_errs: list[str] = []

        categories = [
            (config.adr_paths, "adr", [".md", ".markdown", ".mdown"]),
            (config.spec_paths, "spec", [".md", ".markdown", ".mdown"]),
            (config.schema_paths, "schema", [".sql", ".ddl", ".json", ".py"]),
            (config.api_paths, "api", [".py"]),
            (config.governed_paths, "code", [".py"]),
            (config.test_paths, "test", [".py"]),
        ]

        for paths_list, kind, allowed_exts in categories:
            for p_str in paths_list:
                full_p = (root / p_str).resolve()
                try:
                    full_p.relative_to(root)
                except ValueError:
                    disc_errs.append(f"Security Violation: Search path '{p_str}' escapes project root '{root}'")
                    continue
                if not full_p.exists():
                    continue

                rel = str(full_p.relative_to(root)).replace("\\", "/")
                if SpecGraphConfig.is_path_excluded(rel, config.excluded_paths):
                    continue

                if full_p.is_file():
                    if any(rel.lower().endswith(ext) for ext in allowed_exts):
                        files[rel] = (full_p, kind)
                elif full_p.is_dir():
                    cls._fast_scan_tree(root, full_p, allowed_exts, files, kind, disc_errs, config.excluded_paths)

        filtered = {}
        for rel, (fp, k) in files.items():
            if SpecGraphConfig.is_path_excluded(rel, config.excluded_paths):
                continue
            filtered[rel] = (fp, k)

        from .traceability import mapping_files
        declared_files, mapping_errors = mapping_files(root, config)
        filtered.update(declared_files)
        disc_errs.extend(mapping_errors)

        return filtered, disc_errs

    @classmethod
    def _compute_transitive_blast_radius(cls, initial_nodes: set[str], reverse_deps: dict[str, list[str]]) -> list[str]:
        """Compute the complete downstream transitive closure of impacted nodes via Breadth-First Search (BFS)."""
        visited: set[str] = set()
        queue = deque(sorted(list(initial_nodes)))

        while queue:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            for neighbor in reverse_deps.get(node, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        return sorted(list(visited))

    @classmethod
    def detect_staleness(cls, root_dir: Path | str = ".", crypto_verify: bool = True) -> StalenessReport:
        """Detect whether the current codebase state has drifted from the persisted CacheManifest."""
        t0 = time.perf_counter()
        root = Path(root_dir).resolve()
        config = SpecGraphConfig.load(root)

        disk_files, disc_errs = cls._discover_all_project_files(config, root)
        manifest = cls.load_manifest(root)

        if manifest is None:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            return StalenessReport(
                status="STALE",
                is_stale=True,
                checked_files_count=len(disk_files),
                added_files=sorted(list(disk_files.keys())),
                duration_ms=duration_ms,
                generation_digest="",
                discovery_errors=disc_errs
            )

        cached_files = manifest.files
        modified: list[str] = []
        added: list[str] = []
        deleted: list[str] = []
        initial_impacted: set[str] = set()

        # Check deleted files
        for rel_path in cached_files:
            if rel_path not in disk_files:
                deleted.append(rel_path)
                for node_id in cached_files[rel_path].extracted_nodes:
                    initial_impacted.add(node_id)

        # Helper to extract current spec tags & dependencies from a file
        def extract_tags(fp: Path) -> set[str]:
            tags: set[str] = set()
            try:
                txt = fp.read_text(encoding="utf-8", errors="ignore")
                # 1. @spec, $spec, "spec":
                for m in re.finditer(r"(?:@spec|\$spec|\"spec\"|\bspec\b)[\s\":=]+([A-Za-z0-9_\-]+)", txt, re.IGNORECASE):
                    tag = m.group(1).upper()
                    if tag.startswith("SPEC-"):
                        tags.add(tag)
                # 2. @pytest.mark.verifies("SPEC-...") or verifies(...)
                for m in re.finditer(r"verifies\s*\(\s*[\"\']\s*([A-Za-z0-9_\-]+)", txt, re.IGNORECASE):
                    tag = m.group(1).upper()
                    if tag.startswith("SPEC-"):
                        tags.add(tag)
                # 3. YAML frontmatter parsing (including multi-line lists)
                if txt.startswith("---"):
                    parts = txt.split("---", 2)
                    if len(parts) >= 3:
                        try:
                            import yaml
                            data = yaml.safe_load(parts[1])
                            if isinstance(data, dict):
                                sid = data.get("id") or data.get("spec_id")
                                if sid and str(sid).upper().startswith("SPEC-"):
                                    tags.add(str(sid).upper())
                                gov = data.get("governed_specs") or []
                                if isinstance(gov, list):
                                    for g in gov:
                                        if str(g).upper().startswith("SPEC-"):
                                            tags.add(str(g).upper())
                                elif isinstance(gov, str) and gov.upper().startswith("SPEC-"):
                                    tags.add(gov.upper())
                                imp = data.get("implements") or []
                                if isinstance(imp, list):
                                    for i in imp:
                                        if str(i).upper().startswith("SPEC-"):
                                            tags.add(str(i).upper())
                                elif isinstance(imp, str) and imp.upper().startswith("SPEC-"):
                                    tags.add(imp.upper())
                        except Exception:
                            pass

                # 4. AST-based import target extraction for Python files
                if fp.suffix == ".py":
                    try:
                        import ast
                        tree = ast.parse(txt, filename=str(fp))
                        for n in ast.walk(tree):
                            if isinstance(n, ast.Import):
                                for alias in n.names:
                                    mod = alias.name
                                    for c_rel, c_fp in cached_files.items():
                                        if c_rel.endswith(f"{mod}.py") or c_rel == f"{mod}.py":
                                            for ext_n in c_fp.extracted_nodes:
                                                tags.add(ext_n)
                            elif isinstance(n, ast.ImportFrom):
                                mod = n.module or ""
                                for alias in n.names:
                                    sym_name = alias.name
                                    target_suffix = f"{mod}.py:{sym_name}" if mod else f":{sym_name}"
                                    for c_rel, c_fp in cached_files.items():
                                        if (mod and c_rel.endswith(f"{mod}.py")) or c_rel.endswith(f"/{mod}.py"):
                                            for ext_n in c_fp.extracted_nodes:
                                                if ext_n.endswith(f":{sym_name}") or ext_n == sym_name:
                                                    tags.add(ext_n)
                                                tags.add(ext_n)
                    except Exception:
                        pass
            except Exception:
                pass
            return tags

        # Check added and modified files
        for rel_path, (full_path, kind) in disk_files.items():
            if rel_path not in cached_files:
                added.append(rel_path)
                for t in extract_tags(full_path):
                    initial_impacted.add(t)
            else:
                fingerprint = cached_files[rel_path]
                try:
                    stat = full_path.stat()
                except (FileNotFoundError, OSError, PermissionError) as st_err:
                    disc_errs.append(f"Stat failure on '{rel_path}': {st_err}")
                    deleted.append(rel_path)
                    for node_id in fingerprint.extracted_nodes:
                        initial_impacted.add(node_id)
                    continue

                stat_diff = (stat.st_mtime_ns != fingerprint.mtime_ns or stat.st_size != fingerprint.byte_size)
                if crypto_verify or stat_diff:
                    try:
                        current_hash = cls.compute_sha256(full_path)
                    except (FileNotFoundError, OSError, PermissionError) as h_err:
                        disc_errs.append(f"Hash calculation failure on '{rel_path}': {h_err}")
                        modified.append(rel_path)
                        for node_id in fingerprint.extracted_nodes:
                            initial_impacted.add(node_id)
                        continue

                    if current_hash != fingerprint.sha256:
                        modified.append(rel_path)
                        # Union of old nodes and new nodes
                        for node_id in fingerprint.extracted_nodes:
                            initial_impacted.add(node_id)
                        for new_tag in extract_tags(full_path):
                            initial_impacted.add(new_tag)

        # Compute full transitive causal blast radius
        transitive_impacted = cls._compute_transitive_blast_radius(initial_impacted, manifest.reverse_deps)

        is_stale = bool(modified or added or deleted or disc_errs)
        duration_ms = (time.perf_counter() - t0) * 1000.0

        return StalenessReport(
            status="STALE" if is_stale else "FRESH",
            is_stale=is_stale,
            checked_files_count=len(disk_files),
            modified_files=sorted(modified),
            added_files=sorted(added),
            deleted_files=sorted(deleted),
            impacted_nodes=transitive_impacted,
            duration_ms=duration_ms,
            generation_digest=manifest.generation_digest,
            discovery_errors=disc_errs
        )

    @classmethod
    def sync_cache(cls, matrix: Any, root_dir: Path | str = ".") -> CacheManifest:
        """Build and persist a fresh CacheManifest directly from an assembled CausalTraceabilityMatrix."""
        root = Path(root_dir).resolve()
        cfg = matrix.config

        # Refuse to sync cache if drift_check() fails for ANY reason (referential, parsing, duplicates, or inactive ADR governance)
        drift = matrix.drift_check(strict_override=False)
        if drift["status"] != "PASS":
            raise ValueError(f"Cannot sync cache with drift violations: {drift['violations']}")

        disk_files, disc_errs = cls._discover_all_project_files(cfg, root)
        if disc_errs:
            raise ValueError(f"Cannot sync cache with discovery errors: {disc_errs}")

        now_iso = datetime.now(timezone.utc).isoformat()

        # Build directed causal downstream reverse dependency map (dependency -> dependent)
        reverse_deps: dict[str, list[str]] = {}
        for edge in matrix.edges:
            src = edge["from"]
            dst = edge["to"]
            reverse_deps.setdefault(dst, []).append(src)

        # Map file -> extracted node identifiers (including symbols and governed/linked specs)
        file_nodes_map: dict[str, list[str]] = {}
        for a in matrix.adrs.values():
            file_nodes_map.setdefault(a.file_path, []).append(a.id)
            for spec_id in a.governed_specs:
                file_nodes_map.setdefault(a.file_path, []).append(spec_id)
        for s in matrix.specs.values():
            file_nodes_map.setdefault(s.file_path, []).append(s.id)
            for ac in s.acceptance_criteria:
                file_nodes_map.setdefault(s.file_path, []).append(f"{s.id}:{ac}")
        for sch in matrix.schemas.values():
            file_nodes_map.setdefault(sch.file_path, []).append(sch.schema_id)
            for lk in sch.linked_specs:
                file_nodes_map.setdefault(sch.file_path, []).append(lk.split(":")[0])
        for ep in matrix.apis.values():
            file_nodes_map.setdefault(ep.file_path, []).append(ep.endpoint_id)
            for lk in ep.linked_specs:
                file_nodes_map.setdefault(ep.file_path, []).append(lk.split(":")[0])
        for tool in matrix.mcp_tools.values():
            file_nodes_map.setdefault(tool.file_path, []).append(tool.tool_id)
            for lk in tool.linked_specs:
                file_nodes_map.setdefault(tool.file_path, []).append(lk.split(":")[0])
        for sym in matrix.symbols.values():
            file_nodes_map.setdefault(sym.file_path, []).append(sym.symbol_id)
            for lk in sym.linked_specs:
                file_nodes_map.setdefault(sym.file_path, []).append(lk.split(":")[0])
        for t in matrix.tests.values():
            file_nodes_map.setdefault(t.file_path, []).append(t.test_id)
            for lk in t.verified_specs:
                file_nodes_map.setdefault(t.file_path, []).append(lk.split(":")[0])

        manifest_files: dict[str, FileFingerprint] = {}
        for rel_path, (full_path, kind) in disk_files.items():
            stat = full_path.stat()
            h = cls.compute_sha256(full_path)
            nodes = list(dict.fromkeys(file_nodes_map.get(rel_path, [])))
            manifest_files[rel_path] = FileFingerprint(
                rel_path=rel_path,
                mtime_ns=stat.st_mtime_ns,
                byte_size=stat.st_size,
                sha256=h,
                artifact_kind=kind,
                extracted_nodes=sorted(list(set(nodes))),
                last_parsed_at=now_iso
            )

        manifest = CacheManifest(
            schema_version=CACHE_SCHEMA_VERSION,
            project_name=cfg.project_name,
            last_indexed_at=now_iso,
            generation_digest=matrix.canonical_digest(),
            files=manifest_files,
            reverse_deps={k: sorted(list(set(v))) for k, v in reverse_deps.items()}
        )

        cls.save_manifest(manifest, root_dir=root)
        return manifest
