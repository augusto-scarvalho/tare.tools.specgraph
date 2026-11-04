"""Opt-in, offline X3 packet and context-lookup comparison; no model calls or retained corpus.

Run: python tools/qualify_context_delta.py --output /fresh/path/result.json
The report is small; temporary sources and graph objects are removed on return.
This measures transport bytes and deterministic context tasks, not agent delivery
quality, invoice tokens, prompt-cache hit rates or index-computation savings.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import statistics
import tempfile
import time
import sys

import specgraph.context as context_owner
from specgraph.context import ContextPackager


EDITS = ("implementation", "criterion", "removed_test", "removed_code", "line_shift",
         "verification_binding", "unrelated", "broad_rewrite")


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def corpus(root):
    """Fixed before measurement: 12 implementations/tests and three criteria."""
    for name in ("src", "tests", "specs"):
        (root / name).mkdir(parents=True)
    spec = ("---\nid: SPEC-X3-FIXTURE\ntitle: Bounded arithmetic\nstatus: ACCEPTED\n"
            "acceptance_criteria:\n  AC-01: Reject negative input\n"
            "  AC-02: Add each declared offset\n  AC-03: Preserve café in diagnostics\n---\n"
            "Each operation has its own explicit specification and verification link.\n")
    source, tests = [], []
    for i in range(12):
        source.append(f'def operation_{i}(value):\n    """@spec SPEC-X3-FIXTURE\n'
                      f'    Apply offset {i} after validating this operation input.\n    """\n'
                      '    if value < 0:\n        raise ValueError("café: negative input")\n'
                      f'    return value + {i}\n')
        tests.append(f'def test_operation_{i}():\n    """@verifies SPEC-X3-FIXTURE [AC-01, AC-02, AC-03]"""\n'
                     f'    assert operation_{i}(10) == {10+i}\n')
    write(root / 'specs/contract.md', spec)
    write(root / 'src/operations.py', '\n'.join(source))
    write(root / 'tests/test_operations.py', '\n'.join(tests))


def write(path, text):
    path.write_text(text, encoding="utf-8", newline="\n")


def edit(root, name):
    source = root / 'src/operations.py'
    tests = root / 'tests/test_operations.py'
    spec = root / 'specs/contract.md'
    if name == 'implementation':
        write(source, source.read_text(encoding='utf-8').replace('return value + 0\n', 'return value + 1\n', 1))
    elif name == 'criterion':
        write(spec, spec.read_text(encoding='utf-8').replace('Reject negative input', 'Reject nonpositive input'))
    elif name == 'removed_test':
        write(tests, tests.read_text(encoding='utf-8').split('\ndef test_operation_11')[0] + '\n')
    elif name == 'removed_code':
        write(source, source.read_text(encoding='utf-8').split('\ndef operation_11')[0] + '\n')
    elif name == 'line_shift':
        write(source, '# Shift every implementation range.\n\n' + source.read_text(encoding='utf-8'))
    elif name == 'verification_binding':
        write(tests, tests.read_text(encoding='utf-8').replace('@verifies SPEC-X3-FIXTURE [AC-01, AC-02, AC-03]', 'No verification binding', 1))
    elif name == 'unrelated':
        write(root / 'src/unrelated.py', 'unrelated = 42\n')
    elif name == 'broad_rewrite':
        write(source, source.read_text(encoding='utf-8').replace('return value + ', 'return value * '))
        changed = tests.read_text(encoding='utf-8')
        for i in range(12):
            changed = changed.replace(f'operation_{i}(10) == {10+i}', f'operation_{i}(10) == {10*i}')
        write(tests, changed)
        write(spec, spec.read_text(encoding='utf-8').replace('Add each declared offset', 'Multiply by each declared offset'))
    else:
        raise ValueError('unknown frozen edit')


def reconstructed(previous, response):
    if response['kind'] in ('full', 'refresh'):
        return {**response, 'kind': 'full', 'reason': None}
    if response['base_snapshot'] != previous['snapshot']['snapshot']:
        raise ValueError('base snapshot mismatch')
    units = {unit['id']: unit for unit in previous['units']}
    for identity in response['removed']:
        del units[identity]
    units.update({unit['id']: unit for unit in response['upserts']})
    return {'kind': 'full', 'reason': None, 'snapshot': response['snapshot'],
            'handle': response['handle'], 'units': [units[identity] for identity in response['unit_order']]}


