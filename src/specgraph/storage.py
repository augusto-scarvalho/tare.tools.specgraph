"""Local, transactional read models. Opening an index never reads source files."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from .serialization import canonical_json
from .lexical import indexed_text

SCHEMA_VERSION = 1


class IndexUnavailableError(ValueError):
    """The selected repository has no compatible published index."""


def repository_identity(root: Path | str) -> str:
    return os.path.normcase(str(Path(root).resolve()))


def index_path(root: Path | str, *, state_dir: Path | str | None = None) -> Path:
    """Return the native-platform state path without creating it."""
    if state_dir is None:
        override = os.environ.get("TARE_SPECGRAPH_STATE_DIR")
        if override:
            state_dir = override
        elif os.name == "nt":
            state_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "specgraph"
        else:
            state_dir = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "specgraph"
    key = hashlib.sha256(repository_identity(root).encode("utf-8")).hexdigest()
    return Path(state_dir).expanduser() / "repositories" / key / "index.sqlite3"


def _initialize(connection: sqlite3.Connection) -> bool:
    """Initialize compatible tables; called only by the explicit writer."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, SCHEMA_VERSION):
        raise IndexUnavailableError(f"Unsupported index schema {version}; expected {SCHEMA_VERSION}")
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS revisions (
            revision_seq INTEGER PRIMARY KEY, content_hash TEXT NOT NULL, graph_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS files (
            file_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL,
            artifact_kind TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (
            file_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS symbols (
            symbol_id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
            file_path TEXT NOT NULL, start_line INTEGER NOT NULL, end_line INTEGER NOT NULL,
            name_fold TEXT NOT NULL, leaf_fold TEXT NOT NULL, id_fold TEXT NOT NULL,
            path_fold TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS symbol_name ON symbols(name_fold);
        CREATE INDEX IF NOT EXISTS symbol_leaf ON symbols(leaf_fold);
        CREATE INDEX IF NOT EXISTS symbol_path ON symbols(file_path);
        CREATE INDEX IF NOT EXISTS symbol_id_fold ON symbols(id_fold);
        CREATE INDEX IF NOT EXISTS symbol_path_fold ON symbols(path_fold);
        CREATE TABLE IF NOT EXISTS edges (
            source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS edge_source ON edges(source, relation);
        CREATE INDEX IF NOT EXISTS edge_target ON edges(target, relation);
        CREATE TABLE IF NOT EXISTS documents (
            document_id TEXT PRIMARY KEY, kind TEXT NOT NULL, file_path TEXT NOT NULL,
            title TEXT NOT NULL, body TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS document_path ON documents(file_path);
    """)
    fts5 = True
    try:
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5(symbol_id UNINDEXED, name, file_path, docstring)")
        connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(document_id UNINDEXED, title, body)")
    except sqlite3.OperationalError as exc:
        if "no such module: fts5" not in str(exc).lower():
            raise
        fts5 = False
    connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return fts5


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT value FROM metadata WHERE key='snapshot'").fetchone()
    if row is None:
        raise IndexUnavailableError("No published index; run specgraph index for the selected root")
    return json.loads(row[0])


def _validate_root(metadata: dict[str, Any], root: Path | str) -> None:
    if metadata.get("repository_root") != repository_identity(root):
        raise IndexUnavailableError("Index belongs to a different repository root")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise IndexUnavailableError("Index metadata schema is incompatible")


def _publish(connection: sqlite3.Connection, payload: dict[str, Any], metadata: dict[str, Any],
             files: dict[str, dict[str, Any]], documents: list[dict[str, Any]], *, fts5: bool,
             sources: dict[str, str] | None = None) -> None:
    """Replace K=1 state inside the caller's BEGIN IMMEDIATE transaction."""
    for table in ("revisions", "files", "sources", "symbols", "edges", "documents"):
        connection.execute(f"DELETE FROM {table}")
    if fts5:
        connection.execute("DELETE FROM symbol_fts")
        connection.execute("DELETE FROM document_fts")
    connection.executemany("INSERT INTO files VALUES (?, ?, ?, ?)", [
        (path, item["sha256"], item["byte_size"], item["artifact_kind"])
        for path, item in sorted(files.items())
    ])
    connection.executemany("INSERT INTO sources VALUES (?, ?, ?)", [
        (path, files[path]["sha256"], body) for path, body in sorted((sources or {}).items())
    ])
    for symbol in payload["symbols"]:
        name, sid, path = symbol["name"], symbol["symbol_id"], symbol["file_path"]
        connection.execute("INSERT INTO symbols VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (sid, name, symbol["kind"], path, symbol["start_line"], symbol["end_line"],
                            name.casefold(), name.casefold().rsplit(".", 1)[-1], sid.casefold(),
                            path.casefold(), canonical_json(symbol)))
        if fts5:
            connection.execute("INSERT INTO symbol_fts VALUES (?, ?, ?, ?)",
                               (sid, indexed_text(name), indexed_text(path),
                                indexed_text(symbol.get("docstring") or "")))
    connection.executemany("INSERT INTO edges VALUES (?, ?, ?, ?)", [
        (edge["from"], edge["to"], edge["type"], canonical_json(edge)) for edge in payload["edges"]
    ])
    for document in documents:
        connection.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)",
                           (document["document_id"], document["kind"], document["file_path"],
                            document["title"], document["body"], canonical_json(document)))
        if fts5:
            connection.execute("INSERT INTO document_fts VALUES (?, ?, ?)",
                               (document["document_id"], indexed_text(document["title"]),
                                indexed_text(document["body"])))
    connection.execute("INSERT INTO revisions VALUES (?, ?, ?)",
                       (metadata["revision_seq"], metadata["content_hash"], canonical_json(payload)))
    connection.execute("INSERT OR REPLACE INTO metadata VALUES ('snapshot', ?)", (canonical_json(metadata),))


class RepositoryIndex:
    """Read-only, source-independent snapshot pinned for this context's lifetime.

    Freshness describes the published source observation, never current worktree
    freshness. Use a new context to observe a later committed generation.

@spec SPEC-SPECGRAPH-C2
    """

    def __init__(self, root: Path | str, *, state_dir: Path | str | None = None,
                 timeout_s: float = 5.0):
        if not isinstance(timeout_s, (int, float)) or not math.isfinite(timeout_s) or not 0 <= timeout_s <= 5:
            raise ValueError("timeout_s must be finite and between 0 and 5 seconds")
        self.timeout_s = timeout_s
        self.root = Path(root).resolve()
        self.path = index_path(self.root, state_dir=state_dir)
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> RepositoryIndex:
        if self._connection is not None:
            raise RuntimeError("RepositoryIndex context is already open")
        if not self.path.is_file():
            raise IndexUnavailableError("No published index; run specgraph index for the selected root")
        connection = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                     isolation_level=None, timeout=self.timeout_s)
        try:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise IndexUnavailableError("Index schema is incompatible")
            connection.execute("BEGIN DEFERRED")
            _validate_root(_metadata(connection), self.root)  # First read pins the WAL snapshot.
        except Exception:
            connection.close()
            raise
        self._connection = connection
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.execute("ROLLBACK" if exc_type else "COMMIT")
            finally:
                connection.close()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("Use RepositoryIndex in a with statement")
        return self._connection

    def metadata(self) -> dict[str, Any]:
        return _metadata(self.connection)

    def snapshot_payload(self) -> dict[str, Any]:
        """Return persisted graph records without invoking graph/source code."""
        return json.loads(self.connection.execute("SELECT graph_json FROM revisions").fetchone()[0])

    @staticmethod
    def _validate_query(query: str, limit: int) -> str:
        normalized = query.strip().casefold()
        if not normalized:
            raise ValueError("query must not be empty")
        if len(normalized.encode("utf-8")) > 512:
            raise ValueError("query exceeds 512 UTF-8 bytes")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return normalized

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Preserve literal search ranking; exact names use B-tree indexes.

        Partial-name fallback scans materialized SQLite columns, not repository
        files. It intentionally preserves substring semantics instead of
        pretending FTS token matching is equivalent.
        """
        normalized = self._validate_query(query, limit)
        exact = self.connection.execute(
            "SELECT payload, 400 FROM symbols WHERE name_fold=? OR leaf_fold=? ORDER BY id_fold, symbol_id LIMIT ?",
            (normalized, normalized, limit)).fetchall()
        remaining = limit - len(exact)
        rows = list(exact)
        if remaining:
            rows.extend(self.connection.execute("""
                SELECT payload, CASE
                    WHEN substr(name_fold, -length(?) - 1) = '.' || ? THEN 300
                    WHEN instr(name_fold, ?) > 0 THEN 200 ELSE 100 END AS score
                FROM symbols WHERE name_fold != ? AND leaf_fold != ?
                    AND (instr(name_fold, ?) > 0 OR instr(id_fold, ?) > 0 OR instr(path_fold, ?) > 0)
                ORDER BY score DESC, id_fold, symbol_id LIMIT ?
            """, (normalized, normalized, normalized, normalized, normalized,
                  normalized, normalized, normalized, remaining)).fetchall())
        results = []
        for raw, score in rows:
            symbol = json.loads(raw)
            results.append({key: symbol[key] for key in (
                "symbol_id", "name", "kind", "file_path", "start_line", "end_line", "linked_specs")} | {"score": score})
        return results

    def neighbors(self, symbol_id: str, *, direction: str = "both", relation: str | None = None,
                  limit: int = 20) -> list[dict[str, Any]]:
        if direction not in ("in", "out", "both") or not 1 <= limit <= 100:
            raise ValueError("direction must be in/out/both and limit between 1 and 100")
        clause = {"in": "target=?", "out": "source=?", "both": "(source=? OR target=?)"}[direction]
        params: list[Any] = [symbol_id] * (2 if direction == "both" else 1)
        if relation is not None:
            clause += " AND relation=?"
            params.append(relation)
        params.append(limit)
        rows = self.connection.execute(
            f"SELECT payload FROM edges WHERE {clause} ORDER BY source, relation, target LIMIT ?", params)
        return [json.loads(row[0]) for row in rows]

    def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        normalized = self._validate_query(query, limit)
        if not self.metadata()["capabilities"]["fts5"]:
            raise IndexUnavailableError("FTS5 is unavailable in this SQLite build; document search is disabled")
        tokens = re.findall(r"\w+", normalized, flags=re.UNICODE)
        if not tokens:
            return []
        literal = " AND ".join('"' + token + '"' for token in tokens)
        rows = self.connection.execute("""
            SELECT d.payload, bm25(document_fts) AS score FROM document_fts
            JOIN documents d ON d.document_id=document_fts.document_id
            WHERE document_fts MATCH ? ORDER BY score, d.document_id LIMIT ?
        """, (literal, limit))
        return [json.loads(row[0]) | {"score": row[1]} for row in rows]
