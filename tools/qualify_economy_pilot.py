"""Twelve bounded Aider calls on three new defects; focused indexing is charged per cell.

Preparation and the native offline probe work without the provider. A separate
append-only binding records a healthy provider before any inference is allowed.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time

import qualify_native_host as native
from economy_oracles import TASKS, SELECTIONS
from economy_controls import variants

SCHEMA = 'tare.tools/economy-pilot/1'
ORDER = [(n, task, arm) for n in (1, 2) for i, task in enumerate(TASKS)
         for arm in (native.ARMS if (n + i) % 2 else tuple(reversed(native.ARMS)))]
PROMPTS = {
    TASKS[0]: 'Repair SpecGraphConfig.load: empty/null YAML must retain defaults, but every non-mapping YAML root including empty lists, false, zero and empty strings must record config_errors with strict_orphan_check=true. Preserve mapping values, explicit empty path lists, strict=false, field diagnostics, absent defaults, alternate .specgraph.yaml and primary file precedence. Do not modify input files or change public return shapes.',
    TASKS[1]: 'Repair TestParser.discover_tests: excluded_patterns must use the same path semantics as SpecGraphConfig.is_path_excluded: normalize slash and backslash separators and boundary slashes, match complete relative paths or their descendants, and match single path components anywhere. Ignore empty rules; do not match substrings such as generated_backup for generated. Apply exclusions before parsing, including explicitly selected files. Preserve root containment, selection order, parsing errors and all test metadata. Do not change duplicate-discovery policy.',
    TASKS[2]: 'Repair report_html: max_nodes, max_edges, max_hops and max_bytes must each be a built-in int (reject bool and every other type with ValueError before opening RepositoryIndex). Preserve inclusive ranges 1..1000, 1..5000, 0..10 and 8192..10000000 respectively, existing valid exports, identity, source snippets, relations and byte budgets. Do not change UI, truncation policy or signatures.',
}
save, read, sha, wire = native.save, native.read, native.sha, native.wire
_packet, _frozen = native.packet, native.frozen


def direct_packet(task, root, edit_root):
    source = (Path(root) / task['path']).read_bytes()
    if sha(source) != task['source_sha256']:
        raise ValueError('source changed')
    header = {'authority': 'NONE', 'work_id': native.WORK, 'source_root': str(root),
        'edit_root': str(edit_root), 'source_ref': task['repo'] + ':' + task['base'],
        'snapshot': None, 'file': task['path'], 'sha256': task['source_sha256'],
        'lines': [1, len(source.splitlines())], 'scope': 'new task; full source read directly; no index'}
    result = wire(header) + b'```python\n' + source + b'\n```\n'
    if len(result) > 32768:
        raise ValueError('context byte budget')
    return result


def emit(directory, identity, arm, dest):
    p = frozen(directory)
    sys.path.insert(0, p['engine'])
    row = next(t for t in p['tasks'] if t['id'] == identity)
    task, started = read(row['task']), time.perf_counter()
    index_ms = 0.0
    if arm == 'full':
        body = direct_packet(task, row['root'], dest / 'workspace')
    else:
        from specgraph.indexer import index_repository
        state = dest / 'context-state'
        if state.exists():
            raise ValueError('focused trial requires an absent index')
        metadata = index_repository(row['root'], state_dir=state)
        index_ms = (time.perf_counter() - started) * 1000
        task = task | {'snapshot': metadata['content_hash']}
        header, code = _packet(task, row['root'], state, arm, dest / 'workspace').split(b'\n', 1)
        header = json.loads(header)
        header['scope'] = 'new task; investigator-selected exact method; omitted dependencies not verified'
        body = wire(header) + code
    elapsed = (time.perf_counter() - started) * 1000
    (dest / 'packet.txt').write_bytes(body)
    save(dest / 'emission.json', {'bytes': len(body), 'sha256': sha(body),
        'query_wall_ms': elapsed, 'index_wall_ms': index_ms, 'lookup_wall_ms': elapsed - index_ms,
        'index_builds': int(arm == 'focused'), 'selection_queries': int(arm == 'focused'),
        'bridge': 'NATIVE_AIDER_RUN_ON_DEMAND'})
    sys.stdout.buffer.write(body)


def frozen(directory):
    protocol = _frozen(directory)
    if protocol['schema'] != SCHEMA:
        raise ValueError('unexpected pilot protocol')
    binding = directory / 'provider-binding.json'
    if binding.exists():
        value = read(binding)
        if value['protocol_sha256'] != sha(wire(protocol)):
            raise ValueError('provider binding belongs to another protocol')
        protocol = protocol | {'provider': value['provider']}
    return protocol


def decide(records):
    actual = [(r['round'], r['task'], r['arm']) for r in records]
    complete = len(actual) == len(ORDER) and set(actual) == set(ORDER)
    stable = complete and all(not r.get(k) for r in records for k in
        ('offline', 'provider_changed', 'global_analytics_changed', 'extra_requests', 'interventions'))
    pairs = []
    for n in (1, 2):
        for task in TASKS:
            pair = {'round': n, 'task': task}
            for arm in native.ARMS:
                selected = [r for r in records if (r['round'], r['task'], r['arm']) == (n, task, arm)]
                row = selected[0] if len(selected) == 1 else None
                calls = row.get('calls', []) if row else []
                usage = calls[0].get('usage') if len(calls) == 1 else None
                pair[arm] = {'success': bool(row and row['success']), 'usage': usage,
                             'whole_wall_ms': row.get('whole_wall_ms') if row else None}
            a, b = pair['full'], pair['focused']
            pair['token_gain'] = bool(stable and a['success'] and b['success'] and a['usage'] and b['usage']
                                      and b['usage']['total_tokens'] < a['usage']['total_tokens'])
            pair['time_gain'] = bool(pair['token_gain'] and b['whole_wall_ms'] <= a['whole_wall_ms'])
            pairs.append(pair)
    return {'status': 'LOCAL_PILOT_PASSED' if stable and all(r['success'] for r in records)
            else 'REJECT_PILOT' if complete else 'INCOMPLETE',
            'token_benefit': 'OBSERVED_IN_ALL_SIX_PAIRS' if all(p['token_gain'] for p in pairs) else 'NOT_DEMONSTRATED',
            'time_benefit': 'OBSERVED_IN_ALL_SIX_PAIRS' if all(p['time_gain'] for p in pairs) else 'NOT_DEMONSTRATED',
            'pairs': pairs, 'automatic_roles': [], 'sustained_benefit': False,
            'host_work_complete': False, 'investigator_tokens': None,
            'scope': 'three independent tasks repeated twice, exact selection, cold focused index included'}


def prepare(args):
    started = time.perf_counter()
    engine = Path(__file__).resolve().parents[1]
    if len(args.source_commit or '') != 40 or len(args.implementation_commit or '') != 40:
        raise ValueError('full source and implementation commits required')
    head = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD']).decode().strip()
    if head != args.implementation_commit or subprocess.check_output(
            ['git', '-C', str(engine), 'status', '--porcelain', '--untracked-files=normal']):
        raise ValueError('prepare requires a clean, committed implementation checkout')
    sys.path.insert(0, str(engine / 'src'))
    import specgraph.indexer
    if Path(specgraph.indexer.__file__).resolve().parent.parent != engine / 'src':
        raise ValueError('wrong indexer interpreter binding')
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    files, tasks = {}, []
    for identity in TASKS:
        relative, symbol = SELECTIONS[identity]
        source = subprocess.check_output(['git', '-C', str(engine), 'show', args.source_commit + ':' + relative])
        node = ast.parse(source)
        for part in symbol.split('.'):
            node = next(n for n in node.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == part)
        dest = out / 'prepared' / identity
        target = dest / 'corpus' / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        cfg = dest / 'corpus/specgraph.yaml'
        cfg.write_bytes(b'project_name: tare.tools.specgraph\nindexed_paths: [src]\ngoverned_paths: []\n')
        task = {'id': identity, 'repo': 'tare.tools.specgraph', 'base': args.source_commit,
                'path': relative, 'source_sha256': sha(source), 'symbol': symbol,
                'target_range': [node.lineno, node.end_lineno],
                'prompt': PROMPTS[identity]}
        oracle = dest / 'oracle.py'
        oracle.write_bytes(('import sys\nsys.path[:0] = ' + repr([str(engine / 'src'), str(engine / 'tools')])
            + '\nfrom economy_oracles import main\nraise SystemExit(main(' + repr(identity) + ', sys.argv[1]))\n').encode())
        task['oracle_sha256'] = sha(oracle.read_bytes())
        save(dest / 'task.json', task)
        check = subprocess.run([sys.executable, '-B', str(oracle), str(target)], capture_output=True, timeout=10)
        (dest / 'baseline.stdout').write_bytes(check.stdout)
        (dest / 'baseline.stderr').write_bytes(check.stderr)
        baseline = json.loads(check.stdout)
        failed = [r for r in baseline['checks'] if not r['passed']]
        if check.returncode != 1 or not failed or any(r['kind'] != 'regression' for r in failed):
            raise ValueError('baseline must fail new requirements and pass all preservations')
        controls = {}
        for label, body in variants(source.decode(), identity).items():
            candidate = dest / (label + '.py')
            candidate.write_bytes(body.encode())
            result = subprocess.run([sys.executable, '-B', str(oracle), str(candidate)], capture_output=True, timeout=10)
            report = json.loads(result.stdout)
            if report['passed'] != (label == 'reference'):
                raise ValueError('oracle control failed: ' + identity + ':' + label)
            controls[label] = report
            files[str(candidate)] = sha(candidate.read_bytes())
        tasks.append({'id': identity, 'task': str(dest / 'task.json'), 'root': str(dest / 'corpus'),
            'state': str(dest / 'unused-state'), 'source': str(target), 'oracle': str(oracle),
            'baseline': baseline, 'controls': controls})
        for path in (target, cfg, oracle, dest / 'task.json'):
            files[str(path)] = sha(path.read_bytes())
    import aider.coders.base_coder, aider.coders.editblock_coder, aider.commands, aider.io, aider.args
    paths = list((engine / 'src').rglob('*.py')) + [engine / 'tools' / name for name in (
        'qualify_economy_pilot.py', 'economy_oracles.py', 'economy_controls.py', 'qualify_native_host.py',
        'qualify_context_selection.py', 'aider_headless.py', 'response_contract.py', 'native_edit_contract.py')]
    paths += [args.contract.resolve(), Path(aider.coders.base_coder.__file__),
              Path(aider.coders.editblock_coder.__file__), Path(aider.commands.__file__),
              Path(aider.io.__file__), Path(aider.args.__file__)]
    for path in paths:
        files[str(path.resolve())] = sha(path.read_bytes())
    protocol = {'schema': SCHEMA, 'work_id': native.WORK, 'authority': 'NONE',
        'source_commit': args.source_commit, 'implementation_commit': args.implementation_commit,
        'tasks': tasks, 'files': files, 'engine': str(engine / 'src'), 'runner': str(Path(__file__).resolve()),
        'headless': str(engine / 'tools/aider_headless.py'), 'provider': None,
        'aider_version': importlib.metadata.version('aider-chat'),
        'litellm_version': importlib.metadata.version('litellm'), 'python': sys.version,
        'analytics_before': native.base.analytics(), 'order': ORDER, 'automatic_roles': [],
        'budgets': {'real_calls': 12, 'calls_per_trial': 1, 'output_tokens': 2048, 'retry_budget': 0,
                    'packet_bytes': 32768, 'query_ms': 1000, 'request_bytes': 131072,
                    'response_bytes': 1048576, 'trial_seconds': 150},
        'preparation_wall_ms': (time.perf_counter() - started) * 1000,
        'scope': 'Full-file versus exact-function snapshot context; discovery and dependencies not evaluated',
        'cost_limits': ['cold index built in every focused cell; no index for full baseline; common export preparation separate',
                        'investigator tokens and total engineering time unavailable',
                        'offline fixtures never counted as inference']}
    save(out / 'protocol.json', protocol)
    save(out / 'protocol-lock.json', {'sha256': sha(wire(protocol))})
    return {'status': 'PREPARED_NO_INFERENCE', 'preparation_wall_ms': protocol['preparation_wall_ms']}


def run(out):
    protocol = frozen(out)
    if protocol['provider'] is None or protocol['order'] != [list(x) for x in ORDER]:
        raise ValueError('healthy provider binding and frozen order required')
    probe = read(out / 'offline-preflight/receipt.json')
    if not probe['success'] or probe['extra_requests'] or probe['global_analytics_changed']:
        raise ValueError('offline native preflight required')
    with (out / 'run-reserved.json').open('x', encoding='utf-8') as stream:
        json.dump({'max_calls': 12, 'order': ORDER}, stream)
    start, records = time.perf_counter(), []
    for number, task, arm in ORDER:
        before = time.perf_counter()
        row = native.trial(out, task, arm, number)
        row['whole_wall_ms'] = (time.perf_counter() - before) * 1000
        if row['whole_wall_ms'] > 150000:
            row.update(success=False, whole_budget_exceeded=True)
        save(out / f'r{number}-{task}-{arm}/receipt.json', row)
        records.append(row)
        save(out / 'results.json', {'records': records, 'decision': decide(records)})
        print(json.dumps({k: row[k] for k in ('round', 'task', 'arm', 'success', 'whole_wall_ms')}), flush=True)
        if row.get('provider_changed') or row['global_analytics_changed']:
            break
    decision = decide(records)
    save(out / 'decision.json', decision)
    save(out / 'cost.json', {'preparation_wall_ms': protocol['preparation_wall_ms'],
        'offline_preflight_wall_ms': probe['whole_wall_ms'],
        'study_wall_ms': (time.perf_counter() - start) * 1000,
        'trial_whole_wall_ms': sum(r['whole_wall_ms'] for r in records),
        'investigator_tokens': None, 'investigator_time': None,
        'provider_wait_time': 'EXCLUDED; not model execution', 'automatic_roles': []})
    return decision


def main():
    native.frozen = frozen
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'emit', 'probe', 'bind', 'run'])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--contract', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--implementation-commit')
    parser.add_argument('--task', choices=TASKS)
    parser.add_argument('--arm', choices=native.ARMS)
    parser.add_argument('--trial-dir', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.action == 'prepare':
        result = prepare(args)
    elif args.action == 'emit':
        emit(out, args.task, args.arm, args.trial_dir.resolve())
        return
    elif args.action == 'bind':
        protocol = _frozen(out)
        if protocol['schema'] != SCHEMA:
            raise ValueError('unexpected protocol')
        result = {'protocol_sha256': sha(wire(protocol)), 'provider': native.base.provider()}
        with (out / 'provider-binding.json').open('xb') as stream:
            stream.write(wire(result))
    elif args.action == 'probe':
        before = time.perf_counter()
        result = native.trial(out, TASKS[0], 'focused', 0, offline=True)
        result['whole_wall_ms'] = (time.perf_counter() - before) * 1000
        save(out / 'offline-preflight/receipt.json', result)
    else:
        if not args.execute:
            parser.error('run requires --execute')
        result = run(out)
    sys.stdout.buffer.write(wire(result))


if __name__ == '__main__':
    main()
