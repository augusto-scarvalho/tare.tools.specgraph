"""Four pre-registered native Aider executions on unresolved hook defects."""
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
from host_pilot_oracles import TASKS

SCHEMA = 'tare.tools/new-task-pilot/1'
save, read, sha, wire = native.save, native.read, native.sha, native.wire
PROMPTS = {
    TASKS[0]: 'Repair context_hint dirty path identity. Caller dirty_paths must be normalized to root-relative POSIX paths exactly like files before merging with the lifecycle pending paths. Absolute, relative, dot/parent components and duplicates naming the same file must produce identical dirty_paths and digest, including with no files selection. Outside-root dirty paths must return the bounded OUTSIDE_SCOPE report. Preserve budgets, snapshot-only queries, notification silence and all other behavior.',
    TASKS[1]: 'Repair context_hint explicit symbol lookup. When an explicit symbol_id is absent from the published index, return a bounded MISS even if a valid spec_id or files selection was also supplied. A valid specification must not turn a failed explicit symbol lookup into HIT. Preserve valid symbol/spec hits, spec-only intent, missing-spec MISS, mismatched-file OUTSIDE_SCOPE, budgets and snapshot-only queries.',
}
ORDER = [(TASKS[0], 'full'), (TASKS[0], 'focused'), (TASKS[1], 'focused'), (TASKS[1], 'full')]
_original_packet = native.packet


def packet(*args, **kwargs):
    raw = _original_packet(*args, **kwargs)
    line, rest = raw.split(b'\n', 1)
    header = json.loads(line)
    header['scope'] = 'explicit unresolved hook task; function selected by investigator; omitted dependencies not verified'
    result = wire(header) + rest
    if len(result) > 32768:
        raise ValueError('context byte budget exceeded')
    return result


native.packet = packet


def decide(records):
    actual = [(r['task'], r['arm']) for r in records]
    complete = len(actual) == 4 and set(actual) == set(ORDER)
    stable = complete and not any(r.get(k) for r in records for k in (
        'offline', 'provider_changed', 'global_analytics_changed', 'extra_requests', 'interventions'))
    pairs = []
    for task in TASKS:
        pair = {'task': task}
        for arm in native.ARMS:
            xs = [r for r in records if r['task'] == task and r['arm'] == arm]
            r = xs[0] if len(xs) == 1 else None
            calls = r.get('calls', []) if r else []
            usage = calls[0].get('usage') if len(calls) == 1 else None
            pair[arm] = {'success': bool(r and r['success']), 'tokens': usage['total_tokens'] if usage else None,
                         'whole_wall_ms': r.get('whole_wall_ms') if r else None}
        a, b = pair['full'], pair['focused']
        pair['gain'] = bool(stable and b['success'] and a['success'] and a['tokens'] is not None
            and b['tokens'] is not None and b['tokens'] < a['tokens']
            and a['whole_wall_ms'] is not None and b['whole_wall_ms'] is not None
            and b['whole_wall_ms'] <= a['whole_wall_ms'])
        pairs.append(pair)
    qualified = stable and all(r['success'] for r in records)
    return {'status': 'LOCAL_PILOT_PASSED' if qualified else 'REJECT_PILOT' if complete else 'INCOMPLETE',
        'benefit': 'OBSERVED_IN_TWO_PAIRS' if all(p['gain'] for p in pairs) else 'NOT_DEMONSTRATED',
        'pairs': pairs, 'automatic_roles': [], 'sustained_benefit': False, 'host_work_complete': False,
        'all_agent_tokens': 'UNKNOWN; investigator counters unavailable',
        'scope': 'One sample per arm/task, two related defects in one function; exact selection, not NL discovery'}


