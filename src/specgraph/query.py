"""Bounded, deterministic project queries over one published SQLite snapshot."""
from __future__ import annotations

from collections import deque
import json
from pathlib import Path
from typing import Any

from .storage import RepositoryIndex
from .retrieval import retrieve

MODES = {"search", "symbol", "refs", "neighbors", "path", "explain", "context"}


def serialize_query(response: dict[str, Any]) -> str:
    """The budgeted wire representation, including its final newline."""
    return json.dumps(response, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"


def _symbol(index: RepositoryIndex, symbol_id: str) -> dict[str, Any] | None:
    row = index.connection.execute("SELECT payload FROM symbols WHERE symbol_id=?", (symbol_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _select(index: RepositoryIndex, query: str, limit: int) -> tuple[list[dict[str, Any]], bool]:
    exact = _symbol(index, query)
    if exact:
        return [exact], False
    hits = index.search(query, limit=min(limit + 1, 100))
    # A symbolic selector may identify a qualified name or an exact file path;
    # substring matches remain candidates, never silently promoted to identity.
    exact_names = [hit for hit in hits if hit["name"].casefold() == query.casefold()
                   or hit["name"].rsplit(".", 1)[-1].casefold() == query.casefold()]
    chosen = exact_names or hits
    truncated = len(chosen) > limit or (len(hits) == 100 and limit == 100)
    return [_symbol(index, item["symbol_id"]) for item in chosen[:limit]], truncated


def _unit(index: RepositoryIndex, symbol: dict[str, Any], reason: str, *, details: bool = False) -> dict[str, Any]:
    unit = {key: symbol[key] for key in ("symbol_id", "name", "kind", "file_path", "start_line", "end_line", "linked_specs")}
    # The published edge table includes frontmatter and accepted exact mappings;
    # the extractor's linked_specs field only knows docstring annotations.
    links = index.connection.execute(
        "SELECT DISTINCT target FROM edges WHERE source=? AND relation='CODE_IMPLEMENTS_SPEC' ORDER BY target LIMIT 33",
        (symbol["symbol_id"],)).fetchall()
    unit["linked_specs"] = [r[0] for r in links[:32]]
    if len(links) > 32:
        unit["linked_specs_complete"] = False
    row = index.connection.execute("SELECT sha256 FROM files WHERE file_path=?", (symbol["file_path"],)).fetchone()
    unit.update({"reason": reason, "source": {"root": str(index.root), "file_path": symbol["file_path"],
                                                "sha256": row[0] if row else None,
                                                "start_line": symbol["start_line"], "end_line": symbol["end_line"]}})
    if details:
        unit.update({"docstring": symbol.get("docstring"), "references": symbol.get("references", []),
                     "binding_status": symbol.get("binding_status", "unknown"),
                     "source_snippet": {"status": "NOT_STORED", "representation": "definition_range_and_docstring",
                                        "current_source_read": False}})
        if index.metadata().get("capabilities", {}).get("source_ranges"):
            source = index.connection.execute("SELECT body, sha256 FROM sources WHERE file_path=?", (symbol["file_path"],)).fetchone()
            if source is not None:
                lines = source[0].splitlines(keepends=True)
                start, end = symbol["start_line"], symbol["end_line"]
                if 1 <= start <= end <= len(lines):
                    unit["source_snippet"] = {"status": "AVAILABLE", "body": "".join(lines[start - 1:end]),
                                               "sha256": source[1], "representation": "persisted_source_range",
                                               "current_source_read": False}
                else:
                    unit["source_snippet"]["status"] = "INVALID_STORED_RANGE"
    return unit


def _intent_context(index: RepositoryIndex, symbols: list[dict[str, Any]], response: dict[str, Any]) -> None:
    """Append optional complete paths after definitions, never a generic BFS."""
    hops = min(response["limits"]["max_hops"], 2)
    capacity = min(response["limits"]["limit"], response["limits"]["max_nodes"]) - len(response["results"])
    if not hops:
        return
    if capacity <= 0:
        if any(r.get("linked_specs") for r in response["results"]):
            response["omitted"].append({"reason": "intent_context_limit", "count": "AT_LEAST_ONE"})
        return
    remaining = min(response["limits"]["edge_budget"], 128)
    used_nodes = set()
    for result in response["results"]:
        if "symbol_id" in result:
            used_nodes.add(result["symbol_id"])
        elif "document_id" in result:
            used_nodes.add(result["document_id"].split(":", 1)[-1])
        elif "node" in result:
            node = result["node"]
            used_nodes.add(node.get("symbol_id", node.get("node_id")))
        for edge in result.get("via_edges", []):
            used_nodes.update((edge["from"], edge["to"]))
    node_budget = max(0, response["limits"]["max_nodes"] - len(used_nodes))
    seen = set()
    for symbol in symbols[:3]:
        allowed = min(remaining, capacity, node_budget)
        links = _edges(index, symbol["symbol_id"], "out", "CODE_IMPLEMENTS_SPEC", allowed + 1)
        if len(links) > allowed:
            response["omitted"].append({"reason": "intent_context_limit", "count": "AT_LEAST_ONE"})
        for link in links[:allowed]:
            if node_budget <= 0 or remaining <= 0:
                response["omitted"].append({"reason": "intent_context_limit", "count": "AT_LEAST_ONE"})
                return
            edges = [link]
            remaining -= 1
            node_budget -= 1
            if hops > 1:
                adrs = _edges(index, link["to"], "out", "SPEC_GOVERNED_BY_ADR", min(remaining, node_budget, 4) + 1)
                if len(adrs) > min(remaining, node_budget, 4):
                    response["omitted"].append({"reason": "intent_adr_limit", "count": "AT_LEAST_ONE"})
                edges.extend(adrs[:min(remaining, node_budget, 4)])
                remaining -= len(edges) - 1
                node_budget -= len(edges) - 1
            key = (symbol["symbol_id"], link["to"])
            if key in seen:
                continue
            seen.add(key)
            documents = []
            for node_id in dict.fromkeys(e["to"] for e in edges):
                document = _node(index, node_id, "declared_intent")
                if "file_path" in document:
                    row = index.connection.execute("SELECT sha256 FROM files WHERE file_path=?", (document["file_path"],)).fetchone()
                    document["source"] = {"root": str(index.root), "file_path": document["file_path"],
                                           "sha256": row[0] if row else None}
                documents.append(document)
            response["results"].append({"kind": "intent_context", "symbol_id": symbol["symbol_id"],
                "documents": documents, "edges": edges, "reason": "published_declared_links", "runtime_proof": False})
            capacity -= 1
            if capacity == 0 or remaining == 0:
                return


def _node(index: RepositoryIndex, node_id: str, reason: str) -> dict[str, Any]:
    symbol = _symbol(index, node_id)
    if symbol:
        return _unit(index, symbol, reason)
    row = index.connection.execute("SELECT payload FROM documents WHERE document_id IN (?,?) ORDER BY document_id LIMIT 1",
                                   (f"spec:{node_id}", f"adr:{node_id}")).fetchone()
    if row:
        document = json.loads(row[0])
        return {"node_id": node_id, "kind": document["kind"], "file_path": document["file_path"],
                "title": document["title"], "reason": reason}
    return {"node_id": node_id, "kind": "graph_node", "reason": reason, "source": "NOT_MATERIALIZED"}


def _edges(index: RepositoryIndex, node: str, direction: str, relation: str | None, limit: int) -> list[dict[str, Any]]:
    clause = {"out": "source=?", "in": "target=?", "both": "(source=? OR target=?)"}[direction]
    params: list[Any] = [node, node] if direction == "both" else [node]
    if relation is not None:
        clause += " AND relation=?"
        params.append(relation)
    rows = index.connection.execute(f"SELECT payload FROM edges WHERE {clause} ORDER BY source, relation, target, payload LIMIT ?",
                                    params + [limit])
    return [json.loads(row[0]) for row in rows]


def _walk(index: RepositoryIndex, start: str, target: str | None, direction: str, relation: str | None,
          max_nodes: int, max_hops: int, *, inspect_frontier: bool = True) -> tuple[list[str], list[dict[str, Any]], list[str], bool]:
    queue = deque([(start, 0)])
    visited = {start}
    predecessors: dict[str, tuple[str, dict[str, Any]]] = {}
    nodes, edges, limits = [start], [], set()
    seen_edges = set()
    remaining_edges = max_nodes * 4
    found = start == target
    while queue and not found:
        node, depth = queue.popleft()
        if depth >= max_hops:
            if inspect_frontier and _edges(index, node, direction, relation, 1):
                limits.add("max_hops")
            continue
        candidates = _edges(index, node, direction, relation, remaining_edges + 1)
        if len(candidates) > remaining_edges:
            limits.add("edge_budget")
            candidates = candidates[:remaining_edges]
        remaining_edges -= len(candidates)
        for edge in candidates:
            neighbor = edge["to"] if edge["from"] == node else edge["from"]
            if neighbor not in visited:
                if len(visited) >= max_nodes:
                    limits.add("max_nodes")
                    continue
                visited.add(neighbor)
                nodes.append(neighbor)
                predecessors[neighbor] = (node, edge)
                queue.append((neighbor, depth + 1))
            key = json.dumps(edge, sort_keys=True)
            if key not in seen_edges:
                seen_edges.add(key)
                edges.append(edge)
            if neighbor == target:
                found = True
                break
        if remaining_edges == 0 and queue:
            limits.add("edge_budget")
            break
    if target is not None and found:
        chain, chain_edges = [target], []
        while chain[-1] != start:
            parent, edge = predecessors[chain[-1]]
            chain.append(parent)
            chain_edges.append(edge)
        return list(reversed(chain)), list(reversed(chain_edges)), sorted(limits), True
    return nodes, edges, sorted(limits), found


def _fit(response: dict[str, Any], max_bytes: int, mandatory: int = 0) -> dict[str, Any]:
    omitted_for_bytes = 0
    while len(serialize_query(response).encode("utf-8")) > max_bytes:
        if len(response["results"]) <= mandatory:
            raise ValueError("Required context/metadata exceeds max_bytes; narrow the selection or increase the budget")
        response["results"].pop()
        omitted_for_bytes += 1
        if response["status"] != "AMBIGUOUS":
            response["status"] = "PARTIAL"
        existing = next((item for item in response["omitted"] if item["reason"] == "max_bytes"), None)
        if existing:
            existing["count"] = omitted_for_bytes
        else:
            response["omitted"].append({"reason": "max_bytes", "count": omitted_for_bytes, "unit": "complete_result"})
    return response


def project_query(root: Path | str, query: str, *, mode: str = "search", target: str | None = None,
                  limit: int = 20, max_nodes: int = 100, max_hops: int = 3, max_bytes: int = 8192,
                  state_dir: Path | str | None = None, direction: str | None = None,
                  relation: str | None = None) -> dict[str, Any]:
    """Recover code facts without rebuilding, opening sources, or invoking LLMs.

    Absence is scoped to the published extraction and explicit exploration
    limits. No query claims complete Python runtime/reference knowledge.

@spec SPEC-SPECGRAPH-C4
@spec SPEC-SPECGRAPH-X2
    """
    if mode not in MODES:
        raise ValueError(f"Unsupported query mode: {mode}")
    RepositoryIndex._validate_query(query, limit)
    query = query.strip()
    if target is not None:
        RepositoryIndex._validate_query(target, limit)
        target = target.strip()
    if not 1 <= max_nodes <= 1000 or not 0 <= max_hops <= 10 or not 1024 <= max_bytes <= 65536:
        raise ValueError("max_nodes must be 1..1000, max_hops 0..10, max_bytes 1024..65536")
    direction = direction or ("in" if mode == "refs" else "out" if mode in ("path", "explain") else "both")
    if direction not in ("in", "out", "both"):
        raise ValueError("direction must be in, out or both")
    if mode == "path" and target is None:
        raise ValueError("path mode requires target")
    limit = min(limit, max_nodes)
    with RepositoryIndex(root, state_dir=state_dir) as index:
        metadata = index.metadata()
        coverage = dict(metadata["coverage"])
        # Per-query proof needs scope and extractor availability, not the same
        # language manual repeated in every agent turn. Doctor retains details.
        if "language_extractors" in coverage:
            coverage["language_extractors"] = {language: {key: value.get(key) for key in (
                "status", "engine", "grammar_version")} for language, value in coverage["language_extractors"].items()}
        errors = coverage.pop("errors", [])
        coverage["parse_error_count"] = len(errors)
        if errors:
            coverage["diagnostics"] = "specgraph doctor --format json"
        coverage.update({"method": "published_ast_and_declared_links", "semantic_completeness": "NOT_CLAIMED",
                         "excluded_paths": coverage.get("excluded_paths", "NOT_RECORDED_IN_SNAPSHOT"),
                         "unresolved_scope": "selected_symbols_only", "unresolved_references": []})
        response: dict[str, Any] = {
            "schema": "tare.tools/specgraph-query/1", "root": metadata["repository_root"],
            "revision_seq": metadata["revision_seq"], "content_hash": metadata["content_hash"],
            "status": "HIT", "mode": mode, "query": query, "target": target,
            "coverage": coverage, "freshness": metadata["freshness"], "results": [], "omitted": [],
            "limits": {"limit": limit, "max_nodes": max_nodes, "max_hops": max_hops,
                       "max_bytes": max_bytes, "direction": direction, "relation": relation,
                       "edge_budget": max_nodes * 4, "serialization": "compact_json_utf8_with_final_lf"}}
        if mode == "search":
            hits, omissions = retrieve(index, query, limit=limit, max_hops=min(max_hops, 2),
                                       edge_budget=min(max_nodes * 4, 128), max_nodes=max_nodes)
            response["omitted"].extend(omissions)
            response["limits"].update({"lexical_candidates_per_index": 100, "intent_max_hops": min(max_hops, 2),
                                        "intent_edge_budget": min(max_nodes * 4, 128)})
            symbols = []
            for hit in hits:
                if "symbol_id" in hit:
                    symbols.append(hit)
                    unit = _unit(index, hit, hit["reason"]) | {"score": hit["score"]}
                else:
                    unit = {key: hit[key] for key in ("document_id", "kind", "file_path", "title", "score")}
                    unit.update({"reason": hit.get("reason", "fts5_document_match"), "source_root": str(index.root)})
                for key in ("match", "via_document", "via_edges", "via_source"):
                    if key in hit:
                        unit[key] = hit[key]
                response["results"].append(unit)
            _intent_context(index, symbols, response)
            if not response["results"]:
                response["status"] = "UNKNOWN" if response["omitted"] or metadata["status"] == "PARTIAL" else "NOT_FOUND_IN_SNAPSHOT"
                response["absence"] = {"claim": "no matching indexed records", "universal": False,
                                       "current_worktree_checked": False, "runtime_behavior_checked": False}
            elif response["omitted"] or metadata["status"] == "PARTIAL":
                response["status"] = "PARTIAL"
            return _fit(response, max_bytes)

        selected, truncated = _select(index, query, limit)
        if truncated:
            response["omitted"].append({"reason": "candidate_limit", "count": "UNKNOWN"})
        if len(selected) != 1 or truncated:
            response["status"] = "AMBIGUOUS" if selected else "UNKNOWN"
            response["results"] = [_unit(index, symbol, "selector_candidate") for symbol in selected]
            response["absence"] = {"claim": "selector did not identify one indexed symbol", "universal": False}
            return _fit(response, max_bytes)
        seed = selected[0]
        unresolved = [ref for ref in seed.get("references", []) if ref.get("status") != "resolved"]
        # Keep complete references in required symbol/context units. Coverage
        # summarizes them without duplicating potentially large payloads.
        coverage["unresolved_references"] = {"count": len(unresolved), "statuses": sorted({r.get("status", "unknown") for r in unresolved})}
        if mode in ("symbol", "context"):
            response["results"] = [_unit(index, seed, "selected_definition", details=True)]
            if unresolved or metadata["status"] == "PARTIAL":
                response["status"] = "PARTIAL"
            if mode == "context":
                allowed = min(limit, max_nodes - 1) if max_hops else 0
                edges = _edges(index, seed["symbol_id"], direction, relation, allowed + 1)
                if len(edges) > allowed:
                    response["omitted"].append({"reason": "neighbor_limit", "count": "AT_LEAST_ONE"})
                seen = {seed["symbol_id"]}
                for edge in edges[:allowed]:
                    neighbor = edge["to"] if edge["from"] == seed["symbol_id"] else edge["from"]
                    if neighbor not in seen:
                        response["results"].append({"node": _node(index, neighbor, "causal_neighbor"), "edge": edge})
                        seen.add(neighbor)
            _intent_context(index, [seed], response)
            if response["omitted"]:
                response["status"] = "PARTIAL"
            return _fit(response, max_bytes, mandatory=1)

        target_id = None
        if target is not None:
            targets, target_truncated = _select(index, target, limit)
            if len(targets) != 1 or target_truncated:
                response["status"] = "AMBIGUOUS" if targets else "UNKNOWN"
                response["results"] = [_unit(index, item, "target_candidate") for item in targets]
                return _fit(response, max_bytes)
            target_id = targets[0]["symbol_id"]
        hops = min(max_hops, 1) if mode in ("refs", "neighbors") else max_hops
        response["limits"]["effective_max_hops"] = hops
        nodes, edges, boundaries, found = _walk(index, seed["symbol_id"], target_id, direction, relation, max_nodes, hops,
                                               inspect_frontier=mode not in ("refs", "neighbors"))
        for boundary in boundaries:
            response["omitted"].append({"reason": boundary, "count": "UNKNOWN"})
        if target_id is not None:
            if found:
                response["results"] = [{"kind": "path", "nodes": [_node(index, item, "directed_path") for item in nodes],
                                        "edges": edges, "reason": "observed_static_path", "runtime_proof": False}]
            else:
                response["status"] = "UNKNOWN"
                response["absence"] = {"claim": "no path found within indexed relations and traversal limits", "universal": False}
        else:
            if mode == "explain":
                response["results"].append(_unit(index, seed, "selected_definition", details=True))
            for edge in edges[:limit]:
                response["results"].append({"kind": "relation", "edge": edge,
                                            "from_node": _node(index, edge["from"], "relation_source"),
                                            "to_node": _node(index, edge["to"], "relation_target"),
                                            "reason": "observed_indexed_relation", "runtime_proof": False})
            if len(edges) > limit:
                response["omitted"].append({"reason": "limit", "count": len(edges) - limit})
            if not edges:
                response["status"] = "HIT" if mode == "explain" else "UNKNOWN"
                response["absence"] = {"claim": "no selected indexed relations", "universal": False,
                                       "unresolved_elsewhere": "NOT_INSPECTED"}
        if metadata["status"] == "PARTIAL" or unresolved or response["omitted"]:
            if response["status"] == "HIT":
                response["status"] = "PARTIAL"
        mandatory = 1 if response["results"] and (target_id is not None or mode == "explain") else 0
        return _fit(response, max_bytes, mandatory=mandatory)


def architecture_witness(root: Path | str, source: str, target: str, *,
                         contract_id: str, adr_ref: str,
                         state_dir: Path | str | None = None, max_nodes: int = 100,
                         max_hops: int = 3, max_bytes: int = 8192) -> dict[str, Any]:
    """Explain a caller-forbidden indexed dependency, without policy authority.

    References are opaque caller assertions, never opened or authenticated.
    SUPPORTED means only a complete Python AST/resolved path in the published
    observation. It is not fresh filesystem evidence, runtime proof, or import
    cleanliness: the ADR-057 AST boundary oracle also covers unused imports.
    No result is PASS, a Permit, an execution block, or an automatic cut.
    Minimality is scoped to the outgoing static relation and complete bounded
    traversal evidence. Unsupported oracle paths are not searched around.

    Uses project_query's argument checks and serializer. If either the path or
    the ENTIRE decorated UTF-8 response cannot fit, raises ValueError rather
    than returning a truncated chain (including at the minimum 1024 budget).

@spec SPEC-SPECGRAPH-X4
    """
    for name, value in (("contract_id", contract_id), ("adr_ref", adr_ref)):
        if (not isinstance(value, str) or not value.strip()
                or len(value.encode("utf-8")) > 256
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError(f"{name} must be a nonempty string of at most 256 UTF-8 bytes without controls")
    RepositoryIndex._validate_query(target, 20)
    response = project_query(root, source, mode="path", target=target,
                             direction="out", relation="DEPENDS_ON_STATIC",
                             state_dir=state_dir, max_nodes=max_nodes,
                             max_hops=max_hops, max_bytes=max_bytes)
    supported = response["status"] == "HIT" and not response["omitted"]
    path = response["results"][0] if response["results"] else {}
    supported = supported and path.get("kind") == "path" and bool(path.get("edges"))
    reason = "incomplete_or_absent_path"
    if supported:
        # A second read is pinned and identity-checked: never mix publications.
        with RepositoryIndex(root, state_dir=state_dir) as index:
            metadata = index.metadata()
            supported = all(metadata[key] == response[key] for key in (
                "revision_seq", "content_hash")) and metadata["repository_root"] == response["root"]
            supported = supported and metadata.get("capabilities", {}).get("source_ranges") is True
            symbols, stored_lines = {}, {}
            for node in path["nodes"]:
                symbol = _symbol(index, node.get("symbol_id", ""))
                row = index.connection.execute(
                    "SELECT body, sha256 FROM sources WHERE file_path=?", (node.get("file_path"),)).fetchone()
                start, end = node.get("start_line"), node.get("end_line")
                valid = (symbol is not None and symbol.get("language") == "python"
                         and symbol.get("binding_status") == "resolved" and row is not None
                         and row[1] == node.get("source", {}).get("sha256")
                         and type(start) is int and type(end) is int
                         and 1 <= start <= end <= len(row[0].splitlines()))
                supported = supported and valid
                if valid:
                    symbols[node["symbol_id"]] = symbol
                    stored_lines[node["symbol_id"]] = row[0].splitlines()
            for offset, edge in enumerate(path["edges"]):
                owner = symbols.get(edge.get("from"), {})
                line, column = edge.get("source_line"), edge.get("source_column")
                valid = (edge.get("origin") == "python_ast" and edge.get("status") == "resolved"
                         and edge.get("type") == "DEPENDS_ON_STATIC"
                         and edge.get("from") == path["nodes"][offset].get("symbol_id")
                         and edge.get("to") == path["nodes"][offset + 1].get("symbol_id")
                         and type(line) is int and type(column) is int and column >= 0
                         and owner.get("start_line", 1) <= line <= owner.get("end_line", 0)
                         and edge.get("from") in stored_lines
                         and column < len(stored_lines[edge["from"]][line - 1].encode("utf-8"))
                         and any(ref.get("origin") == "python_ast" and ref.get("status") == "resolved"
                                 and ref.get("target") == edge.get("to") and ref.get("line") == line
                                 and ref.get("column") == column for ref in owner.get("references", [])))
                supported = supported and valid
            reason = "published_resolved_python_path" if supported else "missing_or_unsupported_provenance"
    response["architecture_witness"] = {
        "status": "SUPPORTED" if supported else "INCONCLUSIVE", "reason": reason,
        "contract_id": contract_id, "adr_ref": adr_ref,
        "references": "CALLER_SUPPLIED", "adr_read": False, "authority": "NONE",
        "rule": "caller forbids this indexed source-to-target dependency",
        "minimality": "outgoing_static_bfs_with_complete_traversal_evidence" if supported else "NOT_CLAIMED",
        "scope": "published observation, not current source or runtime behavior; used symbol dependencies only; "
                 "not import cleanliness and not a replacement for the ADR-057 AST boundary oracle",
    }
    if len(serialize_query(response).encode("utf-8")) > max_bytes:
        raise ValueError("Architecture witness including contract metadata exceeds max_bytes; increase the budget")
    return response
