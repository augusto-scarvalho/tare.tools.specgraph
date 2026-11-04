"""Initial implementation context, with explicit retrieval and dependency boundaries.

This is the pilot's delivery policy over public SpecGraph APIs, not a claim of
complete runtime dependencies. The agent retains access to the whole checkout.
"""
from __future__ import annotations

import ast
from collections import deque
import hashlib
import re
from pathlib import Path, PurePosixPath
import time


def select_context(root, state, description, context_files, *, max_bytes=32768, limit=5, success_criteria=''):
    from specgraph.indexer import index_repository
    from specgraph.query import project_query
    from specgraph.identity import DEFINITIONS, python_identities
    from specgraph.storage import index_path

    root = Path(root).resolve()
    published_path = index_path(root, state_dir=state)
    if published_path.exists() or (state is not None and Path(state).exists()):
        raise ValueError('context index must start cold')
    if type(max_bytes) is not int or max_bytes < 1 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('positive packet budget and bounded candidate limit required')
    sources, trees, ranges, traces = {}, {}, {}, []
    stats = {'policy': 'implementation-initial-context/2', 'query_api_calls': 0,
        'published_index_path': str(published_path), 'canonical_host_state': state is None,
        'packet_source_reads': 0, 'packet_source_bytes': 0, 'index_builds': 1,
        'index_physical_source_reads': None, 'sql_statement_count': None, 'host_source_reads': None,
        'fallback_reason': None, 'fallback_reasons': [], 'coverage_notes': [],
        'dependency_completeness': 'NOT_CLAIMED', 'dependency_hops': 1,
        'followup_access': 'complete checkout through host tools'}

    def path(name):
        p = PurePosixPath(name)
        if not name or p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name or name != p.as_posix():
            raise ValueError('unsafe context source path')
        target = root / name
        if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in (target, *target.parents) if p != root.parent):
            raise ValueError('context source escapes repository or uses a link')
        return target

    if not context_files or len(context_files) != len(set(context_files)):
        raise ValueError('nonempty unique public context files required')
    for name in context_files:
        path(name)
    # Keep title retrieval intact. Explicit identifiers in public criteria get
    # their own lookup; concatenating prose can exceed the public 32-term limit
    # and can displace the implementation target in lexical ranking.
    references = list(dict.fromkeys(word for word in re.findall(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*', success_criteria)
                                   if '.' in word or '_' in word))
    if len(references) > 16:
        raise ValueError('success criteria exceed the 16 explicit-reference query budget')
    stats['criteria_sha256'] = hashlib.sha256(success_criteria.encode()).hexdigest()
    stats['criteria_reference_queries'] = references
    started = time.perf_counter()
    metadata = index_repository(root, state_dir=state)
    stats['index_wall_ms'] = (time.perf_counter() - started) * 1000
    query_started = time.perf_counter()

    def query(text, mode):
        stats['query_api_calls'] += 1
        response = project_query(root, text, mode=mode, state_dir=state, limit=limit,
            max_bytes=65536, direction='out', relation='DEPENDS_ON_STATIC', max_hops=1)
        if response['content_hash'] != metadata['content_hash']:
            raise ValueError('context snapshot changed during selection')
        traces.append(response)
        return response

    def source(name, expected=None):
        if name not in sources:
            body = path(name).read_bytes()
            sources[name] = (body.decode('utf-8'), hashlib.sha256(body).hexdigest())
            stats['packet_source_reads'] += 1
            stats['packet_source_bytes'] += len(body)
        text, actual = sources[name]
        if expected is not None and expected != actual:
            raise ValueError('source differs from published selection: ' + name)
        return text

    def fallback(name, reason):
        text = source(name)
        ranges[name] = [(1, len(text.splitlines()))]
        note = {'file_path': name, 'reason': reason}
        if note not in stats['fallback_reasons']:
            stats['fallback_reasons'].append(note)

    def include(unit):
        name = unit['file_path']
        proof = unit.get('source') or {}
        if not proof.get('sha256'):
            raise ValueError('published source hash required: ' + name)
        text = source(name, proof['sha256'])
        lo, hi = unit.get('start_line'), unit.get('end_line')
        if type(lo) is not int or type(hi) is not int or not 1 <= lo <= hi <= len(text.splitlines()):
            raise ValueError('invalid published source range: ' + name)
        spans = [(lo, hi)]
        if name.endswith('.py'):
            if name not in trees:
                tree = ast.parse(text)
                trees[name] = (tree, python_identities(tree, name))
            tree, identities = trees[name]
            node = next((node for node, ident in identities.items() if ident.symbol_id == unit['symbol_id']), None)
            if node is None or (node.lineno, node.end_lineno) != (lo, hi):
                raise ValueError('published definition no longer matches source: ' + name)
            if node.decorator_list:
                spans[0] = (min(d.lineno for d in node.decorator_list), hi)
            # Module statements retain imports, constants and conditional setup.
            spans.extend((n.lineno, n.end_lineno) for n in tree.body if not isinstance(n, DEFINITIONS))
            # A member excerpt also needs its lexical class header and class data.
            for parent in ast.walk(tree):
                if isinstance(parent, ast.ClassDef) and parent.lineno < lo <= hi <= parent.end_lineno:
                    first = parent.body[0]
                    first_line = min([first.lineno] + [d.lineno for d in getattr(first, 'decorator_list', [])])
                    header_start = min([parent.lineno] + [d.lineno for d in parent.decorator_list])
                    spans.append((header_start, max(parent.lineno, first_line - 1)))
                    spans.extend((n.lineno, n.end_lineno) for n in parent.body if not isinstance(n, DEFINITIONS))
        ranges.setdefault(name, []).extend(spans)

    searches = [query(text, 'search') for text in [description, *references]]
    primary = [r for r in searches[0]['results'] if r.get('symbol_id') and r.get('file_path') in context_files]
    referenced = [r for found in searches[1:] for r in found['results'] if r.get('symbol_id')]
    seeds = list({r['symbol_id']: r for r in [*primary, *referenced]}.values())
    broad = [s for s in seeds if s['kind'] == 'class' and any(
        child['file_path'] == s['file_path'] and s['start_line'] < child['start_line'] <= child['end_line'] <= s['end_line']
        for child in seeds)]
    for seed in broad:
        stats['coverage_notes'].append({'kind': 'matched_member_preferred_to_container', 'symbol_id': seed['symbol_id']})
    seeds = [s for s in seeds if s not in broad]
    # Ranking limits say other candidates exist, not that a selected definition
    # has lost a required source range. Preserve those limits as observations.
    for found in searches:
        if found.get('omitted'):
            stats['coverage_notes'].append({'kind': 'retrieval_boundary', 'omitted': found['omitted']})
    if not primary:
        for name in context_files:
            fallback(name, 'retrieval_miss')
    pending = deque((seed, True) for seed in seeds)
    inspected = set()
    context_query_limit = min(64, max(1, limit) * 4)
    stats['context_query_limit'] = context_query_limit
    while pending:
        seed, inspect_children = pending.popleft()
        if seed['symbol_id'] in inspected:
            continue
        if len(inspected) >= context_query_limit:
            source(seed['file_path'], seed.get('source', {}).get('sha256'))
            fallback(seed['file_path'], 'definition_inspection_limit')
            continue
        inspected.add(seed['symbol_id'])
        try:
            result = query(seed['symbol_id'], 'context')
        except ValueError as error:
            if 'max_bytes' not in str(error):
                raise
            fallback(seed['file_path'], 'definition_query_byte_limit')
            continue
        include(result['results'][0])
        if inspect_children and seed['kind'] == 'class' and seed['file_path'] in trees:
            # A class range already contains its methods. Their outgoing
            # dependencies must be inspected as well: the class symbol's own
            # references deliberately exclude method bodies in the AST index.
            _, identities = trees[seed['file_path']]
            for node, identity in identities.items():
                if seed['start_line'] < node.lineno <= node.end_lineno <= seed['end_line']:
                    pending.append(({'symbol_id': identity.symbol_id, 'file_path': seed['file_path'],
                        'kind': identity.kind, 'source': seed['source']}, False))
        for row in result['results'][1:]:
            edge = row.get('edge', {})
            node = row.get('node', {})
            if edge.get('type') == 'DEPENDS_ON_STATIC' and edge.get('from') == seed['symbol_id'] and node.get('symbol_id'):
                include(node)  # Read-only dependencies may be outside the edit/context manifest.
            else:
                stats['coverage_notes'].append({'kind': 'optional_graph_context',
                    'symbol_id': seed['symbol_id'], 'relation': edge.get('type'),
                    'context_kind': row.get('kind'), 'file_path': node.get('file_path')})
        unresolved = result.get('coverage', {}).get('unresolved_references', {})
        if unresolved.get('count'):
            stats['coverage_notes'].append({'kind': 'static_resolution_boundary',
                'symbol_id': seed['symbol_id'], **unresolved})
        for omitted in result.get('omitted', []):
            if omitted['reason'] in {'neighbor_limit', 'max_bytes', 'candidate_limit'}:
                fallback(seed['file_path'], 'outgoing_dependency_limit')
            else:
                stats['coverage_notes'].append({'kind': 'optional_context_boundary', **omitted})
    stats['query_wall_ms'] = (time.perf_counter() - query_started) * 1000

    def render():
        chunks, delivered = [], []
        for name in sorted(ranges):
            lines = source(name).splitlines(keepends=True)
            merged = []
            for lo, hi in sorted(ranges[name]):
                if merged and (lo <= merged[-1][1] + 1 or
                               not ''.join(lines[merged[-1][1]:lo-1]).strip()):
                    merged[-1][1] = max(hi, merged[-1][1])
                else:
                    merged.append([lo, hi])
            for lo, hi in merged:
                chunks.append(f'File: {name}, lines {lo}-{hi}\nSHA256: {sources[name][1]}\n```\n' + ''.join(lines[lo-1:hi]) + '\n```\n')
            delivered.append({'file_path': name, 'sha256': sources[name][1], 'ranges': merged,
                              'outside_context_manifest': name not in context_files})
        return '\n'.join(chunks).encode(), delivered

    packet, delivered = render()
    if len(packet) > max_bytes:
        ranges.clear()
        for name in context_files:
            fallback(name, 'packet_byte_limit')
        packet, delivered = render()
    if len(packet) > max_bytes:
        raise ValueError('full fallback exceeds packet byte budget; increase budget before inference')
    # Freshness covers every source used to choose or render the packet.
    for name, (_, expected) in sources.items():
        if hashlib.sha256(path(name).read_bytes()).hexdigest() != expected:
            raise ValueError('source changed during packet preparation: ' + name)
    stats['source_verification_reads'] = len(sources)
    reasons = stats['fallback_reasons']
    stats.update(fallback_reason=reasons[0]['reason'] if reasons else None,
        fallback_deliveries=int(bool(reasons)), fallback_file_count=len({r['file_path'] for r in reasons}),
        delivered_sources=delivered, context_total_wall_ms=(time.perf_counter()-started)*1000,
        packet_bytes=len(packet), packet_sha256=hashlib.sha256(packet).hexdigest(), snapshot=metadata['content_hash'])
    return packet, stats, traces
