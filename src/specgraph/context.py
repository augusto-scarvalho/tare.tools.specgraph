from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
import hashlib
import json
import math
import time

from .graph import CausalTraceabilityMatrix


def encode_context_output(text: str, max_bytes: int) -> bytes:
    """Enforce one UTF-8 budget for SDK text and complete CLI serialization."""
    if not 1024 <= max_bytes <= 65536:
        raise ValueError("max_bytes must be between 1024 and 65536")
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        raise ValueError("context exceeds UTF-8 byte budget; narrow the selection or increase --max-bytes")
    return raw


def _source_lines(root: Path, relative_path: str, *, keepends: bool = False) -> list[str]:
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("context source escapes the repository root")
    try:
        with path.open(encoding="utf-8", errors="strict", newline="") as source:
            return source.read().splitlines(keepends=keepends)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"context source unavailable or invalid UTF-8: {relative_path}") from exc


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _selection(matrix: CausalTraceabilityMatrix, spec_key: str) -> list[dict[str, Any]]:
    return sorted(
        [e for e in matrix.edges if e["to"] == spec_key
         and e["type"] in ("CODE_IMPLEMENTS_SPEC", "TEST_VERIFIES_SPEC")],
        key=_json,
    )


def _valid_handle(handle: Any, binding: dict[str, Any], now: float) -> bool:
    # Bound traversal before serialization, including hostile nested/cyclic JSON-like data.
    pending = [(handle, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > 2048 or depth > 4:
            return False
        if type(value) is dict:
            if len(value) > 128 or any(type(k) is not str or len(k) > 512 for k in value):
                return False
            pending.extend((v, depth + 1) for v in value.values())
        elif type(value) is list:
            if len(value) > 128:
                return False
            pending.extend((v, depth + 1) for v in value)
        elif type(value) is str:
            if len(value) > 4096:
                return False
        elif type(value) is int:
            if not -(2 ** 53) < value < 2 ** 53:
                return False
        elif type(value) not in (float, bool, type(None)):
            return False
    try:
        if len(_json(handle).encode("utf-8")) > 16384:
            return False
    except (ValueError, UnicodeError, OverflowError):
        return False
    keys = {"version", "root", "work_id", "spec_id", "generation", "created", "expires", "units", "snapshot"}
    if type(handle) is not dict or set(handle) != keys or type(handle["version"]) is not int or handle["version"] != 1:
        return False
    if any(type(handle[k]) is not str or handle[k] != binding[k] for k in ("root", "work_id", "spec_id")):
        return False
    for key in ("created", "expires"):
        if type(handle[key]) not in (int, float) or not math.isfinite(handle[key]):
            return False
    if not handle["created"] <= now < handle["expires"] or not 0 < handle["expires"] - handle["created"] <= 3600:
        return False
    def is_hash(v: Any) -> bool:
        return type(v) is str and len(v) == 64 and all(c in "0123456789abcdef" for c in v)
    if not is_hash(handle["generation"]) or not is_hash(handle["snapshot"]):
        return False
    units = handle["units"]
    if type(units) is not dict or not 1 <= len(units) <= 128:
        return False
    if any(not k or len(k) > 512 or not is_hash(v) for k, v in units.items()):
        return False
    return handle["snapshot"] == _digest({k: v for k, v in handle.items() if k != "snapshot"})


class ContextPackager:
    """Generates minimal, token-efficient Context Envelopes for AI Agents."""

    @classmethod
    def generate_spec_snapshot(
        cls, spec_id: str, root_dir: Path | str = ".", *, work_id: str,
        max_bytes: int = 65536, ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, Any]:
        """OPTIONAL X3: fresh complete structured context, with no cache writes.

        Wire encoding is UTF-8 JSON, sorted keys, ensure_ascii=False, compact
        separators, no NaN. The ENTIRE packet obeys max_bytes (1024..65536).
        Units are indivisible; oversize context raises ValueError, never truncates.
        Handles contain only hashes/metadata (16 KiB, 128 units, TTL <=1 hour).
        They are untrusted cache hints, not authenticity or authorization proof.
        No runtime integration, persisted history, or index-computation savings.
        """
        encode_context_output("", max_bytes)
        if type(work_id) is not str or not work_id.strip() or len(work_id) > 256:
            raise ValueError("work_id must be a nonempty string of at most 256 characters")
        if type(spec_id) is not str or not spec_id.strip() or len(spec_id) > 256:
            raise ValueError("spec_id must be a nonempty string of at most 256 characters")
        now = clock()
        if type(now) not in (int, float) or not -(2 ** 53) < now < 2 ** 53 or not math.isfinite(now):
            raise ValueError("clock must return a finite timestamp")
        if type(ttl_seconds) not in (int, float) or not 0 < ttl_seconds <= 3600 or not math.isfinite(ttl_seconds):
            raise ValueError("ttl_seconds must be positive and at most 3600")
        root = Path(root_dir).resolve()
        matrix = CausalTraceabilityMatrix.build(root, auto_cache=False)
        key = spec_id.upper()
        if key not in matrix.specs:
            raise ValueError(f"Specification '{spec_id}' not found in SpecGraph")
        # A partial parse is not a complete context oracle.
        if matrix.parse_errors or matrix.duplicate_warnings:
            raise ValueError("cannot snapshot incomplete or ambiguous graph")
        spec = matrix.specs[key]
        edges = _selection(matrix, key)
        units: list[dict[str, Any]] = []
        def add(identity: str, **data: Any) -> None:
            unit = {"id": identity, **data}
            units.append({**unit, "digest": _digest(unit)})
        spec_lines = _source_lines(root, spec.file_path, keepends=True)
        add("spec:" + key, kind="spec", file=spec.file_path,
            start_line=1, end_line=len(spec_lines), content="".join(spec_lines),
            title=spec.title, status=spec.status, implements=spec.implements,
            acceptance_criteria=spec.acceptance_criteria, intent=spec.body,
            links=edges)
        for kind, edge_type, collection in (
            ("code", "CODE_IMPLEMENTS_SPEC", matrix.symbols),
            ("test", "TEST_VERIFIES_SPEC", matrix.tests),
        ):
            for identity in sorted({e["from"] for e in edges if e["type"] == edge_type}):
                links = [e for e in edges if e["type"] == edge_type and e["from"] == identity]
                symbol = collection.get(identity)
                if symbol is None:
                    add(kind + ":" + identity, kind=kind, absent=True, links=links)
                    continue
                lines = _source_lines(root, symbol.file_path, keepends=True)
                if not 1 <= symbol.start_line <= symbol.end_line <= len(lines):
                    raise ValueError("invalid context source range")
                add(kind + ":" + identity, kind=kind, absent=False, links=links,
                    file=symbol.file_path, name=symbol.name,
                    start_line=symbol.start_line, end_line=symbol.end_line,
                    content="".join(lines[symbol.start_line - 1:symbol.end_line]))
        handle = {"version": 1, "root": str(root), "work_id": work_id, "spec_id": key,
                  "generation": matrix.canonical_digest(), "created": now, "expires": now + ttl_seconds,
                  "units": {u["id"]: u["digest"] for u in units}}
        handle["snapshot"] = _digest(handle)
        metadata = {k: v for k, v in handle.items() if k != "units"}
        bounded = _valid_handle(handle, metadata, now)
        packet = {"kind": "full" if bounded else "refresh", "reason": None if bounded else "handle_limit",
                  "snapshot": metadata, "units": units, "handle": handle if bounded else None}
        encode_context_output(_json(packet), max_bytes)
        return packet

    @classmethod
    def generate_spec_delta(
        cls, spec_id: str, handle: Any, root_dir: Path | str = ".", *, work_id: str,
        max_bytes: int = 65536, ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.time,
    ) -> dict[str, Any]:
        """Recompute FULL, then transmit a smaller delta or explicit FULL refresh.

        Only a consumer holding the exact prior snapshot may apply a delta:
        check base_snapshot, remove every removed id, replace complete upserts,
        order by unit_order, then replace snapshot and handle. This reconstructs
        the fresh full packet (kind='full', reason=None) exactly. Never mutate a
        cached prompt prefix; append only to explicitly matching consumers.
        Invalid/expired hints and non-smaller deltas return complete refreshes.
        Comparison covers entire deterministic wire envelopes, including handles.
        At an exact byte boundary, refresh metadata may displace the optional
        next handle, never a context unit; the caller must request full again.
        """
        full = cls.generate_spec_snapshot(spec_id, root_dir, work_id=work_id,
            max_bytes=max_bytes, ttl_seconds=ttl_seconds, clock=clock)
        if full["handle"] is None:
            return full
        if not _valid_handle(handle, full["snapshot"], full["snapshot"]["created"]):
            reason = "invalid_handle"
        else:
            current = full["handle"]["units"]
            delta = {"kind": "delta", "base_snapshot": handle["snapshot"],
                     "snapshot": full["snapshot"], "handle": full["handle"],
                     "unit_order": [u["id"] for u in full["units"]],
                     "upserts": [u for u in full["units"] if handle["units"].get(u["id"]) != u["digest"]],
                     "removed": sorted(set(handle["units"]) - set(current))}
            if len(_json(delta).encode("utf-8")) < len(_json(full).encode("utf-8")):
                encode_context_output(_json(delta), max_bytes)
                return delta
            reason = "delta_not_smaller"
        full.update(kind="refresh", reason=reason)
        if len(_json(full).encode("utf-8")) > max_bytes:
            # A refresh adds a reason beyond the already-bounded full packet.
            # The cache hint is optional; obligations and provenance are not.
            full["handle"] = None
        encode_context_output(_json(full), max_bytes)
        return full

    @classmethod
    def generate_spec_context(
        cls,
        matrix: CausalTraceabilityMatrix,
        spec_id: str,
        root_dir: Path | str = ".",
        *,
        max_bytes: int = 8192,
    ) -> str:
        """Produce a complete, self-contained Context Envelope for a given Spec."""
        encode_context_output("", max_bytes)
        root = Path(root_dir).resolve()
        if root != matrix.root_dir.resolve():
            raise ValueError("context root differs from the graph root")
        spec_key = spec_id.upper()
        if spec_key not in matrix.specs:
            raise ValueError(f"Specification '{spec_id}' not found in SpecGraph")

        spec = matrix.specs[spec_key]

        # Find linked code symbols
        code_sym_ids = {e["from"] for e in _selection(matrix, spec_key) if e["type"] == "CODE_IMPLEMENTS_SPEC"}
        linked_symbols = [matrix.symbols[s_id] for s_id in sorted(code_sym_ids) if s_id in matrix.symbols]

        # Find linked tests
        test_links: dict[str, set[str]] = {}
        for edge in matrix.edges:
            if edge["to"] == spec_key and edge["type"] == "TEST_VERIFIES_SPEC":
                test_links.setdefault(edge["from"], set()).add(edge.get("verified_criterion") or spec_key)
        linked_tests = [matrix.tests[t_id] for t_id in sorted(test_links) if t_id in matrix.tests]

        # Assemble markdown envelope
        lines: list[str] = []
        lines.append(f"# Context Envelope: [{spec.id}] {spec.title}")
        lines.append(f"**Status:** `{spec.status}` | **File:** `{spec.file_path}`")
        lines.append(f"**Root Directory:** `{root}`")
        lines.append(f"**Generation Digest:** `{matrix.canonical_digest()}`")
        if spec.implements:
            lines.append(f"**Implements Requirements:** {', '.join(spec.implements)}")
        lines.append("")

        # Acceptance Criteria
        if spec.acceptance_criteria:
            lines.append("## Acceptance Criteria")
            for ac_id, desc in sorted(spec.acceptance_criteria.items()):
                lines.append(f"- **`{ac_id}`**: {desc}")
            lines.append("")

        # Spec Body / Intent
        if spec.body:
            lines.append("## Specification Intent & Details")
            lines.append(spec.body)
            lines.append("")

        # Linked AST Code Implementations
        lines.append("## Linked Code Implementations")
        if linked_symbols:
            # Group by file
            files_map: dict[str, list[Any]] = {}
            for sym in linked_symbols:
                files_map.setdefault(sym.file_path, []).append(sym)

            for file_path, syms in files_map.items():
                lines.append(f"### File: `{file_path}`")
                file_lines = _source_lines(root, file_path)
                for s in syms:
                    lines.append(f"#### `{s.kind} {s.name}` (Lines {s.start_line}-{s.end_line})")
                    code_snippet = "\n".join(file_lines[s.start_line - 1 : s.end_line])
                    lines.append("```python")
                    lines.append(code_snippet)
                    lines.append("```")
                lines.append("")
        else:
            lines.append("*No code implementations linked yet.*")
            lines.append("")

        # Linked Verifying Tests
        lines.append("## Verifying Test Suites")
        if linked_tests:
            test_files_map: dict[str, list[Any]] = {}
            for t in linked_tests:
                test_files_map.setdefault(t.file_path, []).append(t)

            for t_path, t_syms in test_files_map.items():
                lines.append(f"### Test File: `{t_path}`")
                t_lines = _source_lines(root, t_path)
                for t in t_syms:
                    lines.append(f"#### `{t.name}` (Lines {t.start_line}-{t.end_line})")
                    lines.append("Declared verification links: " + ", ".join(f"`{ref}`" for ref in sorted(test_links[t.test_id])))
                    code_snippet = "\n".join(t_lines[t.start_line - 1 : t.end_line])
                    lines.append("```python")
                    lines.append(code_snippet)
                    lines.append("```")
                lines.append("")
        else:
            lines.append("*No verifying tests linked yet.*")
            lines.append("")

        context = "\n".join(lines)
        encode_context_output(context, max_bytes)
        return context


    @classmethod
    def generate_reviewer_bundle(
        cls,
        matrix: CausalTraceabilityMatrix,
        files: list[str] | None = None,
        spec_id: str | None = None,
        root_dir: Path | str = ".",
        *,
        max_bytes: int = 8192,
    ) -> str:
        """Return a complete UTF-8-bounded bundle or refuse; never truncate intent."""
        encode_context_output("", max_bytes)
        if spec_id is not None and files is not None:
            raise ValueError("select either spec_id or files, not both")
        root = Path(root_dir).resolve()
        if root != matrix.root_dir.resolve():
            raise ValueError("context root differs from the graph root")
        lines: list[str] = []
        
        target_specs: list[str] = []
        if spec_id is not None:
            spec_key = spec_id.upper()
            if spec_key not in matrix.specs:
                raise ValueError(f"Specification '{spec_id}' not found in SpecGraph")
            target_specs.append(spec_key)
        elif files:
            norm_files = set()
            for name in files:
                try:
                    norm_files.add((root / name.replace("\\", "/")).resolve().relative_to(root).as_posix())
                except ValueError as exc:
                    raise ValueError("bundle path escapes the repository root") from exc
            for sym_id, sym in matrix.symbols.items():
                if sym.file_path in norm_files:
                    for edge in matrix.edges:
                        if edge["from"] == sym_id and edge["type"] == "CODE_IMPLEMENTS_SPEC":
                            if edge["to"] not in target_specs:
                                target_specs.append(edge["to"])
        target_specs.sort()

        lines.append("# 📦 REVIEWER CONTEXT BUNDLE (SpecGraph ADR-022)")
        lines.append(f"**Target Specs:** {', '.join(target_specs) if target_specs else 'General Diff'}")
        lines.append(f"**Root Directory:** `{root}`")
        lines.append(f"**Generation Digest:** `{matrix.canonical_digest()}`")
        lines.append("")

        # 1. Governing Requirements & Acceptance Criteria
        lines.append("## 1. Governing Acceptance Criteria (EARS)")
        total_acs = 0
        for s_key in target_specs:
            if s_key in matrix.specs:
                sp = matrix.specs[s_key]
                lines.append(f"### [{sp.id}] {sp.title}")
                for ac_id, ac_text in sorted(sp.acceptance_criteria.items()):
                    lines.append(f"- **`{ac_id}`**: {ac_text}")
                    total_acs += 1
        if total_acs == 0:
            lines.append("- *No explicit governing specs mapped for these files.*")
        lines.append("")

        # 2. Exact Touched Code AST Ranges
        lines.append("## 2. Relevant Code Implementations (AST Ranges)")
        linked_symbols = {}
        for s_key in target_specs:
            code_sym_ids = [e["from"] for e in matrix.edges if e["to"] == s_key and e["type"] == "CODE_IMPLEMENTS_SPEC"]
            for s_id in code_sym_ids:
                if s_id in matrix.symbols:
                    linked_symbols[s_id] = matrix.symbols[s_id]

        if linked_symbols:
            files_map: dict[str, list[Any]] = {}
            for sym in sorted(linked_symbols.values(), key=lambda s: (s.file_path, s.start_line, s.symbol_id)):
                files_map.setdefault(sym.file_path, []).append(sym)

            for file_path, syms in files_map.items():
                lines.append(f"### File: `{file_path}`")
                file_lines = _source_lines(root, file_path)
                for s in syms:
                    lines.append(f"#### `{s.kind} {s.name}` (Lines {s.start_line}-{s.end_line})")
                    code_snippet = "\n".join(file_lines[s.start_line - 1 : s.end_line])
                    lines.append("```python")
                    lines.append(code_snippet)
                    lines.append("```")
        else:
            lines.append("- *No code symbols mapped.*")
        lines.append("")

        # 3. Verifying Test Suites
        lines.append("## 3. Verifying Test Suites (@pytest.mark.verifies)")
        linked_tests = {
            (edge["from"], edge.get("verified_criterion") or edge["to"])
            for edge in matrix.edges
            if edge["type"] == "TEST_VERIFIES_SPEC"
            and edge["to"] in target_specs and edge["from"] in matrix.tests
        }

        if linked_tests:
            for test_id, verified_spec in sorted(linked_tests):
                t = matrix.tests[test_id]
                lines.append(f"- **`{t.name}`** (`{t.file_path}:{t.start_line}`) -> verifies `{verified_spec}`")
        else:
            lines.append("- *No verifying test suites mapped.*")
        lines.append("")

        raw_bundle = "\n".join(lines)
        approx_tokens = int(len(raw_bundle) / 3.5)
        lines.append(f"<!-- token_budget: approx ~{approx_tokens} tokens | frugal_bundle_v1 -->")
        bundle = "\n".join(lines)
        encode_context_output(bundle, max_bytes)
        return bundle
