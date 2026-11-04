"""Explicit owner checkouts for ``specgraph ground --read-bodies --owner REPOSITORY=PATH``.

The federated ontology catalog loader that lived here was removed with the
Library's catalog (SPEC-SPECGRAPH-GOVERNANCE-001 revision 1.0.2). Each
repository keeps its ontology in its own tree.
"""
from __future__ import annotations

from pathlib import Path


class OntologyFederationError(ValueError):
    """Raised for an invalid explicit owner selection."""


def parse_owner_roots(values: list[str], base: Path) -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for value in values:
        repository, separator, raw_path = value.partition("=")
        if not separator or not repository.strip() or not raw_path.strip():
            raise OntologyFederationError("--owner must use REPOSITORY=PATH")
        repository = repository.strip()
        if repository in roots:
            raise OntologyFederationError(f"owner root supplied twice: {repository}")
        path = Path(raw_path.strip())
        roots[repository] = (path if path.is_absolute() else base / path).resolve()
    return roots
