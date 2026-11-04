"""Graphify's native code graph plus deterministic source hydration for the pilot.

No SpecGraph lookup or private oracle is used. Native query outputs remain in
custody; only their definition witnesses select source ranges for delivery.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from compare_graphify import delivered_nodes
from pilot_process import run_process
from pilot_workspace import relative_path

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def hydrate(root, witnesses, context_files, *, max_bytes, primary_found):
    root = Path(root).resolve()
    sources, trees, ranges, selected, rejected = {}, {}, {}, [], []
    def source(name):
        relative_path(name)
        target = root / name
        if not target.resolve().is_relative_to(root) or target.is_symlink():
            raise ValueError('Graphify source path escapes corpus')
        if name not in sources:
            raw = target.read_bytes()
            if len(raw) > 2 * 1024 * 1024:
                raise ValueError('Graphify source too large')
            sources[name] = raw
        return sources[name]
    for row in witnesses:
        name = row['file']
        if not name.endswith('.py'):
            rejected.append(row | {'reason': 'not_python_definition'}); continue
        raw = source(name)
        if name not in trees:
            trees[name] = ast.parse(raw)
        tree = trees[name]
        nodes = [n for n in ast.walk(tree) if isinstance(n, DEFINITIONS)
                 and n.name == row['name'].rsplit('.', 1)[-1]
                 and row['start'] in [n.lineno, *[d.lineno for d in n.decorator_list]]]
        if len(nodes) != 1:
            rejected.append(row | {'reason': 'not_exact_definition'}); continue
        n = nodes[0]
        selected.append((name, n, tree))
    # Same source ergonomics as the SpecGraph pilot: matched members supersede
    # their redundant class container; imports/constants/decorators stay visible.
    selected = [(name, n, tree) for name, n, tree in selected if not (
        isinstance(n, ast.ClassDef) and any(p == name and n.lineno < other.lineno <= other.end_lineno <= n.end_lineno
            for p, other, _ in selected))]
    for name, n, tree in selected:
        spans = [(min([n.lineno] + [d.lineno for d in n.decorator_list]), n.end_lineno)]
        spans.extend((x.lineno, x.end_lineno) for x in tree.body if not isinstance(x, DEFINITIONS))
        for parent in ast.walk(tree):
            if isinstance(parent, ast.ClassDef) and parent.lineno < n.lineno <= n.end_lineno <= parent.end_lineno:
                first = parent.body[0]
                first_line = min([first.lineno] + [d.lineno for d in getattr(first, 'decorator_list', [])])
                spans.append((min([parent.lineno] + [d.lineno for d in parent.decorator_list]), max(parent.lineno, first_line - 1)))
                spans.extend((x.lineno, x.end_lineno) for x in parent.body if not isinstance(x, DEFINITIONS))
        ranges.setdefault(name, []).extend(spans)
    fallback = not primary_found or not any(name in context_files for name in ranges)
    if fallback:
        for name in context_files:
            ranges[name] = [(1, len(source(name).splitlines()))]
    chunks, delivered = [], []
    for name in sorted(ranges):
        lines = source(name).decode().splitlines(keepends=True); merged = []
        for lo, hi in sorted(set(ranges[name])):
            if merged and (lo <= merged[-1][1] + 1 or not ''.join(lines[merged[-1][1]:lo-1]).strip()):
                merged[-1][1] = max(hi, merged[-1][1])
            else: merged.append([lo, hi])
        digest = hashlib.sha256(source(name)).hexdigest()
        for lo, hi in merged:
            body = ''.join(lines[lo-1:hi])
            chunks.append(f'File: {name}, lines {lo}-{hi}\nSHA256: {digest}\n```\n{body}\n```\n'.encode())
            delivered.append({'file_path': name, 'sha256': digest, 'start_line': lo, 'end_line': hi})
        if (root / name).read_bytes() != source(name):
            raise ValueError('Graphify source changed during hydration')
    packet = b'\n'.join(chunks)
    fallback_reason = 'retrieval_miss' if fallback else None
    attempted_packet_bytes = len(packet)
    verification_reads = len(ranges)
    if len(packet) > max_bytes and not fallback:
        # Match the SpecGraph arm's declared overflow policy: explicit full
        # public-file fallback, with all attempted retrieval/hydration charged.
        fallback, fallback_reason = True, 'packet_byte_limit'
        chunks, delivered = [], []
        for name in context_files:
            raw = source(name); digest = hashlib.sha256(raw).hexdigest(); hi = len(raw.splitlines())
            chunks.append(f'File: {name}, lines 1-{hi}\nSHA256: {digest}\n```\n'.encode() + raw + b'\n```\n')
            delivered.append({'file_path': name, 'sha256': digest, 'start_line': 1, 'end_line': hi})
        packet = b'\n'.join(chunks)
    if len(packet) > max_bytes:
        raise ValueError('Graphify delivery exceeds packet byte budget; no silent cut')
    for name, raw in sources.items():
        if (root / name).read_bytes() != raw:
            raise ValueError('Graphify source changed during hydration')
    verification_reads += len(sources)
    return packet, {'packet_source_reads': len(sources), 'packet_source_bytes': sum(map(len, sources.values())),
        'packet_bytes': len(packet), 'packet_sha256': hashlib.sha256(packet).hexdigest(),
        'fallback_deliveries': int(fallback), 'fallback_reason': fallback_reason,
        'attempted_packet_bytes': attempted_packet_bytes,
        'source_verification_reads': verification_reads, 'delivered': delivered, 'rejected_witnesses': rejected}


def select_context(root, state, description, context_files, *, success_criteria='', max_bytes=32768,
                   python_paths, revision, native_budget=2000, limit=5, code_root='src'):
    started = time.perf_counter(); root = Path(root).resolve(); state = Path(state).resolve()
    state.mkdir(exist_ok=False)
    env = os.environ.copy()
    for key in list(env):
        if key.endswith(('API_KEY', 'AUTH_TOKEN')) or key.startswith(('GRAPHIFY_', 'AWS_', 'ANTHROPIC_', 'OPENAI_')):
            env.pop(key, None)
    env.update(PYTHONPATH=os.pathsep.join(python_paths), PYTHONDONTWRITEBYTECODE='1',
               GRAPHIFY_NO_TIPS='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONHASHSEED='0')
    def invoke(args, name):
        argv = [sys.executable, '-B', '-m', 'graphify', *args]
        result = run_process(argv, cwd=root, dest=state/name, timeout=120, env=env)
        if result['exit_code'] != 0 or not result['cleanup_verified']:
            raise ValueError('Graphify CLI failed: ' + name)
        raw = (state/name/'stdout.txt').read_bytes()
        return raw.decode('utf-8'), {'argv': argv, 'process': result, 'stdout_sha256': hashlib.sha256(raw).hexdigest(), 'stdout_bytes': len(raw)}
    relative_path(code_root)
    _, build = invoke(['extract', str(root/code_root), '--code-only', '--no-cluster', '--max-workers', '1', '--out', str(state)], 'build')
    if 'dependency is missing' in (state/'build/stderr.txt').read_text(encoding='utf-8'):
        raise ValueError('Graphify extraction dependency unavailable; do not count as a retrieval miss')
    index_ms = (time.perf_counter()-started)*1000
    graph = state/'graphify-out/graph.json'
    if not graph.is_file(): raise ValueError('Graphify did not publish graph.json')
    references = list(dict.fromkeys(w for w in re.findall(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', success_criteria) if '.' in w or '_' in w))
    if len(references) > 16: raise ValueError('too many explicit criterion references')
    traces, witnesses = [], []; primary_found = False
    for i, query in enumerate([description, *references]):
        text, receipt = invoke(['query', query, '--budget', str(native_budget), '--graph', str(graph)], f'query-{i}')
        nodes = delivered_nodes('graphify', text)
        nodes = [dict(n, file=code_root + '/' + n['file']) for n in nodes]
        eligible = [n for n in nodes if i or n['file'] in context_files][:limit]
        if not i: primary_found = bool(eligible)
        witnesses.extend(eligible)
        traces.append({'query': query, 'native_output': text, 'selected_witnesses': eligible, **receipt})
    packet, metrics = hydrate(root, witnesses, context_files, max_bytes=max_bytes, primary_found=primary_found)
    elapsed = (time.perf_counter()-started)*1000
    metrics.update(policy='graphify-native-witness-hydration/1', revision=revision, index_builds=1,
        code_root=code_root, scope='same declared src code root as SpecGraph; no semantic docs or test-metadata pass',
        index_wall_ms=index_ms, query_wall_ms=elapsed-index_ms, context_total_wall_ms=elapsed,
        query_api_calls=len(traces), native_budget_estimated_tokens=native_budget, query_limit=limit,
        criteria_reference_queries=references, graph_sha256=hashlib.sha256(graph.read_bytes()).hexdigest(),
        graph_bytes=graph.stat().st_size, index_physical_source_reads=None, host_source_reads=None,
        dependency_completeness='NOT_CLAIMED', native_output_delivered_verbatim=False,
        delivery='Source hydration from native definition witnesses; native outputs retained externally')
    return packet, metrics, {'build': build, 'queries': traces}