def prepare(args):
    start = time.perf_counter()
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    engine = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(engine / 'src'))
    from specgraph.indexer import index_repository
    import specgraph.indexer
    if Path(specgraph.indexer.__file__).resolve().parent.parent != engine / 'src':
        raise ValueError('prepare must index using the frozen checkout engine')
    if len(args.implementation_commit or '') != 40:
        raise ValueError('full committed implementation identity required')
    # Input is exported from Git by the owner before native Windows preparation.
    source = args.source.read_bytes()
    rows, files = [], {}
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'context_hint')
    for identity in TASKS:
        dest = out / 'prepared' / identity
        path = dest / 'corpus/src/specgraph/hook_context.py'
        path.parent.mkdir(parents=True)
        path.write_bytes(source)
        cfg = dest / 'corpus/specgraph.yaml'
        cfg.write_bytes(b'project_name: tare.tools.specgraph\nindexed_paths: [src]\ngoverned_paths: []\n')
        index_start = time.perf_counter()
        metadata = index_repository(dest / 'corpus', state_dir=dest / 'state')
        index_ms = (time.perf_counter() - index_start) * 1000
        task = {'id': identity, 'repo': 'tare.tools.specgraph', 'base': args.source_commit,
            'path': 'src/specgraph/hook_context.py', 'source_sha256': sha(source),
            'symbol': 'context_hint', 'target_range': [node.lineno, node.end_lineno],
            'snapshot': metadata['content_hash'], 'prompt': PROMPTS[identity]}
        oracle = dest / 'oracle.py'
        oracle.write_bytes(('import sys\nsys.path[:0] = ' + repr([str(engine / 'src'), str(engine / 'tools')])
            + '\nfrom host_pilot_oracles import main\nraise SystemExit(main(' + repr(identity) + ', sys.argv[1]))\n').encode())
        task['oracle_sha256'] = sha(oracle.read_bytes())
        save(dest / 'task.json', task)
        baseline = subprocess.run([sys.executable, '-B', str(oracle), str(path)], capture_output=True, timeout=10)
        report = json.loads(baseline.stdout)
        if baseline.returncode != 1 or report['passed']:
            raise ValueError('task must reproduce a current unresolved defect')
        row = {'id': identity, 'task': str(dest / 'task.json'), 'root': str(dest / 'corpus'),
            'state': str(dest / 'state'), 'source': str(path), 'oracle': str(oracle), 'index_ms': index_ms,
            'baseline': report, 'packet_bytes': {a: len(packet(task, dest / 'corpus', dest / 'state', a, 'COPY')) for a in native.ARMS}}
        rows.append(row)
        for f in [path, cfg, oracle, dest / 'task.json']:
            files[str(f)] = sha(f.read_bytes())
    import aider.coders.base_coder, aider.coders.editblock_coder, aider.commands, aider.io, aider.args
    paths = list((engine / 'src').rglob('*.py')) + [engine / 'tools' / name for name in (
        'qualify_new_task_pilot.py', 'host_pilot_oracles.py', 'qualify_native_host.py', 'qualify_context_selection.py',
        'aider_headless.py', 'response_contract.py', 'native_edit_contract.py')]
    paths += [args.contract.resolve(), args.registration.resolve(), args.source.resolve(),
        Path(aider.coders.base_coder.__file__), Path(aider.coders.editblock_coder.__file__),
        Path(aider.commands.__file__), Path(aider.io.__file__), Path(aider.args.__file__)]
    for path in paths:
        files[str(path.resolve())] = sha(path.read_bytes())
    p = {'schema': SCHEMA, 'work_id': native.WORK, 'implementation_commit': args.implementation_commit,
        'source_commit': args.source_commit, 'tasks': rows, 'files': files, 'engine': str(engine / 'src'),
        'runner': str(Path(__file__).resolve()), 'headless': str(engine / 'tools/aider_headless.py'),
        'aider_version': importlib.metadata.version('aider-chat'), 'litellm_version': importlib.metadata.version('litellm'),
        'python': sys.version, 'provider': native.base.provider(), 'analytics_before': native.base.analytics(),
        'budgets': {'real_calls': 4, 'calls_per_trial': 1, 'output_tokens': 2048, 'retry_budget': 0,
            'packet_bytes': 32768, 'query_ms': 1000, 'request_bytes': 131072, 'response_bytes': 1048576,
            'trial_seconds': 150}, 'order': ORDER, 'automatic_roles': [],
        'scope': 'Two investigator-selected unresolved defects, no known fix sent or required; four native calls',
        'decision_rule': 'All four pass; focused uses fewer reported tokens and no greater whole time in each pair; no retries/drift/unknown counters. No sustained/global claim.'}
    p['preparation_wall_ms'] = (time.perf_counter() - start) * 1000
    save(out / 'protocol.json', p)
    save(out / 'protocol-lock.json', {'sha256': sha(wire(p))})
    return {'status': 'PREPARED_NO_INFERENCE', 'preparation_wall_ms': p['preparation_wall_ms'],
            'tasks': [{k: r[k] for k in ('id', 'baseline', 'packet_bytes', 'index_ms')} for r in rows]}


def run(out):
    p = native.frozen(out)
    if p['schema'] != SCHEMA or p['order'] != [list(x) for x in ORDER]:
        raise ValueError('only the frozen four-cell pilot can authorize calls')
    probe = read(out / 'offline-preflight/receipt.json')
    if not probe['success'] or probe['extra_requests'] or probe['global_analytics_changed']:
        raise ValueError('offline native preflight required')
    # Reserve the entire study once; never restart partial runs automatically.
    with (out / 'run-reserved.json').open('x', encoding='utf-8') as stream:
        json.dump({'max_calls': 4, 'order': ORDER}, stream)
    start, records = time.perf_counter(), []
    for task, arm in ORDER:
        started = time.perf_counter()
        result = native.trial(out, task, arm, 1)
        result['whole_wall_ms'] = (time.perf_counter() - started) * 1000
        if result['whole_wall_ms'] > 150000:
            result['success'] = False
            result['whole_budget_exceeded'] = True
        save(out / f'r1-{task}-{arm}' / 'receipt.json', result)
        records.append(result)
        save(out / 'results.json', {'records': records, 'decision': decide(records)})
        print(json.dumps({k: result[k] for k in ('task', 'arm', 'success', 'whole_wall_ms')}), flush=True)
        if result.get('provider_changed') or result['global_analytics_changed']:
            break
    decision = decide(records)
    save(out / 'decision.json', decision)
    save(out / 'cost.json', {'preparation_wall_ms': p['preparation_wall_ms'],
        'study_wall_ms': (time.perf_counter() - start) * 1000,
        'trial_whole_wall_ms': sum(r['whole_wall_ms'] for r in records),
        'offline_preflight_wall_ms': probe['total_wall_ms'], 'investigator_tokens': None,
        'investigator_time': 'Not metered; excluded from model/host timing', 'automatic_roles': []})
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'emit', 'probe', 'run'])
    parser.add_argument('--output', required=True, type=Path)
    for key in ('source', 'contract', 'registration', 'trial-dir'):
        parser.add_argument('--' + key, type=Path)
    for key in ('source-commit', 'implementation-commit', 'task', 'arm'):
        parser.add_argument('--' + key)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.action == 'prepare':
        result = prepare(args)
    elif args.action == 'emit':
        native.emit(args.output, args.task, args.arm, args.trial_dir.resolve())
        return
    elif args.action == 'probe':
        result = native.trial(args.output, TASKS[0], 'focused', 0, offline=True)
    else:
        if not args.execute:
            parser.error('run requires --execute')
        result = run(args.output)
    sys.stdout.buffer.write(wire(result))


if __name__ == '__main__':
    main()
