"""Behavioral oracles for the second pilot; no model or solution generation."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

TASKS = ('expired-reconciliation-lease', 'partial-federated-acquisition')


def evaluate(candidate: Path, task: str) -> dict:
    if task not in TASKS:
        raise ValueError('unknown task')
    spec = importlib.util.spec_from_file_location('specgraph._distinct_candidate', candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []

    def check(name, predicate, *, regression=False):
        try:
            rows.append({'check': name, 'kind': 'regression' if regression else 'preservation',
                         'passed': bool(predicate())})
        except Exception as exc:
            rows.append({'check': name, 'kind': 'regression' if regression else 'preservation',
                         'passed': False, 'error': type(exc).__name__})

    with tempfile.TemporaryDirectory(prefix='sg-distinct-') as directory:
        base = Path(directory)
        if task == TASKS[0]:
            for mode in ('valid', 'expired', 'expired-at-final-lock', 'renewed', 'replaced', 'partial', 'error', 'concurrent'):
                root, state = base / mode, base / (mode + '-state')
                root.mkdir()
                module.record_changes(root, ['src/a.py'], state_dir=state)
                original_writer = module._writer
                with original_writer(root, state) as (_, values):
                    values.update(last_reconciled_snapshot='b' * 64,
                                  last_observation_started_at=1.0, last_observation_completed_at=2.0)
                writer_calls = 0

                @contextmanager
                def writer(*args, **kwargs):
                    nonlocal writer_calls
                    writer_calls += 1
                    with original_writer(*args, **kwargs) as pair:
                        if mode == 'expired-at-final-lock' and writer_calls == 2:
                            pair[1]['lease_until'] = time.time() - 1
                        yield pair

                def index(*args, **kwargs):
                    if mode in ('expired', 'renewed', 'replaced'):
                        with original_writer(root, state) as (_, values):
                            values['lease_until'] = time.time() - 1 if mode == 'expired' else time.time() + 30
                            if mode == 'replaced':
                                values['owner'] = 'replacement-owner'
                    if mode == 'concurrent':
                        module.record_changes(root, ['src/a.py'], state_dir=state)
                    if mode == 'error':
                        raise RuntimeError('injected publication failure')
                    return {'status': 'PARTIAL' if mode == 'partial' else 'READY', 'content_hash': 'a' * 64}

                result, raised = None, False
                with patch.object(module, '_writer', writer), patch('specgraph.indexer.index_repository', index):
                    try:
                        result = module.reconcile_repository(root, state_dir=state, reason='pilot')
                    except RuntimeError:
                        raised = True
                with original_writer(root, state) as (connection, values):
                    observed = dict(values)
                    pending = connection.execute('SELECT path,seq FROM pending ORDER BY path').fetchall()
                if mode in ('expired', 'expired-at-final-lock', 'replaced', 'partial', 'error'):
                    check(mode + '-no-ack', lambda: (raised if mode == 'error' else
                          result['reconciliation']['acknowledged'] is False),
                          regression=mode.startswith('expired'))
                    check(mode + '-pending-and-observation-preserved', lambda:
                          pending == [('src/a.py', 1)] and observed['reconciled_seq'] == 0
                          and observed['last_reconciled_snapshot'] == 'b' * 64
                          and observed['last_observation_started_at'] == 1.0
                          and observed['last_observation_completed_at'] == 2.0,
                          regression=mode.startswith('expired'))
                    if mode == 'replaced':
                        check('replacement-owner-preserved', lambda: observed['owner'] == 'replacement-owner')
                else:
                    check(mode + '-ack', lambda: not raised and result['reconciliation']['acknowledged'] is True
                          and observed['reconciled_seq'] == 1 and observed['last_reconciled_snapshot'] == 'a' * 64)
                    check(mode + '-pending', lambda: pending == ([('src/a.py', 2)] if mode == 'concurrent' else []))
                if result is not None:
                    check(mode + '-receipt-identity', lambda: result['reconciliation']['reason'] == 'pilot'
                          and result['reconciliation']['events_through'] == 1
                          and result['reconciliation']['journal_id'] == observed['journal_id'])
            # Added after the frozen trial: preserve the lease timestamp and
            # advance the clock while acquiring the final lock. Merely setting
            # lease_until in the past does not catch use of a pre-lock clock.
            root, state = base / 'lock-wait', base / 'lock-wait-state'
            root.mkdir()
            module.record_changes(root, ['src/a.py'], state_dir=state)
            original_writer = module._writer
            with original_writer(root, state) as (_, values):
                values.update(last_reconciled_snapshot='b' * 64,
                              last_observation_started_at=1.0, last_observation_completed_at=2.0)
            clock, acquisitions = [1000.0], [0]

            @contextmanager
            def delayed_writer(*args, **kwargs):
                with original_writer(*args, **kwargs) as pair:
                    acquisitions[0] += 1
                    if acquisitions[0] == 2:
                        clock[0] = 1000.0 + module.LEASE_SECONDS + 1
                    yield pair

            with patch.object(module, 'time', SimpleNamespace(time=lambda: clock[0])), \
                 patch.object(module, '_writer', delayed_writer), \
                 patch('specgraph.indexer.index_repository', return_value={'status': 'READY', 'content_hash': 'a' * 64}):
                result = module.reconcile_repository(root, state_dir=state, reason='pilot')
            with original_writer(root, state) as (connection, values):
                observed = dict(values)
                pending = connection.execute('SELECT path,seq FROM pending ORDER BY path').fetchall()
            check('lease-expires-while-final-lock-is-acquired-no-ack',
                  lambda: result['reconciliation']['acknowledged'] is False, regression=True)
            check('lease-expires-while-final-lock-is-acquired-preserves-observation',
                  lambda: pending == [('src/a.py', 1)] and observed['reconciled_seq'] == 0
                  and observed['last_reconciled_snapshot'] == 'b' * 64
                  and observed['last_observation_started_at'] == 1.0
                  and observed['last_observation_completed_at'] == 2.0, regression=True)
        else:
            owner = base / 'owner'
            (owner / 'docs').mkdir(parents=True)
            bodies = {name: f'---\nid: SPEC-{name.upper()}\nacceptance_criteria:\n  AC-01: preserve {name}\n---\n{name}\n'
                      for name in ('a', 'z')}
            for name, body in bodies.items():
                (owner / f'docs/{name}.md').write_bytes(body.encode())

            def git(*args):
                return subprocess.check_output(['git', '-c', f'core.hooksPath={base / "empty-hooks"}',
                    '-c', 'user.name=Pilot fixture', '-c', 'user.email=pilot@example.invalid',
                    '-C', str(owner), *args], timeout=5).decode().strip()

            git('init', '-q'); git('add', '.'); git('commit', '-qm', 'pinned fixture')
            revision = git('rev-parse', 'HEAD')
            entries = []
            for name, body in bodies.items():
                digest = hashlib.sha256(body.encode()).hexdigest()
                entries.append({'id': digest, 'semantic_document_id': f'owner:docs/{name}.md',
                    'doc_type': 'spec', 'title': name, 'relative_path': f'owner@{revision}:docs/{name}.md',
                    'sha256': digest, 'target_repositories': ['owner'], 'acceptance_criteria': []})
            manifest = base / 'manifest.json'
            ids = [e['semantic_document_id'] for e in entries]
            for mode, expected in [('valid', 'READY'), ('unpinned', 'UNPINNED_SOURCE'),
                                   ('wrong-owner', 'SOURCE_MISMATCH'), ('wrong-identity', 'SOURCE_MISMATCH'),
                                   ('bad-hash', 'SOURCE_INVALID'), ('missing-owner', 'UNAVAILABLE')]:
                changed = copy.deepcopy(entries)
                if mode == 'unpinned':
                    changed[1]['relative_path'] = 'docs/z.md'
                elif mode == 'wrong-owner':
                    changed[1]['relative_path'] = f'other@{revision}:docs/z.md'
                elif mode == 'wrong-identity':
                    changed[1]['relative_path'] = f'owner@{revision}:docs/other.md'
                elif mode == 'bad-hash':
                    changed[1]['sha256'] = changed[1]['id'] = '0' * 64
                raw = json.dumps({'version': '2.0', 'specs': changed}).encode()
                manifest.write_bytes(raw)
                roots = {} if mode == 'missing-owner' else {'owner': owner}
                result = module.acquire_federated_documents(manifest, 'owner', roots, include_ids=ids)
                check(mode + '-status', lambda: result['status'] == expected)
                check(mode + '-envelope-identity', lambda: result['repository'] == 'owner'
                      and result['manifest_sha256'] == hashlib.sha256(raw).hexdigest()
                      and len(result['grounding_scope_sha256']) == 64 and result['authority'] == 'NONE')
                check(mode + '-inputs-unchanged', lambda: manifest.read_bytes() == raw and ids == [e['semantic_document_id'] for e in entries])
                if mode == 'valid':
                    check('valid-complete-bodies-and-criteria', lambda: len(result['documents']) == 2
                          and all(d['body'] == bodies[n] and d['revision'] == revision
                                  and d['semantic_document_id'] == f'owner:docs/{n}.md'
                                  and d['acceptance_criteria'] == {'AC-01': f'preserve {n}'}
                                  and d['body_read'] is True and d['body_delivered'] is True
                                  for n, d in zip(('a', 'z'), result['documents'])))
                    check('valid-flags', lambda: result['metadata_only'] is False
                          and result['body_read'] is True and result['body_delivered'] is True)
                else:
                    check(mode + '-no-partial-documents', lambda: result['documents'] == [],
                          regression=mode in ('unpinned', 'wrong-owner', 'wrong-identity'))
                    check(mode + '-failure-flags', lambda: result['metadata_only'] is True
                          and result['body_read'] is False and result['body_delivered'] is False)
                bounded = module.acquire_federated_documents(manifest, 'owner', roots, include_ids=ids, max_bytes=1024)
                check(mode + '-byte-budget', lambda: len(module.serialize_federation(bounded).encode()) <= 1024)
    return {'task': task, 'passed': all(row['passed'] for row in rows), 'checks': rows}


def main(task, candidate):
    report = evaluate(Path(candidate), task)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1
