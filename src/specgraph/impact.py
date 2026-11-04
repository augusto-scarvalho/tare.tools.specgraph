from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .cache import IncrementalCacheEngine


IMPACT_SCHEMA = "tare.tools/specgraph-impact/1"


@dataclass(frozen=True)
class ImpactReport:
    """Bounded, deterministic preview of the cached downstream blast radius."""

    schema: str
    status: str
    query: str
    source_kind: str | None
    source_nodes: tuple[str, ...]
    impacted_nodes: tuple[str, ...]
    total_impacted_nodes: int
    truncated: bool
    generation_digest: str
    reason: str | None = None
    stale_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalize_cached_path(root: Path, query: str) -> str:
    candidate = Path(query)
    absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("impact path escapes the repository root") from exc


def preview_impact(
    root_dir: Path | str,
    query: str,
    *,
    max_nodes: int = 100,
) -> ImpactReport:
    """Preview impact from one exact cached path or ``symbol:<node-id>`` query.

    The cache is deliberately treated as an immutable read model. A missing or
    stale cache never falls back to a live graph build because that would hide
    an invalid pre-edit premise from callers.
    """

    root = Path(root_dir).resolve()
    clean_query = query.strip()
    if not clean_query:
        raise ValueError("impact query must not be empty")
    if len(clean_query.encode("utf-8")) > 1024:
        raise ValueError("impact query exceeds 1024 UTF-8 bytes")
    if not 1 <= max_nodes <= 1000:
        raise ValueError("max_nodes must be between 1 and 1000")

    manifest = IncrementalCacheEngine.load_manifest(root)
    if manifest is None:
        return ImpactReport(
            schema=IMPACT_SCHEMA,
            status="UNAVAILABLE",
            query=clean_query,
            source_kind=None,
            source_nodes=(),
            impacted_nodes=(),
            total_impacted_nodes=0,
            truncated=False,
            generation_digest="",
            reason="initialized SpecGraph cache is required",
        )

    stale = IncrementalCacheEngine.detect_staleness(root_dir=root)
    if stale.is_stale:
        stale_files = sorted(
            set(stale.modified_files + stale.added_files + stale.deleted_files)
        )
        return ImpactReport(
            schema=IMPACT_SCHEMA,
            status="STALE",
            query=clean_query,
            source_kind=None,
            source_nodes=(),
            impacted_nodes=(),
            total_impacted_nodes=0,
            truncated=False,
            generation_digest=manifest.generation_digest,
            reason="SpecGraph cache does not match current repository bytes",
            stale_files=tuple(stale_files),
        )

    source_kind: str
    source_nodes: tuple[str, ...]
    if clean_query.startswith("symbol:"):
        source_kind = "symbol"
        node_id = clean_query[len("symbol:") :].strip()
        if not node_id:
            raise ValueError("symbol impact query must include an exact node ID")
        known_nodes = {
            node
            for fingerprint in manifest.files.values()
            for node in fingerprint.extracted_nodes
        }
        known_nodes.update(manifest.reverse_deps)
        for dependents in manifest.reverse_deps.values():
            known_nodes.update(dependents)
        source_nodes = (node_id,) if node_id in known_nodes else ()
    else:
        source_kind = "path"
        rel_path = _normalize_cached_path(root, clean_query)
        fingerprint = manifest.files.get(rel_path)
        source_nodes = tuple(sorted(set(fingerprint.extracted_nodes))) if fingerprint else ()

    if not source_nodes:
        return ImpactReport(
            schema=IMPACT_SCHEMA,
            status="MISS",
            query=clean_query,
            source_kind=source_kind,
            source_nodes=(),
            impacted_nodes=(),
            total_impacted_nodes=0,
            truncated=False,
            generation_digest=manifest.generation_digest,
            reason="exact path or node is not present in the fresh cache",
        )

    impacted = IncrementalCacheEngine._compute_transitive_blast_radius(
        set(source_nodes), manifest.reverse_deps
    )
    bounded = tuple(impacted[:max_nodes])
    return ImpactReport(
        schema=IMPACT_SCHEMA,
        status="HIT",
        query=clean_query,
        source_kind=source_kind,
        source_nodes=source_nodes,
        impacted_nodes=bounded,
        total_impacted_nodes=len(impacted),
        truncated=len(impacted) > max_nodes,
        generation_digest=manifest.generation_digest,
    )
