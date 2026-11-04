"""Budgeted advisory context from a published snapshot; no source/model calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .serialization import canonical_json
from .storage import IndexUnavailableError, RepositoryIndex

ROLES = ("planner", "implementer", "auditor", "explorer")


def hook_doctor(root: Path | str | None = None) -> dict[str, Any]:
    """Describe implementation separately from host delivery and measured value.

@spec SPEC-SPECGRAPH-C7
    """
    report = {"service": "IMPLEMENTED", "authority": "NONE", "roles": list(ROLES),
            "source_scans": False, "llm_calls": False, "network_calls": False,
            "host_registration": "NOT_OBSERVED", "host_trigger": "NOT_OBSERVED",
            "host_delivery": "NOT_OBSERVED", "effectiveness": "NOT_MEASURED",
            "evidence": "DECLARED_LINKS_ONLY; no execution attestation",
            "freshness": "PUBLISHED_SNAPSHOT; current worktree not validated"}
    if root is not None:
        try:
            from compute_plane.hook_observations import hook_host_status
            report["host_observations"] = hook_host_status(root, timeout_ms=20)
            report.update(host_registration="SEE_PER_HOST_OBSERVATIONS",
                          host_trigger="SEE_PER_HOST_OBSERVATIONS", host_delivery="SEE_PER_HOST_OBSERVATIONS")
        except (ImportError, OSError, ValueError, sqlite3.DatabaseError):
            report["host_observations"] = {"status": "UNAVAILABLE", "authority": "NONE",
                                            "reason": "Kernel host observer unavailable in this interpreter"}
    return report


def context_hint(root: Path | str, *, role: str, event: str, files: tuple[str, ...] = (),
                 spec_id: str | None = None, work_id: str | None = None,
                 symbol_id: str | None = None,
                 source_ref: str | None = None, query: str | None = None,
                 previous_digest: str | None = None, dirty_paths: tuple[str, ...] = (),
                 max_bytes: int = 4096, timeout_ms: int = 50,
                 state_dir: Path | str | None = None) -> dict[str, Any]:
    """Select exact-file/symbol context without guessing a Work or repository.

    The complete returned JSON fits max_bytes. Mandatory identity and selected
    acceptance criteria are never truncated: callers receive BUDGET_REQUIRED.
    Timeouts bound SQLite execution; OS/filesystem scheduling can exceed wall
    budget, so this is not a hard real-time or workstation latency guarantee.

