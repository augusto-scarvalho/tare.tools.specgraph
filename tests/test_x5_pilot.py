"""Synthetic structural advice, not delivery qualification."""
import sys

import pytest

from specgraph import test_selection as selection


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    files = {'src/code.py': 'value = 1\n',
             'tests/test_code.py': 'def test_code(): pass\n',
             'pyproject.toml': '[tool.example]\n'}
    for path, text in files.items():
        dest = tmp_path / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding='utf-8')
    monkeypatch.setattr(selection, '_environment', lambda: {'synthetic': True})
    monkeypatch.setattr(selection, 'symbol_impact', lambda *a, **kw: None)
    monkeypatch.setattr(selection, 'static_test_files', lambda *a, **kw: {})
    def forbidden(*a, **kw):
        pytest.fail('No commands or C5 composition for local advice')
    monkeypatch.setattr(selection.subprocess, 'run', forbidden)
    monkeypatch.setattr(selection, 'build_x5_scope', forbidden)
    node = 'tests/test_code.py::test_code'
    receipt = {'schema': selection.SCHEMA, 'tests': [node],
               'executed_blocks': {node: ['src/code.py:1']},
               'fingerprints': selection._files(tmp_path),
               'environment': {'synthetic': True}, 'suite_returncode': 0,
               'failed': [], 'pytest_args': ['tests']}
    (tmp_path / 'src/code.py').write_text('value = 2\n', encoding='utf-8')
    return tmp_path, receipt


def test_candidate_is_only_pre_execution_advice(pilot):
    root, receipt = pilot
    receipt.update(attestation='VERIFIED', dependency_closure='EXTERNALLY_VERIFIED')
    result = selection.recommend_from_receipt(['src/code.py:1'], receipt, root=root)
    advice = result['applicability']
    assert advice['status'] == 'CANDIDATE'
    assert advice['execution_authorized'] is advice['supports_success'] is False
    assert advice['dependency_closure'] == 'UNKNOWN'
    assert advice['reason_codes'] == []
    assert 'LOCAL_TRANSITIVE_CLOSURE_UNKNOWN' in advice['limitations']
    assert result['mode'] == 'FULL_SUITE'
    assert result['command'] == [sys.executable, '-m', 'pytest', 'tests']
    assert result['policy_tests'] == receipt['tests']


@pytest.mark.parametrize('case,code', [
    ('config', 'CONFIGURATION_CHANGE'), ('env', 'ENVIRONMENT_CHANGE'),
    ('add', 'ADDED_OR_DELETED_PATH'), ('delete', 'ADDED_OR_DELETED_PATH'),
    ('unlisted', 'UNLISTED_CHANGE'), ('mandatory', 'UNKNOWN_MANDATORY'),
    ('empty', 'EMPTY_CHANGES'), ('invalid', 'UNSAFE_PATH'),
    ('gap', 'IMPACT_GAP'), ('policy', 'EMPTY_POLICY'),
    ('truncated', 'INVALID_CAPTURE'), ('failed', 'INVALID_CAPTURE'),
])
def test_structural_fallbacks(pilot, case, code):
    root, receipt = pilot
    changed = ['src/code.py:1']
    mandatory = []
    if case == 'config':
        changed.append('pyproject.toml')
        (root / 'pyproject.toml').write_text('[tool.changed]\n')
    elif case == 'env':
        receipt['environment'] = {}
    elif case == 'add':
        (root / 'src/new.py').write_text('x = 1\n')
        changed.append('src/new.py')
    elif case == 'delete':
        (root / 'src/code.py').unlink()
    elif case == 'unlisted':
        (root / 'tests/test_code.py').write_text('def test_code(): assert False\n')
    elif case == 'mandatory':
        mandatory = ['tests/unknown.py::test_unknown']
    elif case == 'empty':
        changed = []
    elif case == 'invalid':
        changed = ['../src/code.py:1']
    elif case in {'gap', 'policy'}:
        receipt['executed_blocks'] = {t: [] for t in receipt['tests']}
    elif case == 'truncated':
        receipt['truncated'] = True
    elif case == 'failed':
        receipt['failed'] = receipt['tests'][:]
    result = selection.recommend_from_receipt(changed, receipt, root=root, mandatory=mandatory)
    assert result['mode'] == 'FULL_SUITE'
    assert result['applicability']['status'] == 'FALLBACK'
    assert code in result['applicability']['reason_codes']


@pytest.mark.parametrize('path', ['', '/absolute.py', 'src/./code.py',
                                'src//code.py', 'C:/code.py', 'src/code.py:0'])
def test_empty_and_noncanonical_changes(pilot, path):
    root, receipt = pilot
    result = selection.recommend_from_receipt([path], receipt, root=root)
    assert result['applicability']['status'] == 'FALLBACK'
