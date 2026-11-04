"""Reusable hook falsifiers, strengthened after the first pilot's identity review.

The original trial scoring uses its separately archived, frozen evaluator.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import tempfile

TASKS = ('dirty-path-identity', 'missing-symbol-with-spec')


def evaluate(candidate: Path, task: str) -> dict:
    from specgraph.indexer import index_repository
    if task not in TASKS:
        raise ValueError('unknown pilot task')
    spec = importlib.util.spec_from_file_location('specgraph._pilot_candidate', candidate)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rows = []
    with tempfile.TemporaryDirectory(prefix='sg-pilot-') as directory:
        root = Path(directory) / 'repo'
        (root / 'src').mkdir(parents=True)
        (root / 'src/lib.py').write_text('def leaf(): return 1\n', encoding='utf-8')
        (root / 'specs').mkdir()
        (root / 'specs/SPEC-ONE.md').write_text(
            '---\nid: SPEC-ONE\ntitle: Exact intent\nacceptance_criteria:\n  AC-01: Return one\n---\n', encoding='utf-8')
        state = Path(directory) / 'state'
        index_repository(root, state_dir=state)

        def query(**kw):
            return module.context_hint(root, role='implementer', event='pre', state_dir=state,
                                       timeout_ms=1000, **kw)

        def check(label, predicate):
            try:
                passed = bool(predicate())
                rows.append({'check': label, 'passed': passed})
            except Exception as exc:
                rows.append({'check': label, 'passed': False, 'error': type(exc).__name__})

        if task == TASKS[0]:
            # Same root and file identity must yield the same context and digest.
            rel = query(files=('src/lib.py',), dirty_paths=('src/lib.py',))
            for label, spelling in [('absolute', str(root / 'src/lib.py')),
                                    ('dot', './src/lib.py'), ('parent', 'src/../src/lib.py')]:
                check(label, lambda spelling=spelling: query(files=('src/lib.py',),
                    dirty_paths=(spelling,)) == rel)
            check('duplicates', lambda: query(files=('src/lib.py',),
                dirty_paths=('src/lib.py', str(root / 'src/lib.py'))) == rel)
            check('dedup', lambda: query(files=('src/lib.py',), dirty_paths=(str(root / 'src/lib.py'),),
                previous_digest=rel['digest'])['status'] == 'SILENT')
            check('escape', lambda: query(files=('src/lib.py',), dirty_paths=('../outside.py',))['status'] == 'OUTSIDE_SCOPE')
            check('unselected', lambda: query(dirty_paths=(str(root / 'src/lib.py'),))['dirty_paths'] == ['src/lib.py'])
        else:
            for files in [(), ('src/lib.py',)]:
                check('missing-symbol-' + str(bool(files)), lambda files=files: query(files=files,
                    symbol_id='src/lib.py:missing', spec_id='SPEC-ONE')['status'] == 'MISS')
            # A correct status alone does not preserve the requested identity.
            # Exercise both SPEC-assisted and symbol-only requests, with and
            # without a file selection; keep these distinct from frozen scores.
            expected_identity = {'status': 'MISS', 'symbol_id': 'src/lib.py:missing',
                                 'root': str(root.resolve()), 'work_id': 'PILOT-WORK',
                                 'source_ref': 'explicit:pilot-source'}
            for spec_id in [None, 'SPEC-ONE']:
                for files in [(), ('src/lib.py',)]:
                    def preserves_identity(spec_id=spec_id, files=files):
                        result = query(files=files, symbol_id=expected_identity['symbol_id'],
                                       spec_id=spec_id, work_id=expected_identity['work_id'],
                                       source_ref=expected_identity['source_ref'])
                        return all(result.get(key) == value for key, value in expected_identity.items())

                    check(f'missing-symbol-identity-spec-{bool(spec_id)}-files-{bool(files)}',
                          preserves_identity)
            check('valid-symbol-and-spec', lambda: query(symbol_id='src/lib.py:leaf', spec_id='SPEC-ONE')['status'] == 'HIT')
            check('spec-only', lambda: query(spec_id='SPEC-ONE')['intent']['acceptance_criteria'] == {'AC-01': 'Return one'})
            check('missing-spec', lambda: query(symbol_id='src/lib.py:leaf', spec_id='SPEC-MISSING')['status'] == 'MISS')
            check('mismatched-file', lambda: query(symbol_id='src/lib.py:leaf', files=('src/other.py',),
                spec_id='SPEC-ONE')['status'] == 'OUTSIDE_SCOPE')
        check('ordinary-hit', lambda: query(files=('src/lib.py',))['status'] == 'HIT')
        check('missing-symbol-alone', lambda: query(symbol_id='src/lib.py:missing')['status'] == 'MISS')
        check('byte-cap', lambda: len((json.dumps(query(files=('src/lib.py',), max_bytes=512),
            ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode()) <= 512)
        # No fresh source read is necessary for an existing published snapshot.
        (root / 'src/lib.py').unlink()
        check('snapshot-without-source', lambda: query(files=('src/lib.py',))['status'] == 'HIT')
    return {'task': task, 'passed': all(row['passed'] for row in rows), 'checks': rows}


def main(task, candidate):
    report = evaluate(Path(candidate), task)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1
