"""Small capability and installation diagnostics, without scanning a project."""
from __future__ import annotations

from importlib.util import find_spec
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any

from .config import SpecGraphConfig
from .storage import IndexUnavailableError, RepositoryIndex, index_path


def capability_catalog() -> dict[str, Any]:
    """Describe implementation boundaries; presence is not release qualification.

    @spec SPEC-SPECGRAPH-C0 [AC-01]
    """
    capabilities = [
        ("python_structure", "PARTIAL", "specgraph.code_parser", "C1", "Native AST; dynamic dispatch and re-exports can remain unresolved."),
        ("persistent_snapshot", "IMPLEMENTED", "specgraph.storage", "C2", "SQLite read model; publication does not prove a current immutable worktree."),
        ("incremental_extraction", "IMPLEMENTED", "specgraph.indexer", "C3", "Content/config/parser-bound extraction cache; SHA-verified unchanged polling skips parsing and relation assembly."),
        ("periodic_reconciliation", "IMPLEMENTED", "specgraph.lifecycle", "C3", "Explicit foreground worker and bounded event journal; events are best effort, source observation is not an immutable Git snapshot."),
        ("literal_search", "IMPLEMENTED", "specgraph.search", "C4", "Indexed snapshot preferred; live compatibility fallback is explicitly labeled."),
        ("code_lexical_search", "IMPLEMENTED", "specgraph.retrieval", "C4", "Persistent normalized FTS5; bounded candidates, PT/EN stopwords, no translation or semantic completeness. Explicit reindex required for older projections."),
        ("project_queries", "PARTIAL", "specgraph.query", "C4", "Bounded supported relations; no universal absence guarantee."),
        ("bounded_spec_context", "IMPLEMENTED", "specgraph.context", "C4", "Complete selected criteria/snippets or explicit byte-budget refusal."),
        ("federated_grounding", "PARTIAL", "specgraph.federation", "C5", "Explicit owner roots and committed bodies; no automatic publishing or rebinding."),
        ("execution_evidence", "PARTIAL", "specgraph.evidence", "C5", "Conservative validity model; requires a trusted executor attestation verifier."),
        ("typescript_rust", "PARTIAL", "specgraph.extractors", "C6", "Optional grammar wheels; supported constructs and local binding only, no semantic completeness."),
        ("offline_visualizer", "IMPLEMENTED", "specgraph.report", "C6", "Bounded offline snapshot; omitted relations constrain path and impact queries."),
        ("mcp_read_only", "PARTIAL", "specgraph.mcp_server", "C2", "Minimal read-only gateway; qualification is per protocol and host."),
        ("adaptive_hooks", "PARTIAL", "specgraph.hook_context", "C7", "Role-aware advisory context; host delivery and task savings require separate observation."),
    ]
    return {"schema": "tare.tools/specgraph-capabilities/1", "qualification": "NOT_A_RELEASE_ATTESTATION",
            "capabilities": [{"id": name, "status": status if module is None or find_spec(module) else "PLANNED",
                              "implementation": module, "spec": f"SPEC-SPECGRAPH-{work}",
                              "work_id": f"SPECGRAPH-COMPLETION-{work}", "limits": limit,
                              "verification": "SPEC verification command and delivery evidence; tools/check_completion_contracts.py checks documents only",
                              "qualification": "NOT_A_RELEASE_ATTESTATION"}
                             for name, status, module, work, limit in capabilities]}


