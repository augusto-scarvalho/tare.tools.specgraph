from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .cache import IncrementalCacheEngine


STALENESS_SCHEMA = "tare.tools/specgraph-staleness/1"


@dataclass(frozen=True)
class ChangedPathStalenessReport:
    """Bounded staleness result for exact paths reported by a tool adapter."""

    schema: str
    status: str
    changed_paths: tuple[str, ...]
    observed_changed_paths: tuple[str, ...]
    unchanged_paths: tuple[str, ...]
    impacted_nodes: tuple[str, ...]
    total_impacted_nodes: int
    truncated: bool
    repository_stale: bool
    other_changed_paths_count: int
    generation_digest: str
    reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalize_path(root: Path, value: str) -> str:
    candidate = Path(value)
    absolute = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        return absolute.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("changed path escapes the repository root") from exc


def assess_changed_paths(
    root_dir: Path | str,
    changed_paths: list[str] | tuple[str, ...],
    *,
    max_nodes: int = 100,
    max_paths: int = 16,
) -> ChangedPathStalenessReport:
    root = Path(root_dir).resolve()
    if not changed_paths:
        raise ValueError("at least one changed path is required")
    if not 1 <= max_nodes <= 1000:
        raise ValueError("max_nodes must be between 1 and 1000")
    if not 1 <= max_paths <= 64:
        raise ValueError("max_paths must be between 1 and 64")
    if len(changed_paths) > max_paths:
        raise ValueError(f"changed paths exceed the {max_paths}-path limit")

    normalized: list[str] = []
    for value in changed_paths:
        clean = value.strip()
        if not clean:
            raise ValueError("changed path must not be empty")
        if len(clean.encode("utf-8")) > 1024:
            raise ValueError("changed path exceeds 1024 UTF-8 bytes")
        rel_path = _normalize_path(root, clean)
        if rel_path not in normalized:
            normalized.append(rel_path)
    normalized.sort()

    manifest = IncrementalCacheEngine.load_manifest(root)
    if manifest is None:
        return ChangedPathStalenessReport(
            schema=STALENESS_SCHEMA,
            status="UNAVAILABLE",
            changed_paths=tuple(normalized),
            observed_changed_paths=(),
            unchanged_paths=tuple(normalized),
            impacted_nodes=(),
            total_impacted_nodes=0,
            truncated=False,
            repository_stale=True,
            other_changed_paths_count=0,
            generation_digest="",
            reason="initialized SpecGraph cache is required",
        )

    stale = IncrementalCacheEngine.detect_staleness(root_dir=root)
    repository_changes = set(stale.modified_files + stale.added_files + stale.deleted_files)
    requested = set(normalized)
    observed = sorted(requested & repository_changes)
    unchanged = sorted(requested - repository_changes)
    seeds = {
        node
        for path in observed
        for node in (
            manifest.files[path].extracted_nodes if path in manifest.files else []
        )
    }
    impacted = IncrementalCacheEngine._compute_transitive_blast_radius(
        seeds, manifest.reverse_deps
    )
    bounded = tuple(impacted[:max_nodes])
    status = "STALE" if observed else "FRESH"
    reason = None
    if stale.discovery_errors:
        status = "UNAVAILABLE"
        reason = "repository discovery failed during staleness verification"

    return ChangedPathStalenessReport(
        schema=STALENESS_SCHEMA,
        status=status,
        changed_paths=tuple(normalized),
        observed_changed_paths=tuple(observed),
        unchanged_paths=tuple(unchanged),
        impacted_nodes=bounded,
        total_impacted_nodes=len(impacted),
        truncated=len(impacted) > max_nodes,
        repository_stale=stale.is_stale,
        other_changed_paths_count=len(repository_changes - requested),
        generation_digest=manifest.generation_digest,
        reason=reason,
    )
