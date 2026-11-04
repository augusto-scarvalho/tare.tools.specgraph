"""Offline synthetic X5/C5 gate checks; not operational adoption proof."""
import copy
import json
import sys

import pytest

from specgraph import test_selection as selection
from specgraph.evidence import AttestationVerdict, evidence_payload_sha256
from specgraph.x5_scope import build_x5_scope


def verified(record):
    return AttestationVerdict('VERIFIED', evidence_payload_sha256(record),
                              record['attestation_ref'], 'synthetic-owner')


def scope_digest(scope):
    return selection._sha(json.dumps(scope, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8'))


@pytest.fixture
def gate(tmp_path, monkeypatch):
    files = {'src/code.py': 'value = 1\n',
             'tests/test_trace.py': 'def test_trace(): pass\n',
             'tests/test_static.py': 'def test_static(): pass\n',
             'tests/test_required.py': 'def test_required(): pass\n',
             'tests/test_other.py': 'def test_other(): pass\n',
             'pyproject.toml': '[tool.example]\n'}
    for path, text in files.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
    tests = ['tests/test_trace.py::test_trace', 'tests/test_static.py::test_static',
             'tests/test_required.py::test_required', 'tests/test_other.py::test_other']
    environment = {'synthetic': 'owner-observed'}
    monkeypatch.setattr(selection, '_environment', lambda: dict(environment))
    monkeypatch.setattr(selection, 'symbol_impact', lambda *a, **kw: None)
    monkeypatch.setattr(selection, 'static_test_files',
                        lambda *a, **kw: {'tests/test_static.py': ['src/code.py']})
    def forbidden(*a, **kw):
        raise AssertionError('No subprocesses in gate tests')
    monkeypatch.setattr(selection.subprocess, 'run', forbidden)
    fingerprints = selection._files(tmp_path)
    config = selection._sha(json.dumps(
        {k: v for k, v in fingerprints.items() if selection.Path(k).suffix in selection.CONFIG},
        sort_keys=True).encode())
    env = selection._sha(json.dumps(environment, sort_keys=True).encode())
    receipt = {'schema': selection.SCHEMA, 'tests': tests,
               'executed_blocks': {t: ['src/code.py:1'] if i == 0 else []
                                   for i, t in enumerate(tests)},
               'fingerprints': fingerprints, 'environment': environment,
               'config_sha256': config, 'environment_sha256': env,
               'pytest_args': ['tests'], 'suite_returncode': 0}
    # The target differs from the passing capture: baseline hashes are insufficient.
    (tmp_path / 'src/code.py').write_text('value = 2\n', encoding='utf-8')
    changed = ['src/code.py:1']
    mandatory = [tests[2]]
    selected = sorted(tests[:3])
    scope = build_x5_scope(tmp_path, changed, receipt, selected, mandatory)
    project = scope['project']
    digest = selection._sha(b'synthetic-observation')
    dependencies = {f'{project}:{k}': v for k, v in selection._files(tmp_path).items()}
    snapshot = {'repository': project, 'revision_seq': 2, 'content_hash': digest}
    sources = {f'{project}:SPEC.md': digest}
    criteria = {f'{project}:SPEC.md#AC-1': digest}
    artifacts = {'receipt://synthetic/result': digest}
    # Independent fixture observations are captured before constructing the claim.
    current = {'snapshot': dict(snapshot), 'config_sha256': config,
               'environment_sha256': env, 'source_hashes': dict(sources),
               'criterion_hashes': dict(criteria), 'dependency_hashes': dict(dependencies),
               'artifact_hashes': dict(artifacts)}
    record = {'schema': 'tare.tools/execution-evidence/1', 'evidence_id': 'synthetic',
              'executor': 'owner', 'observed_at': '2026-10-14',
              'relation': 'EXECUTED_COVERAGE', 'outcome': 'PASSED',
              'command': ['python', '-m', 'pytest', *selected],
              'config_sha256': config, 'environment_sha256': env,
              'snapshot': dict(snapshot), 'source_hashes': dict(sources),
              'criterion_hashes': dict(criteria),
              'dependencies': {'complete': True, 'hashes': dict(dependencies)},
              'artifact_hashes': dict(artifacts), 'attestation_ref': 'attestation://synthetic',
              'coverage': {'method': 'synthetic-owner-observed',
                           'tests': [f'{project}:{t}' for t in selected],
                           'symbols': [f'{project}:src/code.py:1']}, 'x5_scope_sha256': scope_digest(scope)}
    return tmp_path, changed, receipt, mandatory, record, current


def recommend(gate, verifier=verified):
    root, changed, receipt, mandatory, record, current = gate
    return selection.recommend_from_receipt(changed, receipt, root=root, mandatory=mandatory,
        c5_evidence=record, c5_current=current, attestation_verifier=verifier)


def test_scoped_union_and_command(gate):
    original = copy.deepcopy(gate[1:])
    result = recommend(gate)
    assert result['mode'] == 'SELECTIVE_SCOPED_C5'
    assert result['qualification'] == 'QUALIFIED_SCOPED_C5'
    assert result['fallback_reasons'] == []
    assert 'x5_scope' not in gate[4]
    assert len(gate[4]['x5_scope_sha256']) == 64
    assert result['tests'] == result['policy_tests'] == sorted(gate[2]['tests'][:3])
    assert gate[2]['tests'][1] not in result['candidates']
    assert result['command'] == [sys.executable, '-m', 'pytest', *result['policy_tests']]
    assert gate[1:] == original


def test_missing_c5_and_capture_strings_have_no_authority(gate, monkeypatch):
    root, changed, receipt, mandatory, _, _ = gate
    receipt.update(complete=True, dependency_closure='EXTERNALLY_VERIFIED', attestation='VERIFIED')
    def forbidden(*a, **kw):
        pytest.fail('Legacy CLI must not build scope or validate C5')
    monkeypatch.setattr(selection, 'build_x5_scope', forbidden)
    monkeypatch.setattr(selection, 'evaluate_evidence', forbidden)
    result = selection.recommend_from_receipt(changed, receipt, root=root, mandatory=mandatory)
    assert result['mode'] == 'FULL_SUITE'
    assert result['qualification'] == 'NOT_QUALIFIED_LOCAL_TRACE_ONLY'


@pytest.mark.parametrize('missing', ['evidence', 'current', 'verifier'])
def test_incomplete_c5_skips_scope_and_validation(gate, monkeypatch, missing):
    def forbidden(*a, **kw):
        pytest.fail('Incomplete C5 must not build scope or validate evidence')
    monkeypatch.setattr(selection, 'build_x5_scope', forbidden)
    monkeypatch.setattr(selection, 'evaluate_evidence', forbidden)
    root, changed, receipt, mandatory, record, current = gate
    result = selection.recommend_from_receipt(
        changed, receipt, root=root, mandatory=mandatory,
        c5_evidence=None if missing == 'evidence' else record,
        c5_current=None if missing == 'current' else current,
        attestation_verifier=None if missing == 'verifier' else verified)
    assert result['mode'] == 'FULL_SUITE'


@pytest.mark.parametrize('claim', ['digest', 'boolean', 'inline', 'both'])
def test_invalid_or_legacy_scope_claim(gate, claim):
    record = gate[4]
    if claim == 'digest':
        record['x5_scope_sha256'] = '0' * 64
    elif claim == 'boolean':
        record['x5_scope_sha256'] = True
    else:
        record['x5_scope'] = build_x5_scope(
            gate[0], gate[1], gate[2], sorted(gate[2]['tests'][:3]), gate[3])
        if claim == 'inline':
            record.pop('x5_scope_sha256')
    result = recommend(gate)
    assert result['mode'] == 'FULL_SUITE'
    assert result['qualification'] == 'NOT_QUALIFIED_LOCAL_TRACE_ONLY'
    assert 'C5 evidence does not qualify for this exact target scope' in result['fallback_reasons']


def exploding(record):
    raise RuntimeError('owner unavailable')


@pytest.mark.parametrize('verifier', [None, exploding, lambda r: True,
    lambda r: AttestationVerdict('VERIFIED', '0' * 64, r['attestation_ref'], 'synthetic')])
def test_bad_verifiers(gate, verifier):
    assert recommend(gate, verifier)['mode'] == 'FULL_SUITE'


@pytest.mark.parametrize('field', ['capture_sha256', 'project', 'changed', 'selected', 'mandatory'])
def test_scope_tampering(gate, field):
    scope = build_x5_scope(gate[0], gate[1], gate[2], sorted(gate[2]['tests'][:3]), gate[3])
    scope[field] = 'tampered' if isinstance(scope[field], str) else []
    gate[4]['x5_scope_sha256'] = scope_digest(scope)
    result = recommend(gate)
    assert result['mode'] == 'FULL_SUITE'
    assert result['qualification'] == 'NOT_QUALIFIED_LOCAL_TRACE_ONLY'
    assert 'C5 evidence does not qualify for this exact target scope' in result['fallback_reasons']


@pytest.mark.parametrize('field,value', [('relation', 'DECLARED_VERIFIES'),
    ('outcome', 'FAILED'), ('dependencies', {'complete': False, 'hashes': {}})])
def test_unqualified_c5(gate, field, value):
    gate[4][field] = value
    assert recommend(gate)['mode'] == 'FULL_SUITE'


@pytest.mark.parametrize('field', ['environment_sha256', 'config_sha256', 'dependency_hashes',
    'artifact_hashes', 'snapshot', 'source_hashes', 'criterion_hashes'])
def test_changed_owner_observations(gate, field):
    current = gate[5]
    if field == 'snapshot':
        current[field]['content_hash'] = '0' * 64
    elif isinstance(current[field], dict):
        current[field][next(iter(current[field]))] = '0' * 64
    else:
        current[field] = '0' * 64
    assert recommend(gate)['mode'] == 'FULL_SUITE'


@pytest.mark.parametrize('field', ['tests', 'symbols'])
def test_coverage_gaps(gate, field):
    gate[4]['coverage'][field] = []
    assert recommend(gate)['mode'] == 'FULL_SUITE'


def test_baseline_dependency_is_not_target_execution(gate):
    root, _, receipt, _, record, current = gate
    key = f'{root.as_posix()}:src/code.py'
    baseline = receipt['fingerprints']['src/code.py']
    record['dependencies']['hashes'][key] = baseline
    current['dependency_hashes'][key] = baseline
    assert recommend(gate)['mode'] == 'FULL_SUITE'


def test_core_full_suite(gate, monkeypatch):
    monkeypatch.setattr(selection, 'symbol_impact', lambda *a: {
        'engine': 'synthetic', 'tests': [], 'files': {'tests/test_static.py'}, 'full_suite': True})
    assert recommend(gate)['mode'] == 'FULL_SUITE'


def test_static_can_close_trace_gap(gate):
    gate[2]['executed_blocks'][gate[2]['tests'][0]] = []
    selected = sorted(gate[2]['tests'][1:3])
    gate[4]['x5_scope_sha256'] = scope_digest(build_x5_scope(gate[0], gate[1], gate[2], selected, gate[3]))
    result = recommend(gate)
    assert result['mode'] == 'SELECTIVE_SCOPED_C5'
    assert result['unproven_blocks'] == ['src/code.py:1']
    assert result['tests'] == selected


def test_true_static_gap_stays_full(gate, monkeypatch):
    gate[2]['executed_blocks'][gate[2]['tests'][0]] = []
    monkeypatch.setattr(selection, 'static_test_files', lambda *a, **kw: {})
    gate[4]['x5_scope_sha256'] = scope_digest(build_x5_scope(gate[0], gate[1], gate[2], gate[3], gate[3]))
    assert recommend(gate)['mode'] == 'FULL_SUITE'


@pytest.mark.parametrize('mutation', ['capture', 'new', 'removed', 'unlisted', 'config',
    'environment', 'mandatory', 'empty', 'path', 'line'])
def test_capture_and_scope_fail_closed(gate, mutation):
    root, changed, receipt, mandatory, _, _ = gate
    if mutation == 'capture':
        receipt['suite_returncode'] = 1
    elif mutation == 'new':
        (root / 'new.py').write_text('x = 1\n')
    elif mutation == 'removed':
        (root / 'tests/test_other.py').unlink()
    elif mutation == 'unlisted':
        (root / 'tests/test_other.py').write_text('x = 2\n')
    elif mutation == 'config':
        (root / 'pyproject.toml').write_text('[tool.changed]\n')
        changed.append('pyproject.toml:1')
    elif mutation == 'environment':
        receipt['environment'] = {'forged': True}
    elif mutation == 'mandatory':
        mandatory.append('tests/test_unknown.py::test_unknown')
    elif mutation == 'empty':
        changed.clear()
    elif mutation == 'path':
        changed[:] = ['../outside.py:1']
    elif mutation == 'line':
        changed[:] = ['src/code.py:999']
    assert recommend(gate)['mode'] == 'FULL_SUITE'
