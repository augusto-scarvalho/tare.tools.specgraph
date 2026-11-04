"""Bounded postings-based retrieval and declared document-to-code expansion.

@spec SPEC-SPECGRAPH-LEXICAL-INTENT
"""
from __future__ import annotations

import json
from typing import Any

from .lexical import (CANDIDATE_LIMIT, PROJECTION_VERSION, is_identifier,
                      grouped_expression, query_groups, query_terms, tokens)
from .storage import RepositoryIndex


def _exact(index: RepositoryIndex, query: str, limit: int) -> list[dict[str, Any]]:
    folded = query.strip().casefold()
    for clause, args, score, reason in (
        ("id_fold=?", (folded,), 500, "exact_id"),
        ("name_fold=? OR leaf_fold=?", (folded, folded), 400, "exact_name"),
        ("path_fold=?", (folded,), 100, "exact_path"),
    ):
        rows = index.connection.execute(
            f"SELECT payload FROM symbols WHERE {clause} ORDER BY id_fold, symbol_id LIMIT ?",
            (*args, limit))
        hits = [json.loads(row[0]) | {"score": score, "reason": reason} for row in rows]
        if hits:
            return hits
    return []


def _lexical(index: RepositoryIndex, query: str, *, documents: bool
             ) -> tuple[list[dict[str, Any]], bool]:
    terms = query_terms(query)
    if not terms:
        return [], False
    identifier = is_identifier(query)
    groups = query_groups(terms, identifier=identifier)
    expression = grouped_expression(groups, identifier=identifier)
    table, records, key, weights = (
        ("document_fts", "documents", "document_id", "0, 8, 2") if documents else
        ("symbol_fts", "symbols", "symbol_id", "0, 8, 1, 4"))
    # FTS postings generate candidates. No LIKE/instr scan or graph load.
    rows = index.connection.execute(f"""
        SELECT r.payload, bm25({table}, {weights}) FROM {table}
        JOIN {records} r ON r.{key}={table}.{key}
        WHERE {table} MATCH ? ORDER BY bm25({table}, {weights}), r.{key} LIMIT ?
    """, (expression, CANDIDATE_LIMIT + 1)).fetchall()
    hits = []
    for raw, rank in rows[:CANDIDATE_LIMIT]:
        item = json.loads(raw)
        fields = ("title", "body") if documents else ("name", "file_path", "docstring")
        vocabulary = {field: set(tokens(item.get(field) or "")) for field in fields}
        matched = [term for term, group in zip(terms, groups)
                   if any(set(group) & words for words in vocabulary.values())]
        # A single generic word in a long question is weak evidence. No fuzzy
        # fallback for absent composed identifiers, which use conjunction above.
        if len(matched) < min(2, len(terms)):
            continue
        field_weights = {"title": 2, "body": 1} if documents else {"name": 2, "file_path": 0.25, "docstring": 1}
        relevance = sum(max((field_weights[field] for field, words in vocabulary.items()
                             if set(group) & words), default=0) for group in groups)
        expansions = {term: sorted(set(group[1:]) & set().union(*vocabulary.values()))
                      for term, group in zip(terms, groups)}
        expansions = {term: variants for term, variants in expansions.items() if variants}
        item.update({"score": -rank, "reason": "fts5_document_match" if documents else "fts5_symbol_match",
                     "match": {"terms": matched, "coverage": round(len(matched) / len(terms), 4),
                               "field_relevance": relevance,
                               "fields": [f for f, words in vocabulary.items() if any(set(g) & words for g in groups)],
                               "identity": False}})
        if expansions:
            item["match"]["expansions"] = expansions
        hits.append(item)
    hits.sort(key=lambda h: (-h["match"]["field_relevance"], -h["match"]["coverage"], -h["score"], h[key]))
    return hits, len(rows) > CANDIDATE_LIMIT