def _effectiveness_observation(path: Path | str) -> dict[str, Any]:
    """Read an explicitly selected experiment; never infer production benefit.

    @spec SPEC-SPECGRAPH-C7:AC-04
    """
    base = {"status": "UNAVAILABLE", "authority": "NONE", "production_effectiveness": "NOT_ESTABLISHED"}
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(262145)
        if len(raw) > 262144:
            return base | {"reason": "EXPERIMENT_EXCEEDS_BUDGET"}
        value = json.loads(raw)
        if value.get("schema") != "tare.tools/hook-paired-evaluation/1" or value.get("status") != "OBSERVED":
            return base | {"reason": "UNRECOGNIZED_EXPERIMENT"}
        roles = value["roles"]
        if set(roles) != {"planner", "implementer", "auditor", "explorer"}:
            raise ValueError("Missing role measurements")
        for report in roles.values():
            for arm in ("file_context", "hook_context"):
                counts = report["arms"][arm]
                if not (type(counts["attempts"]) is int and type(counts["successes"]) is int
                        and 0 <= counts["successes"] <= counts["attempts"] <= 10000):
                    raise ValueError("Invalid observation counts")
        if len(json.dumps(roles).encode()) > 8192:
            raise ValueError("Role report exceeds budget")
        return base | {"status": "REPORTED_EXPERIMENT", "report_sha256": hashlib.sha256(raw).hexdigest(),
                       "roles": roles, "attestation": "NOT_VERIFIED", "source": str(path)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return base | {"reason": "EXPERIMENT_UNAVAILABLE_OR_INVALID"}


def project_doctor(root: Path | str, *, state_dir: Path | str | None = None,
                   effectiveness_report: Path | str | None = None) -> dict[str, Any]:
    """Read configuration and snapshot metadata, never build or validate sources.

    @spec SPEC-SPECGRAPH-C0 [AC-01, AC-04]
    """
    root = Path(root).resolve()
    config = SpecGraphConfig.load(root)
    report: dict[str, Any] = {
        "schema": "tare.tools/specgraph-doctor/1", "root": str(root),
        "status": "NOT_CONFIGURED", "mode": "metadata", "executable": sys.executable,
        "sqlite_version": sqlite3.sqlite_version, "index_path": str(index_path(root, state_dir=state_dir)),
        "configuration": {"errors": config.config_errors, "spec_paths": config.spec_paths,
                          "indexed_paths": config.indexed_paths if config.indexed_paths is not None else config.governed_paths,
                          "governed_paths": config.governed_paths, "search_paths": config.search_paths},
        "capabilities": capability_catalog()["capabilities"],
        "source_validation": "NOT_RUN; use doctor --deep for live governance validation",
    }
    from .hook_context import hook_doctor
    report["hooks"] = hook_doctor(root)
    if effectiveness_report is not None:
        observation = _effectiveness_observation(effectiveness_report)
        report["hooks"]["effectiveness"] = observation["status"]
        report["effectiveness_observation"] = observation
    from .lifecycle import lifecycle_status
    report["lifecycle"] = lifecycle_status(root, state_dir=state_dir)
    if not root.is_dir() or config.config_errors:
        report["status"] = "INVALID_CONFIGURATION"
        return report
    try:
        with RepositoryIndex(root, state_dir=state_dir) as index:
            report["snapshot"] = index.metadata()
        report["index_status"] = report["snapshot"]["status"]
        from .lexical import PROJECTION_VERSION
        report["retrieval_projection"] = {
            "status": "AVAILABLE" if report["snapshot"].get("retrieval_projection_version") == PROJECTION_VERSION
                      else "REINDEX_REQUIRED",
            "expected_version": PROJECTION_VERSION,
            "published_version": report["snapshot"].get("retrieval_projection_version"),
            "fts5": report["snapshot"]["capabilities"].get("fts5", False)}
        if not report["retrieval_projection"]["fts5"]:
            report["retrieval_projection"]["status"] = "UNAVAILABLE_FTS5"
        report["governance_status"] = report["snapshot"].get("governance", {}).get("status", "NOT_RECORDED")
        report["status"] = "PARTIAL" if (report["index_status"] == "PARTIAL" or report["governance_status"] != "PASS") else "AVAILABLE"
        if report["status"] == "PARTIAL":
            report["next_action"] = "Inspect published coverage/governance diagnostics; doctor --deep checks current sources"
        if report["retrieval_projection"]["status"] == "REINDEX_REQUIRED":
            report["retrieval_projection"]["next_action"] = "specgraph index"
    except (IndexUnavailableError, sqlite3.DatabaseError, OSError) as exc:
        report["reason"] = str(exc)
        report["next_action"] = "specgraph index"
    return report
