"""Bounded SpecGraph-first lookup with validated traditional-search recovery."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .cache import IncrementalCacheEngine
from .config import SpecGraphConfig

RECOVERY_INDEX = "search-recovery-index.json"
TELEMETRY_LOG = "search-events.jsonl"
MAX_INDEX_ENTRIES = 256
MAX_TELEMETRY_BYTES = 1_048_576
MAX_FILE_BYTES = 2_097_152
MAX_SCANNED_FILES = 5_000
MAX_RECOVERY_BYTES = 16_777_216


@dataclass(frozen=True)
class TraditionalMatch:
    result_type: str
    file_path: str
    line: int
    column: int
    excerpt: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResolveReport:
    query: str
    status: str
    source: str
    primary_status: str
    signals: tuple[str, ...]
    recovery_action: str
    count: int
    results: tuple[dict[str, Any], ...]
    duration_ms: float
    snapshot: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["signals"] = list(self.signals)
        data["results"] = list(self.results)
        data["duration_ms"] = round(self.duration_ms, 2)
        return data


StructuralProbe = Callable[[Path, str, int, float], tuple[str, list[dict[str, Any]]]]
FallbackProbe = Callable[[Path, str, int, float], tuple[str, list[dict[str, Any]]]]


def _generation(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {key: snapshot[key] for key in ("repository_root", "revision_seq", "content_hash")}


def _published_lookup(root: Path, query: str, limit: int, deadline: float
                      ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    """One source-free read transaction; progress and lock waits are bounded."""
    from .storage import IndexUnavailableError, RepositoryIndex
    import sqlite3
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, [], "TIMEOUT"
    try:
        with RepositoryIndex(root, timeout_s=min(0.05, remaining)) as index:
            index.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            metadata = index.metadata()
            from .retrieval import retrieve
            from .query import _unit
            indexed, omissions = retrieve(index, query, limit=limit)
            results = []
            for item in indexed:
                if "symbol_id" in item:
                    result = _unit(index, item, item["reason"]) | {"score": item["score"], "result_type": "symbol"}
                else:
                    result = {key: item[key] for key in ("document_id", "kind", "file_path", "title", "score")}
                    result["result_type"] = "document"
                for key in ("match", "via_document", "via_edges", "via_source"):
                    if key in item:
                        result[key] = item[key]
                results.append(result)
            for result in results:
                fingerprint = index.connection.execute("SELECT sha256 FROM files WHERE file_path=?", (result["file_path"],)).fetchone()
                if fingerprint:
                    result["evidence_sha256"] = fingerprint[0]
            snapshot = {key: metadata[key] for key in (
                "repository_root", "revision_seq", "content_hash", "indexed_at", "freshness")}
            snapshot["coverage"] = {key: metadata["coverage"].get(key) for key in (
                "status", "file_count", "symbol_count", "languages")}
            if omissions:
                snapshot["retrieval_omitted"] = omissions
            return snapshot, results, "HIT" if results else "MISS"
    except (IndexUnavailableError, sqlite3.DatabaseError, OSError, ValueError, KeyError):
        return None, [], "TIMEOUT" if time.monotonic() >= deadline else "UNAVAILABLE"


def _snapshot_report(query: str, results: list[dict[str, Any]], snapshot: dict[str, Any], started: float) -> ResolveReport:
    return ResolveReport(query, "HIT", "specgraph", "HIT", ("PUBLISHED_SNAPSHOT_NOT_LIVE_VERIFIED",),
                         "READ_PUBLISHED_SNAPSHOT", len(results), tuple(results),
                         (time.monotonic() - started) * 1000, dict(snapshot, results_from_snapshot=True))


def _scope_digest(root: Path) -> str:
    """Bind hints to configuration bytes, without rescanning configured trees."""
    identity: dict[str, Any] = {"root": os.path.normcase(str(root)), "policy": "recovery-v2-literal-case-sensitive"}
    for name in ("specgraph.yaml", ".specgraph.yaml"):
        try:
            identity[name] = _file_digest(root / name)
        except FileNotFoundError:
            identity[name] = None
        except OSError:
            identity[name] = "UNREADABLE"
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _recovery_key(query: str, limit: int) -> str:
    return hashlib.sha256(json.dumps([query, limit], ensure_ascii=False).encode("utf-8")).hexdigest()


def resolve_repository(
    root_dir: Path | str,
    query: str,
    *,
    limit: int = 20,
    timeout_ms: int = 2_500,
    structural_probe: StructuralProbe | None = None,
    fallback_probe: FallbackProbe | None = None,
) -> ResolveReport:
    """Resolve a query structurally, falling back and learning only when needed."""

    started = time.monotonic()
    root = Path(root_dir).resolve()
    normalized = _validate(query, limit, timeout_ms)
    deadline = started + (timeout_ms / 1_000)
    signals: list[str] = []
    stale = False

    # Every call, including adapters with injected probes, checks the published
    # generation first. A recovery hint is never an authority over this index.
    snapshot, indexed, index_status = _published_lookup(root, normalized, limit, deadline)
    if indexed:
        report = _snapshot_report(query, indexed, snapshot, started)
        _emit_telemetry(root, normalized, report)
        return report
    if snapshot is not None:
        signals.append("PUBLISHED_SNAPSHOT_NOT_LIVE_VERIFIED")
    elif structural_probe is not None:
        # Retain the legacy adapter diagnostic contract. The production path
        # does not hash the whole repository merely to recover an index miss.
        try:
            stale = IncrementalCacheEngine.detect_staleness(root_dir=root).is_stale
            if stale:
                signals.append("STALE")
        except Exception:
            signals.append("UNAVAILABLE")

    scope = _scope_digest(root)
    learned, evicted = _recall(root, normalized, limit, snapshot=snapshot, scope=scope, deadline=deadline)
    if evicted:
        signals.append("STALE_LEARNED_EVIDENCE")
    if learned:
        # Publication can complete while source hashes are being validated.
        # One bounded reread gives it precedence, with its own coherent snapshot.
        current, better, _ = _published_lookup(root, normalized, limit, deadline)
        if better:
            report = _snapshot_report(query, better, current, started)
            _emit_telemetry(root, normalized, report)
            return report
        if _generation(current) == _generation(snapshot) and _scope_digest(root) == scope:
            report = _report(
                query=query, source="learned", primary_status="STALE" if stale else "HIT",
                signals=signals, action="REUSED_VALIDATED", results=learned,
                started=started, snapshot=snapshot,
            )
            _emit_telemetry(root, normalized, report)
            return report
        evicted = True
        signals.append("RECOVERY_GENERATION_CHANGED")
        snapshot = current
        scope = _scope_digest(root)

    probe = structural_probe
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        structural_status, structural_results = "TIMEOUT", []
    elif probe is None:
        structural_status, structural_results = index_status, []
    else:
        primary_budget = min(1.0, max(0.05, remaining * 0.4))
        try:
            structural_status, structural_results = probe(root, normalized, limit, primary_budget)
        except TimeoutError:
            structural_status, structural_results = "TIMEOUT", []
        except Exception:
            structural_status, structural_results = "UNAVAILABLE", []

    if structural_status not in {"HIT", "MISS", "TIMEOUT", "UNAVAILABLE"}:
        structural_status, structural_results = "UNAVAILABLE", []
    elif structural_status == "HIT" and not structural_results:
        structural_status = "UNAVAILABLE"
    elif structural_results:
        structural_status = "HIT"
    if structural_status != "HIT":
        signals.append(structural_status)

    if structural_results:
        results = structural_results[:limit]
        current, better, _ = _published_lookup(root, normalized, limit, deadline)
        if better:
            report = _snapshot_report(query, better, current, started)
            _emit_telemetry(root, normalized, report)
            return report
        snapshot = current
        action = "INDEXED_CURRENT_EVIDENCE" if stale or evicted else "NONE"
        if action != "NONE":
            learned_current = _learn(root, normalized, results, limit=limit, snapshot=snapshot,
                                     scope=_scope_digest(root), deadline=deadline)
            if not learned_current:
                action = "RETURNED_UNCACHED_EVIDENCE"
        report = _report(
            query=query,
            source="specgraph",
            primary_status="STALE" if stale else "HIT",
            signals=signals,
            action=action,
            results=results,
            started=started,
            snapshot=snapshot,
        )
        _emit_telemetry(root, normalized, report)
        return report

    remaining = deadline - time.monotonic()
    recovery = fallback_probe or _traditional_literal_scan
    if remaining <= 0:
        fallback_status, fallback_results = "TIMEOUT", []
    else:
        try:
            fallback_status, fallback_results = recovery(root, normalized, limit, remaining)
        except TimeoutError:
            fallback_status, fallback_results = "TIMEOUT", []
        except Exception:
            fallback_status, fallback_results = "UNAVAILABLE", []

    if fallback_status not in {"HIT", "MISS", "TIMEOUT", "UNAVAILABLE"}:
        fallback_status, fallback_results = "UNAVAILABLE", []
    elif fallback_status == "HIT" and not fallback_results:
        fallback_status = "UNAVAILABLE"
    elif fallback_results:
        fallback_status = "HIT"
    if fallback_status != "HIT":
        signals.append(f"FALLBACK_{fallback_status}")

    # Never label a probe's old coordinates with a hash of different bytes.
    # Literal evidence is checked against the same bytes that are hashed.
    results = _validate_results(root, normalized, fallback_results[:limit], deadline)
    if len(results) != len(fallback_results[:limit]):
        signals.append("UNVERIFIABLE_FALLBACK_EVIDENCE")
    current, better, _ = _published_lookup(root, normalized, limit, deadline)
    if better:
        report = _snapshot_report(query, better, current, started)
        _emit_telemetry(root, normalized, report)
        return report
    snapshot = current
    action = "NONE"
    if results:
        persisted = _learn(root, normalized, results, limit=limit, snapshot=snapshot,
                           scope=_scope_digest(root), deadline=deadline)
        action = "INDEXED_FALLBACK_EVIDENCE" if persisted else "RETURNED_UNCACHED_EVIDENCE"
    elif evicted:
        action = "EVICTED_STALE"

    primary_status = "STALE" if stale else structural_status
    report = _report(
        query=query,
        source="traditional" if results else "none",
        primary_status=primary_status,
        signals=signals,
        action=action,
        results=results,
        started=started,
        snapshot=snapshot,
    )
    _emit_telemetry(root, normalized, report)
    return report


def runtime_directory(root_dir: Path | str) -> Path:
    """Return external per-repository state without dirtying the worktree."""

    root = Path(root_dir).resolve()
    identity = os.path.normcase(str(root)).encode("utf-8")
    repository_key = hashlib.sha256(identity).hexdigest()[:24]
    override = os.environ.get("TARE_SPECGRAPH_STATE_DIR")
    if override:
        return (Path(override).expanduser() / "recovery" / repository_key).resolve()

    if sys.platform == "win32":
        base_raw = os.environ.get("LOCALAPPDATA")
        base = Path(base_raw) if base_raw else Path.home() / "AppData" / "Local"
    else:
        base_raw = os.environ.get("XDG_STATE_HOME")
        base = Path(base_raw) if base_raw else Path.home() / ".local" / "state"
    return (base / "tare.tools" / "specgraph" / repository_key).resolve()


def telemetry_summary(root_dir: Path | str) -> dict[str, Any]:
    """Aggregate bounded recovery telemetry without queries or source text."""

    root = Path(root_dir).resolve()
    path = _runtime_path(root, TELEMETRY_LOG)
    events: list[dict[str, Any]] = []
    for candidate in (path.with_suffix(path.suffix + ".1"), path):
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict) and event.get("version") == 1:
                    events.append(event)
        except OSError:
            continue

    def counts(field: str) -> dict[str, int]:
        values = Counter(str(event.get(field, "UNKNOWN")) for event in events)
        return dict(sorted(values.items()))

    signal_counts: Counter[str] = Counter()
    durations: list[float] = []
    for event in events:
        raw_signals = event.get("signals", [])
        if isinstance(raw_signals, list):
            signal_counts.update(str(signal) for signal in raw_signals)
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)) and duration >= 0:
            durations.append(float(duration))

    total = len(events)
    source_counts = counts("source")
    primary_counts = counts("primary_status")

    def rate(numerator: int) -> float:
        return round((numerator / total) * 100, 2) if total else 0.0

    return {
        "version": 1,
        "event_count": total,
        "status_counts": counts("status"),
        "primary_status_counts": primary_counts,
        "source_counts": source_counts,
        "signal_counts": dict(sorted(signal_counts.items())),
        "recovery_action_counts": counts("recovery_action"),
        "rates_pct": {
            "traditional_recovery": rate(source_counts.get("traditional", 0)),
            "validated_reuse": rate(source_counts.get("learned", 0)),
            "stale": rate(primary_counts.get("STALE", 0)),
            "timeout": rate(primary_counts.get("TIMEOUT", 0)),
            "unavailable": rate(primary_counts.get("UNAVAILABLE", 0)),
        },
        "latency_ms": {
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "max": round(max(durations), 2) if durations else 0.0,
        },
        "state_directory": str(runtime_directory(root)),
    }


def _validate(query: str, limit: int, timeout_ms: int) -> str:
    normalized = query.strip()
    if not normalized:
        raise ValueError("query must not be empty")
    if len(normalized.encode("utf-8")) > 512:
        raise ValueError("query exceeds 512 UTF-8 bytes")
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if not 100 <= timeout_ms <= 30_000:
        raise ValueError("timeout_ms must be between 100 and 30000")
    return normalized


def _structural_subprocess(
    root: Path, query: str, limit: int, timeout_seconds: float
) -> tuple[str, list[dict[str, Any]]]:
    import subprocess
    source_root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ)
    old_path = env.get("PYTHONPATH")
    env["PYTHONPATH"] = source_root if not old_path else os.pathsep.join((source_root, old_path))
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "specgraph.cli",
                "--root",
                str(root),
                "search",
                query,
                "--limit",
                str(limit),
                "--format",
                "json",
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("structural search timed out") from exc
    if completed.returncode != 0:
        return "UNAVAILABLE", []
    try:
        payload = json.loads(completed.stdout)
        results = payload.get("results")
        if not isinstance(results, list):
            return "UNAVAILABLE", []
        safe = [dict(item) for item in results if isinstance(item, dict)]
        return ("HIT" if safe else "MISS"), safe[:limit]
    except (json.JSONDecodeError, AttributeError):
        return "UNAVAILABLE", []


def _traditional_literal_scan(
    root: Path, query: str, limit: int, timeout_seconds: float
) -> tuple[str, list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    config = SpecGraphConfig.load(root)
    search_roots = sorted(
        set(
            config.governed_paths
            + (config.indexed_paths or [])
            + config.search_paths
            + config.spec_paths
            + config.test_paths
            + config.schema_paths
            + config.api_paths
            + config.adr_paths
        )
    )
    matches: list[dict[str, Any]] = []
    scanned = 0
    seen_paths: set[Path] = set()

    for configured in search_roots:
        base = (root / configured).resolve()
        if not base.exists() or root not in (base, *base.parents):
            continue
        paths = [base] if base.is_file() else base.rglob("*")
        for path in paths:
            if time.monotonic() >= deadline:
                raise TimeoutError("traditional search timed out")
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if SpecGraphConfig.is_path_excluded(rel, config.excluded_paths):
                continue
            resolved = path.resolve()
            if root not in resolved.parents or resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            scanned += 1
            if scanned > MAX_SCANNED_FILES:
                return "UNAVAILABLE", []
            try:
                if resolved.stat().st_size > MAX_FILE_BYTES:
                    continue
                with resolved.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    continue
                if b"\x00" in raw:
                    continue
                text = raw.decode("utf-8", errors="replace")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                column = line.find(query)
                if column < 0:
                    continue
                matches.append(
                    TraditionalMatch(
                        result_type="literal_text",
                        file_path=rel,
                        line=line_number,
                        column=column + 1,
                        excerpt=line[:500],
                    ).to_dict()
                )
                if len(matches) >= limit:
                    return "HIT", matches
    return ("HIT" if matches else "MISS"), matches


def _runtime_path(root: Path, name: str) -> Path:
    return runtime_directory(root) / name


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * quantile + 0.5)))
    return round(ordered[index], 2)


def _query_key(query: str) -> str:
    return hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65_536):
            digest.update(chunk)
    return digest.hexdigest()


def _result_path(result: dict[str, Any]) -> str | None:
    value = result.get("file_path")
    return value if isinstance(value, str) and value else None


def _load_index(root: Path) -> dict[str, Any]:
    path = _runtime_path(root, RECOVERY_INDEX)
    identity = os.path.normcase(str(root))
    try:
        if path.stat().st_size <= MAX_RECOVERY_BYTES:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if (payload.get("version") == 2 and payload.get("repository_root") == identity
                    and isinstance(payload.get("entries"), dict)):
                payload["entries"] = {key: value for key, value in payload["entries"].items()
                                      if isinstance(key, str) and isinstance(value, dict)}
                return payload
    except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
        pass
    # Legacy entries do not prove which repository/configuration produced them.
    return {"version": 2, "repository_root": identity, "entries": {}}


def _validate_results(root: Path, query: str, results: list[dict[str, Any]], deadline: float) -> list[dict[str, Any]]:
    config = SpecGraphConfig.load(root)
    if config.config_errors:
        return []
    scopes = []
    for configured in (config.governed_paths + (config.indexed_paths or []) + config.search_paths
                       + config.spec_paths + config.test_paths + config.schema_paths + config.api_paths + config.adr_paths):
        try:
            scopes.append((root / configured).resolve().relative_to(root).as_posix())
        except ValueError:
            continue
    valid = []
    observed: dict[str, tuple[str, list[str]]] = {}
    for result in results:
        if time.monotonic() >= deadline or not isinstance(result, dict):
            break
        rel = _result_path(result)
        if rel is None or Path(rel).is_absolute() or any(part in (".", "..") for part in Path(rel).parts):
            continue
        try:
            path = (root / rel).resolve()
        except (OSError, ValueError, UnicodeError):
            continue
        if root not in path.parents or config.is_path_excluded(rel, config.excluded_paths):
            continue
        if not any(scope == "." or rel == scope or rel.startswith(scope + "/") for scope in scopes):
            continue
        if rel not in observed:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
                with path.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES:
                    continue
                observed[rel] = (hashlib.sha256(raw).hexdigest(), raw.decode("utf-8").splitlines())
            except (OSError, UnicodeError):
                continue
        digest, lines = observed[rel]
        if result.get("evidence_sha256", digest) != digest:
            continue
        kind = result.get("result_type", "symbol" if "start_line" in result else "unknown")
        if kind == "literal_text":
            line, column, excerpt = result.get("line"), result.get("column"), result.get("excerpt")
            if type(line) is not int or type(column) is not int or not 1 <= line <= len(lines) or column < 1 or not isinstance(excerpt, str):
                continue
            text = lines[line - 1]
            if text[column - 1:column - 1 + len(query)] != query or not text.startswith(excerpt):
                continue
        elif kind == "symbol":
            # Adapter coordinates can only be learned when the adapter also
            # identifies the source bytes from which it extracted them.
            if "evidence_sha256" not in result:
                continue
            start, end = result.get("start_line"), result.get("end_line", result.get("start_line"))
            if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
                continue
        else:
            continue
        valid.append(dict(result, result_type=kind, evidence_sha256=digest))
    return valid


def _recall(root: Path, query: str, limit: int, *, snapshot: dict[str, Any] | None,
            scope: str, deadline: float) -> tuple[list[dict[str, Any]], bool]:
    index = _load_index(root)
    key = _recovery_key(query, limit)
    entry = index["entries"].get(key)
    if not isinstance(entry, dict):
        return [], False
    evidence = entry.get("evidence")
    binding_valid = (entry.get("query") == query and entry.get("limit") == limit
                     and entry.get("scope_digest") == scope and entry.get("generation") == _generation(snapshot)
                     and isinstance(evidence, list) and 0 < len(evidence) <= limit)
    results = []
    if binding_valid:
        for item in evidence:
            if not isinstance(item, dict) or not isinstance(item.get("result"), dict):
                break
            if item.get("origin") not in {"literal_text", "symbol"} or item["result"].get("result_type") != item["origin"]:
                break
            digest = item.get("file_sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                break
            results.append(dict(item["result"], evidence_sha256=digest))
    valid = _validate_results(root, query, results, deadline) if binding_valid else []
    if binding_valid and len(valid) == len(evidence) and _scope_digest(root) == scope:
        return valid, False
    # Drop the whole hint: a valid prefix cannot hide invalidated/later results.
    index["entries"].pop(key, None)
    _save_index(root, index)
    return [], True


def _learn(root: Path, query: str, results: list[dict[str, Any]], *, limit: int,
           snapshot: dict[str, Any] | None, scope: str, deadline: float) -> bool:
    validated = _validate_results(root, query, results, deadline)
    if not validated or len(validated) != len(results) or _scope_digest(root) != scope:
        return False
    evidence = [{"file_sha256": result["evidence_sha256"], "origin": result["result_type"],
                 "result": result} for result in validated]
    index = _load_index(root)
    index["entries"][_recovery_key(query, limit)] = {
        "query": query, "limit": limit, "scope_digest": scope, "generation": _generation(snapshot),
        "learned_at": datetime.now(timezone.utc).isoformat(), "evidence": evidence,
    }
    if len(index["entries"]) > MAX_INDEX_ENTRIES:
        ordered = sorted(index["entries"].items(), key=lambda item: str(item[1].get("learned_at", "")), reverse=True)
        index["entries"] = dict(ordered[:MAX_INDEX_ENTRIES])
    return _save_index(root, index)


def _save_index(root: Path, index: dict[str, Any]) -> bool:
    path = _runtime_path(root, RECOVERY_INDEX)
    temp = path.with_name(f"{path.name}.{os.getpid()}-{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_RECOVERY_BYTES:
            return False
        temp.write_text(encoded, encoding="utf-8")
        os.replace(temp, path)
        return True
    except (OSError, TypeError, ValueError, UnicodeError):
        return False  # Hints are optional and never authorize or block retrieval.
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _report(
    *,
    query: str,
    source: str,
    primary_status: str,
    signals: list[str],
    action: str,
    results: list[dict[str, Any]],
    started: float,
    snapshot: dict[str, Any] | None = None,
) -> ResolveReport:
    return ResolveReport(
        query=query,
        status="HIT" if results else "MISS",
        source=source,
        primary_status=primary_status,
        signals=tuple(dict.fromkeys(signals)),
        recovery_action=action,
        count=len(results),
        results=tuple(results),
        duration_ms=(time.monotonic() - started) * 1_000,
        snapshot=dict(snapshot, results_from_snapshot=False) if snapshot is not None else None,
    )


def _emit_telemetry(root: Path, query: str, report: ResolveReport) -> None:
    path = _runtime_path(root, TELEMETRY_LOG)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= MAX_TELEMETRY_BYTES:
            rotated = path.with_suffix(path.suffix + ".1")
            rotated.unlink(missing_ok=True)
            os.replace(path, rotated)
        event = {
            "version": 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "query_sha256": _query_key(query),
            "query_bytes": len(query.encode("utf-8")),
            "status": report.status,
            "source": report.source,
            "primary_status": report.primary_status,
            "signals": list(report.signals),
            "recovery_action": report.recovery_action,
            "count": report.count,
            "duration_ms": round(report.duration_ms, 2),
        }
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")))
            stream.write("\n")
    except OSError:
        # Observability is deliberately non-authoritative and never blocks retrieval.
        return