def retrieve(index: RepositoryIndex, query: str, *, limit: int = 20,
             max_hops: int = 2, edge_budget: int = 128, max_nodes: int = 100
             ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return definitions first; expand only published SPEC/ADR intent links."""
    index._validate_query(query, limit)
    omissions: list[dict[str, Any]] = []
    exact = _exact(index, query, min(limit + 1, 101))
    if exact:
        if len(exact) > limit:
            omissions.append({"reason": "limit", "count": "AT_LEAST_ONE"})
        return exact[:limit], omissions
    metadata = index.metadata()
    if not metadata["capabilities"].get("fts5"):
        return [], [{"reason": "FTS5_UNAVAILABLE", "unit": "lexical_search"}]
    if metadata.get("retrieval_projection_version") != PROJECTION_VERSION:
        # Read compatibility is retained; only the explicit writer upgrades.
        return index.search_documents(query, limit), [{"reason": "REINDEX_REQUIRED", "unit": "code_lexical_search"}]
    symbols, code_capped = _lexical(index, query, documents=False)
    documents, docs_capped = _lexical(index, query, documents=True)
    if code_capped or docs_capped:
        omissions.append({"reason": "lexical_candidate_limit", "count": "UNKNOWN", "per_index": CANDIDATE_LIMIT})
    seen = {s["symbol_id"] for s in symbols}
    remaining = max(0, edge_budget)
    visited = {s["symbol_id"] for s in symbols[:limit]}
    def admit(node: str) -> bool:
        if node not in visited and len(visited) >= max_nodes:
            omissions.append({"reason": "intent_node_limit", "count": "AT_LEAST_ONE"})
            return False
        visited.add(node)
        return True
    for document in documents[:min(limit, 10)]:
        if len(symbols) >= limit:
            break  # No definition slot remains; do not expand unused context.
        if max_hops == 0 or document["kind"] not in ("spec", "adr"):
            continue
        node_id = document["node"]["id"]
        if not admit(node_id):
            break
        specs = [(node_id, [])] if document["kind"] == "spec" else []
        if document["kind"] == "adr" and max_hops >= 2:
            rows = index.connection.execute(
                "SELECT payload FROM edges WHERE target=? AND relation='SPEC_GOVERNED_BY_ADR' ORDER BY source, payload LIMIT ?",
                (node_id, remaining + 1)).fetchall()
            if len(rows) > remaining:
                omissions.append({"reason": "intent_edge_budget", "count": "AT_LEAST_ONE"})
            edges = [json.loads(r[0]) for r in rows[:remaining]]
            remaining -= len(edges)
            specs = [(e["from"], [e]) for e in edges if admit(e["from"])]
        for spec_id, tail in specs:
            rows = index.connection.execute("""
                SELECT s.payload, e.payload FROM edges e JOIN symbols s ON s.symbol_id=e.source
                WHERE e.target=? AND e.relation='CODE_IMPLEMENTS_SPEC'
                ORDER BY e.source, e.payload LIMIT ?
            """, (spec_id, remaining + 1)).fetchall()
            if len(rows) > remaining:
                omissions.append({"reason": "intent_edge_budget", "count": "AT_LEAST_ONE"})
            selected = rows[:remaining]
            remaining -= len(selected)
            for raw, edge in selected:
                symbol = json.loads(raw)
                if not admit(symbol["symbol_id"]):
                    continue
                if symbol["symbol_id"] in seen:
                    continue
                seen.add(symbol["symbol_id"])
                source = index.connection.execute("SELECT sha256 FROM files WHERE file_path=?", (document["file_path"],)).fetchone()
                symbols.append(symbol | {"score": document["score"], "reason": "declared_intent_match",
                    "match": document["match"], "via_document": document["document_id"],
                    "via_source": {"root": str(index.root), "file_path": document["file_path"],
                                   "sha256": source[0] if source else None},
                    "via_edges": [json.loads(edge)] + tail})
    if len(documents) > min(limit, 10) and max_hops:
        omissions.append({"reason": "intent_document_limit", "count": "UNKNOWN"})
    hits = symbols + documents
    if len(hits) > limit:
        omissions.append({"reason": "limit", "count": "AT_LEAST_ONE"})
    selected_hits = []
    delivered_nodes: set[str] = set()
    for hit in hits[:limit]:
        identity = hit["symbol_id"] if "symbol_id" in hit else hit["document_id"].split(":", 1)[-1]
        nodes = {identity}
        for edge in hit.get("via_edges", []):
            nodes.update((edge["from"], edge["to"]))
        if len(delivered_nodes | nodes) > max_nodes:
            omissions.append({"reason": "intent_node_limit", "count": "AT_LEAST_ONE"})
            continue
        delivered_nodes.update(nodes)
        selected_hits.append(hit)
    # Deduplicate repeated budget boundaries, keeping explicit incompleteness.
    omissions = list({item["reason"]: item for item in omissions}.values())
    return selected_hits, omissions