@spec SPEC-SPECGRAPH-C7
@spec SPEC-SPECGRAPH-HOST-ADOPTION
    """
    if role not in ROLES or event not in {"start", "pre", "post", "stop", "end", "delegate"}:
        raise ValueError("unsupported hook role or event")
    if not 512 <= max_bytes <= 16384 or not 1 <= timeout_ms <= 1000:
        raise ValueError("invalid hook context budget")
    if len(files) > 16 or len(dirty_paths) > 64:
        raise ValueError("hook selection exceeds path budget")
    if symbol_id is not None and (not isinstance(symbol_id, str) or not 1 <= len(symbol_id) <= 4096):
        raise ValueError("symbol_id must be an explicit bounded identity")
    root = Path(root).resolve()
    base = {"schema": "tare.tools/specgraph-hook-context/1", "authority": "NONE",
            "role": role, "event": event, "root": str(root), "work_id": work_id,
            "source_ref": source_ref}

    def bounded(result: dict[str, Any]) -> dict[str, Any]:
        if len((canonical_json(result) + "\n").encode("utf-8")) <= max_bytes:
            return result
        # Even pathological caller-provided identity can exceed the budget.
        # Its digest preserves identity without silently clipping mandatory data.
        return {"schema": base["schema"], "status": "BUDGET_REQUIRED", "authority": "NONE",
                "selection_digest": hashlib.sha256(canonical_json(result).encode()).hexdigest(),
                "reason": "mandatory context cannot fit; narrow selection or increase budget"}

    if event in {"post", "stop", "end"}:
        return bounded(base | {"status": "SILENT", "reason": "notification_only; no stop gate"})
    selected_paths = []
    for value in files:
        path = Path(value)
        if not path.is_absolute():
            path = root / path
        try:
            selected_paths.append(path.resolve().relative_to(root).as_posix())
        except ValueError:
            return bounded(base | {"status": "OUTSIDE_SCOPE", "reason": "explicit path is outside selected root"})
    selected_paths = sorted(set(selected_paths))
    started = time.monotonic()
    deadline = started + timeout_ms / 1000
    try:
        with RepositoryIndex(root, state_dir=state_dir, timeout_s=timeout_ms / 1000) as index:
            connection = index.connection
            connection.execute(f"PRAGMA busy_timeout={timeout_ms}")
            connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
            metadata = index.metadata()
            from .lifecycle import lifecycle_status
            lifecycle = lifecycle_status(root, state_dir=state_dir,
                                         timeout_ms=max(0, min(20, int((deadline - time.monotonic()) * 1000))))
            pending = set(lifecycle.get("pending_paths", ())) | set(dirty_paths)
            dirty = bool(pending or lifecycle.get("overflow")
                         or lifecycle.get("event_seq", 0) > lifecycle.get("reconciled_seq", 0))
            mandatory: dict[str, Any] = base | {
                "status": "HIT", "snapshot": metadata["content_hash"],
                "coverage": {"status": metadata["coverage"]["status"],
                             "scope": metadata["coverage"].get("indexed_paths", []),
                             "current_worktree": "DIRTY_OBSERVED" if dirty else "NOT_CHECKED"},
                "files": selected_paths,
            }
            if lifecycle["status"] != "NOT_INITIALIZED":
                # Observation age and worker heartbeats belong in doctor; they
                # must not invalidate semantic deduplication on every pre-hook.
                mandatory["reconciliation"] = {key: lifecycle.get(key) for key in (
                    "status", "journal_id", "event_seq", "reconciled_seq", "pending_count",
                    "overflow", "last_reconciled_snapshot")}
                mandatory["reconciliation"]["matches_selected_snapshot"] = (
                    lifecycle.get("last_reconciled_snapshot") == metadata["content_hash"])
            if pending:
                relevant_pending = sorted(pending.intersection(selected_paths)) if selected_paths else sorted(pending)
                mandatory["dirty_paths"] = relevant_pending[:8]
                mandatory["dirty_paths_truncated"] = len(relevant_pending) > 8 or lifecycle.get("pending_paths_truncated", False)
            symbols = []
            documents = []
            if symbol_id:
                row = connection.execute("SELECT payload FROM symbols WHERE symbol_id=?", (symbol_id,)).fetchone()
                if not row:
                    return bounded(mandatory | {"status": "MISS", "reason": "explicit symbol is not indexed"})
                symbol = json.loads(row[0])
                if selected_paths and symbol["file_path"] not in selected_paths:
                    return bounded(mandatory | {"status": "OUTSIDE_SCOPE", "reason": "symbol does not belong to selected files"})
                symbols = [symbol]
                mandatory["symbol_id"] = symbol_id
            elif selected_paths:
                parameters = ",".join("?" for _ in selected_paths)
                rows = connection.execute(
                    f"SELECT payload FROM symbols WHERE file_path IN ({parameters}) "
                    "ORDER BY file_path,start_line,symbol_id LIMIT 17", selected_paths).fetchall()
                symbols = [json.loads(row[0]) for row in rows[:16]]
                mandatory["selection_truncated"] = len(rows) > 16
            elif query and not spec_id:
                from .retrieval import retrieve
                hits, omitted = retrieve(index, query, limit=8, max_nodes=32, edge_budget=32)
                symbols = [hit for hit in hits if "symbol_id" in hit]
                documents = [hit for hit in hits if "document_id" in hit]
                if omitted:
                    mandatory["retrieval_omitted"] = omitted
            elif not spec_id:
                # No implicit project exploration or Work selection at startup.
                mandatory["status"] = "AVAILABLE"

            if spec_id:
                row = connection.execute("SELECT payload FROM documents WHERE document_id=? AND kind='spec'",
                                         ("spec:" + spec_id,)).fetchone()
                if not row:
                    return bounded(mandatory | {"status": "MISS", "reason": "explicit specification is not indexed"})
                spec = json.loads(row[0])["node"]
                mandatory["intent"] = {"id": spec["id"], "title": spec["title"],
                                       "acceptance_criteria": spec["acceptance_criteria"],
                                       "source": spec["file_path"]}
            if (selected_paths or query or symbol_id) and not symbols and not documents and not spec_id:
                mandatory.update(status="MISS", reason="no indexed matches; bounded direct recovery allowed")

            optional: list[tuple[str, Any]] = []
            declared_specs: set[str] = set()
            for symbol in symbols:
                item: dict[str, Any] = {"id": symbol["symbol_id"], "kind": symbol["kind"],
                                       "range": [symbol["start_line"], symbol["end_line"]]}
                if query and "reason" in symbol:
                    item["retrieval_reason"] = symbol["reason"]
                    if "match" in symbol:
                        item["match"] = symbol["match"]
                    if "via_edges" in symbol:
                        item["intent_path"] = {"source": symbol["via_source"],
                                               "document": symbol["via_document"],
                                               "edges": symbol["via_edges"]}
                # Consult validated graph edges, including explicit mappings;
                # raw annotations alone can contain dangling references.
                spec_rows = connection.execute(
                    "SELECT DISTINCT target FROM edges WHERE source=? AND relation='CODE_IMPLEMENTS_SPEC' ORDER BY target LIMIT 5",
                    (symbol["symbol_id"],)).fetchall()
                links = [row[0] for row in spec_rows[:4]]
                declared_specs.update(links)
                if role in {"planner", "auditor", "implementer"}:
                    item["declared_specs"] = links
                    if len(spec_rows) > 4:
                        item["spec_selection_truncated"] = True
                if role == "auditor":
                    item["unresolved_references"] = sum(r.get("status") != "resolved"
                                                         for r in symbol.get("references", []))
                    item["evidence"] = "NOT_ATTESTED"
                if role in {"implementer", "explorer"}:
                    rows = connection.execute(
                        "SELECT DISTINCT source,target,relation FROM edges WHERE (source=? OR target=?) "
                        "AND relation IN ('DEPENDS_ON_STATIC','CONTAINS') ORDER BY source,relation,target LIMIT 5",
                        (symbol["symbol_id"], symbol["symbol_id"])).fetchall()
                    item["neighbors"] = [{"id": target if source == symbol["symbol_id"] else source,
                                          "relation": relation, "direction": "out" if source == symbol["symbol_id"] else "in"}
                                         for source, target, relation in rows[:4]]
                    item["neighbors_truncated"] = len(rows) > 4
                optional.append(("symbols", item))
            for document in documents:
                optional.append(("documents", {"id": document["document_id"],
                    "source": document["file_path"], "title": document["title"],
                    "retrieval_reason": document.get("reason", "legacy_document_match"),
                    "match": document.get("match", {"identity": False})}))
            if role in {"planner", "auditor", "implementer"}:
                for linked_spec in sorted(declared_specs)[:4]:
                    if linked_spec != spec_id and role == "planner":
                        row = connection.execute("SELECT payload FROM documents WHERE document_id=? AND kind='spec'", ("spec:" + linked_spec,)).fetchone()
                        if row:
                            node = json.loads(row[0])["node"]
                            optional.append(("declared_intents", {"id": node["id"], "source": node["file_path"],
                                "acceptance_criteria": node["acceptance_criteria"], "origin": "DECLARED_BINDING_NOT_WORK_SELECTION"}))
                    if role in {"implementer", "auditor"}:
                        rows = connection.execute("SELECT DISTINCT source FROM edges WHERE target=? AND relation='TEST_VERIFIES_SPEC' ORDER BY source LIMIT 5", (linked_spec,)).fetchall()
                        optional.append(("test_references", {"spec": linked_spec, "ids": [row[0] for row in rows[:4]],
                            "truncated": len(rows) > 4, "evidence": "DECLARATION_ONLY_NOT_EXECUTED_COVERAGE"}))
            if role == "implementer":
                for symbol in symbols[:4]:
                    if symbol["end_line"] - symbol["start_line"] >= 40:
                        continue
                    row = connection.execute("SELECT body,sha256 FROM sources WHERE file_path=? AND length(CAST(body AS BLOB))<=65536", (symbol["file_path"],)).fetchone()
                    if row:
                        lines = row[0].splitlines(keepends=True)
                        if 1 <= symbol["start_line"] <= symbol["end_line"] <= len(lines):
                            text = "".join(lines[symbol["start_line"] - 1:symbol["end_line"]])
                            if len(text.encode()) <= 2048:
                                optional.append(("snippets", {"id": symbol["symbol_id"], "sha256": row[1], "text": text, "complete_symbol": True}))
            report = dict(mandatory)
            report["omitted_items"] = 0
            for key, item in optional:
                candidate = report | {key: report.get(key, []) + [item]}
                # Reserve enough space for digest and the serialized wrapper.
                if len(canonical_json(candidate).encode("utf-8")) + 128 <= max_bytes:
                    report = candidate
                else:
                    report["omitted_items"] += 1
            if time.monotonic() >= deadline:
                return bounded(base | {"status": "TIMEOUT", "reason": "context budget exhausted; recovery allowed"})
            semantic = dict(report)
            if "reconciliation" in semantic:
                semantic["reconciliation"] = dict(semantic["reconciliation"])
                # Unrelated journal churn changes doctor counters, not the
                # information needed for this explicitly selected file.
                if selected_paths and not pending.intersection(selected_paths) and not lifecycle.get("overflow"):
                    for key in ("event_seq", "reconciled_seq", "pending_count"):
                        semantic["reconciliation"].pop(key, None)
            digest = hashlib.sha256(canonical_json(semantic).encode("utf-8")).hexdigest()
            if digest == previous_digest:
                return bounded(base | {"status": "SILENT", "digest": digest, "reason": "unchanged context"})
            report["digest"] = digest
            return bounded(report)
    except IndexUnavailableError:
        return bounded(base | {"status": "UNAVAILABLE", "reason": "no compatible published snapshot; recovery allowed"})
    except sqlite3.OperationalError as exc:
        status = "TIMEOUT" if "interrupt" in str(exc).lower() or time.monotonic() >= deadline else "UNAVAILABLE"
        return bounded(base | {"status": status, "reason": "snapshot query unavailable; recovery allowed"})
    except (sqlite3.DatabaseError, OSError):
        return bounded(base | {"status": "UNAVAILABLE", "reason": "snapshot unavailable; recovery allowed"})