def task_answer(packet):
    """Use supplied context to recover criteria, implementation and verification obligations."""
    criteria = {}
    implementations, tests = {}, {}
    for unit in packet['units']:
        if unit['kind'] == 'spec':
            criteria.update(unit['acceptance_criteria'])
        elif not unit.get('absent', False):
            parsed = ast.parse(unit['content'])
            value = {'body': ast.dump(parsed, include_attributes=False), 'links': unit['links'],
                     'range': [unit['start_line'], unit['end_line']]}
            (implementations if unit['kind'] == 'code' else tests)[unit['id']] = value
    return {'criteria': criteria, 'implementations': implementations, 'verifying_tests': tests}


def qualify():
    rows = []
    with tempfile.TemporaryDirectory(prefix='specgraph-x3-') as folder:
        for name in EDITS:
            root = Path(folder) / name
            corpus(root)
            baseline = ContextPackager.generate_spec_snapshot('SPEC-X3-FIXTURE', root, work_id='X3:' + name, clock=lambda: 1000)
            before_files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in root.rglob('*') if p.is_file()}
            edit(root, name)
            start = time.perf_counter()
            full = ContextPackager.generate_spec_snapshot('SPEC-X3-FIXTURE', root, work_id='X3:' + name, clock=lambda: 1001)
            full_ms = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            response = ContextPackager.generate_spec_delta('SPEC-X3-FIXTURE', baseline['handle'], root,
                        work_id='X3:' + name, clock=lambda: 1001)
            delta_ms = (time.perf_counter() - start) * 1000
            rebuilt = reconstructed(baseline, response)
            equal = rebuilt == full
            answers_equal = task_answer(rebuilt) == task_answer(full)
            if not equal or not answers_equal:
                raise AssertionError('X3 correctness failure: ' + name)
            # Reconstruct the candidate the owner compares, even after a refresh.
            units = full['handle']['units']
            raw_delta = {'kind': 'delta', 'base_snapshot': baseline['handle']['snapshot'],
                'snapshot': full['snapshot'], 'handle': full['handle'],
                'unit_order': [u['id'] for u in full['units']],
                'upserts': [u for u in full['units'] if baseline['handle']['units'].get(u['id']) != u['digest']],
                'removed': sorted(set(baseline['handle']['units']) - set(units))}
            after_files = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in root.rglob('*') if p.is_file()}
            assert not any('.specgraph' in Path(p).parts for p in after_files), 'must not create persisted graph history'
            rows.append({'case': name, 'full_bytes': len(wire(full)), 'response_bytes': len(wire(response)),
                'candidate_delta_bytes': len(wire(raw_delta)), 'handle_bytes': len(wire(full['handle'])),
                'response_kind': response['kind'], 'reason': response.get('reason'),
                'response_ratio': len(wire(response)) / len(wire(full)),
                'reconstruction_equal': equal, 'context_task_answers_equal': answers_equal,
                'full_wall_ms': round(full_ms, 3), 'delta_complete_wall_ms': round(delta_ms, 3),
                'before_source_hash': hashlib.sha256(wire(before_files)).hexdigest(),
                'after_source_hash': hashlib.sha256(wire(after_files)).hexdigest()})
    median = statistics.median(row['response_ratio'] for row in rows)
    larger = sum(row['candidate_delta_bytes'] > row['full_bytes'] for row in rows) / len(rows)
    useful = median <= .2 and larger <= .3
    return {'schema': 'tare.tools/specgraph-x3-qualification/1',
            'implementation_sha256': hashlib.sha256(Path(context_owner.__file__).read_bytes()).hexdigest(),
            'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'python': sys.version.split()[0], 'platform': sys.platform, 'cases': rows,
            'summary': {'case_count': len(rows), 'reconstruction_equal': len(rows), 'context_task_answers_equal': len(rows),
                        'median_response_ratio': median, 'candidate_larger_fraction': larger,
                        'exploratory_byte_target_met': useful, 'decision': 'KEEP_OPT_IN' if useful else 'REJECT_AUTOMATIC_ADOPTION',
                        'model_calls_for_data': 0, 'index_history_widened': False},
            'limits': ['Synthetic context lookup tasks, not agent-delivery quality or model preference.',
                       'Entire deterministic serialized responses include refreshed handles and metadata.',
                       'Both paths rebuild the complete graph; timing is descriptive, not a computation saving claim.',
                       'No token invoice, prompt-cache hit rate or automatic runtime adoption was measured.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a fresh output file; preserve earlier results')
    result = qualify()
    raw = wire(result) + b'\n'
    if len(raw) > 65536:
        raise ValueError('Qualification receipt exceeded 64 KiB')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(result['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
