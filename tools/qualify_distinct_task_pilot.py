"""Four bounded Aider calls on two distinct, previously unresolved functions.

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
from distinct_task_oracles import TASKS

SCHEMA = 'tare.tools/distinct-task-pilot/1'
ORDER = [(TASKS[0], 'full'), (TASKS[0], 'focused'), (TASKS[1], 'focused'), (TASKS[1], 'full')]
SELECTIONS = {
    TASKS[0]: ('src/specgraph/lifecycle.py', 'reconcile_repository'),
    TASKS[1]: ('src/specgraph/federation.py', 'acquire_federated_documents'),
}
PROMPTS = {
    TASKS[0]: 'Repair reconcile_repository: a worker whose lease has expired at final journal acknowledgement must not acknowledge events, delete pending paths, or overwrite the prior successful observation, even if owner and journal IDs still match. Check validity at the final locked state, including expiry while acquiring that lock. Preserve valid and renewed lease acknowledgements, replacement-owner protection, concurrent-event cutoff, PARTIAL/failure handling, exception propagation, and journal/reason/events_through receipt identities. Do not change indexing, heartbeat policy or public return shapes.',
    TASKS[1]: 'Repair acquire_federated_documents: when any selected document fails acquisition validation, return no documents, including when earlier documents were already read before a later unpinned locator, owner mismatch or semantic identity mismatch is detected. Keep existing failure status/reason, repository/manifest/scope identity, metadata_only=true, body_read=false and body_delivered=false. Preserve complete successful bodies, criteria, pins and success flags, source-invalid/missing-owner behavior, byte budgets, and input immutability. Do not weaken pin/hash/source validation.',
}
save, read, sha, wire = native.save, native.read, native.sha, native.wire
_packet, _frozen = native.packet, native.frozen


def packet(*args, **kwargs):
    header, body = _packet(*args, **kwargs).split(b'\n', 1)
    value = json.loads(header)
    value['scope'] = 'new unresolved task; investigator-selected exact function; omitted dependencies not verified'
    result = wire(value) + body
    if len(result) > 32768:
        raise ValueError('context byte budget exceeded')
    return result


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
    complete = len(records) == 4 and {(r['task'], r['arm']) for r in records} == set(ORDER)
    stable = complete and all(not r.get(k) for r in records for k in
        ('offline', 'provider_changed', 'global_analytics_changed', 'extra_requests', 'interventions'))
    pairs = []
    for task in TASKS:
        pair = {'task': task}
        for arm in native.ARMS:
            selected = [r for r in records if (r['task'], r['arm']) == (task, arm)]
            row = selected[0] if len(selected) == 1 else None
            calls = row.get('calls', []) if row else []
            usage = calls[0].get('usage') if len(calls) == 1 else None
            pair[arm] = {'success': bool(row and row['success']),
                         'tokens': usage['total_tokens'] if usage else None,
                         'whole_wall_ms': row.get('whole_wall_ms') if row else None}
        a, b = pair['full'], pair['focused']
        pair['gain'] = bool(stable and a['success'] and b['success']
                            and a['tokens'] is not None and b['tokens'] is not None
                            and b['tokens'] < a['tokens']
                            and a['whole_wall_ms'] is not None and b['whole_wall_ms'] is not None
                            and b['whole_wall_ms'] <= a['whole_wall_ms'])
        pairs.append(pair)
    return {'status': 'LOCAL_PILOT_PASSED' if stable and all(r['success'] for r in records)
            else 'REJECT_PILOT' if complete else 'INCOMPLETE',
            'benefit': 'OBSERVED_IN_TWO_PAIRS' if all(p['gain'] for p in pairs) else 'NOT_DEMONSTRATED',
            'pairs': pairs, 'automatic_roles': [], 'sustained_benefit': False,
            'host_work_complete': False, 'investigator_tokens': None,
            'scope': 'one sample per arm/task, distinct modules, explicit selection; shared preparation reported separately'}


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
        node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == symbol)
        dest = out / 'prepared' / identity
        target = dest / 'corpus' / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        cfg = dest / 'corpus/specgraph.yaml'
        cfg.write_bytes(b'project_name: tare.tools.specgraph\nindexed_paths: [src]\ngoverned_paths: []\n')
        before_index = time.perf_counter()
        metadata = specgraph.indexer.index_repository(dest / 'corpus', state_dir=dest / 'state')
        index_ms = (time.perf_counter() - before_index) * 1000
        task = {'id': identity, 'repo': 'tare.tools.specgraph', 'base': args.source_commit,
                'path': relative, 'source_sha256': sha(source), 'symbol': symbol,
                'target_range': [node.lineno, node.end_lineno], 'snapshot': metadata['content_hash'],
                'prompt': PROMPTS[identity]}
        oracle = dest / 'oracle.py'
        oracle.write_bytes(('import sys\nsys.path[:0] = ' + repr([str(engine / 'src'), str(engine / 'tools')])
            + '\nfrom distinct_task_oracles import main\nraise SystemExit(main(' + repr(identity) + ', sys.argv[1]))\n').encode())
        task['oracle_sha256'] = sha(oracle.read_bytes())
        save(dest / 'task.json', task)
        check = subprocess.run([sys.executable, '-B', str(oracle), str(target)], capture_output=True, timeout=10)
        (dest / 'baseline.stdout').write_bytes(check.stdout)
        (dest / 'baseline.stderr').write_bytes(check.stderr)
        baseline = json.loads(check.stdout)
        failed = [r for r in baseline['checks'] if not r['passed']]
        if check.returncode != 1 or not failed or any(r['kind'] != 'regression' for r in failed):
            raise ValueError('baseline must fail new requirements and pass all preservations')
        tasks.append({'id': identity, 'task': str(dest / 'task.json'), 'root': str(dest / 'corpus'),
            'state': str(dest / 'state'), 'source': str(target), 'oracle': str(oracle),
            'index_ms': index_ms, 'baseline': baseline,
            'packet_bytes': {arm: len(packet(task, dest / 'corpus', dest / 'state', arm, 'COPY')) for arm in native.ARMS}})
        for path in (target, cfg, oracle, dest / 'task.json'):
            files[str(path)] = sha(path.read_bytes())
    import aider.coders.base_coder, aider.coders.editblock_coder, aider.commands, aider.io, aider.args
    paths = list((engine / 'src').rglob('*.py')) + [engine / 'tools' / name for name in (
        'qualify_distinct_task_pilot.py', 'distinct_task_oracles.py', 'qualify_native_host.py',
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
        'budgets': {'real_calls': 4, 'calls_per_trial': 1, 'output_tokens': 2048, 'retry_budget': 0,
                    'packet_bytes': 32768, 'query_ms': 1000, 'request_bytes': 131072,
                    'response_bytes': 1048576, 'trial_seconds': 150},
        'preparation_wall_ms': (time.perf_counter() - started) * 1000,
        'scope': 'Full-file versus exact-function snapshot context; discovery and dependencies not evaluated',
        'cost_limits': ['shared preparation/indexing separate from paired trial totals',
                        'investigator tokens and total engineering time unavailable',
                        'offline fixtures never counted as inference']}
    save(out / 'protocol.json', protocol)
    save(out / 'protocol-lock.json', {'sha256': sha(wire(protocol))})
    return {'status': 'PREPARED_NO_INFERENCE', 'preparation_wall_ms': protocol['preparation_wall_ms'],
            'tasks': [{'id': t['id'], 'packet_bytes': t['packet_bytes'], 'index_ms': t['index_ms']} for t in tasks]}


def run(out):
    protocol = frozen(out)
    if protocol['provider'] is None or protocol['order'] != [list(x) for x in ORDER]:
        raise ValueError('healthy provider binding and frozen order required')
    probe = read(out / 'offline-preflight/receipt.json')
    if not probe['success'] or probe['extra_requests'] or probe['global_analytics_changed']:
        raise ValueError('offline native preflight required')
    with (out / 'run-reserved.json').open('x', encoding='utf-8') as stream:
        json.dump({'max_calls': 4, 'order': ORDER}, stream)
    start, records = time.perf_counter(), []
    for task, arm in ORDER:
        before = time.perf_counter()
        row = native.trial(out, task, arm, 1)
        row['whole_wall_ms'] = (time.perf_counter() - before) * 1000
        if row['whole_wall_ms'] > 150000:
            row.update(success=False, whole_budget_exceeded=True)
        save(out / f'r1-{task}-{arm}/receipt.json', row)
        records.append(row)
        save(out / 'results.json', {'records': records, 'decision': decide(records)})
        print(json.dumps({k: row[k] for k in ('task', 'arm', 'success', 'whole_wall_ms')}), flush=True)
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
    native.packet, native.frozen = packet, frozen
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
        native.emit(out, args.task, args.arm, args.trial_dir.resolve())
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
