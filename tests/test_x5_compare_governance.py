"""Bounded compare falsifiers; synthetic costs are not operational savings."""
import math
from pathlib import Path

import pytest
import specgraph.test_selection as selection


def _fixture(tmp_path, monkeypatch, *, unstable=False, full_seconds=4):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'sample.py').write_text('value = 1\n', encoding='utf-8')
    for name in ('.git', '.pytest_cache', '__pycache__', '.venv', '.specgraph', 'target'):
        (root / name).mkdir()
        (root / name / 'sentinel').write_text('excluded', encoding='utf-8')
    (root / 'kept').mkdir()
    (root / 'kept/sentinel').write_text('preserved', encoding='utf-8')
    recommendation = dict(candidates=['chosen'], candidate_command=['python', '-m', 'pytest', 'chosen'],
                          full_suite_command=['python', '-m', 'pytest', 'tests'], policy='TRACE',
                          policy_tests=['chosen'], static_candidates=[], mode='FULL_SUITE',
                          unproven_blocks=[], selection_seconds=0.5)
    copies, calls = [], []

    def recommend(changed, receipt, *, root: Path, mandatory):
        assert changed == ['sample.py:1']
        assert (root / 'sample.py').read_text(encoding='utf-8') == 'value = 2\n'
        assert (root / 'kept/sentinel').read_text(encoding='utf-8') == 'preserved'
        assert not any((root / name).exists() for name in
                       ('.git', '.pytest_cache', '__pycache__', '.venv', '.specgraph', 'target'))
        copies.append(root)
        return recommendation

    def run(directory, tests):
        assert directory == copies[-1] and directory.resolve() != root.resolve()
        calls.append(list(tests))
        failed = ['chosen', 'missed'] if tests == ['tests'] else (
            [] if unstable and tests == ['missed'] else list(tests))
        return dict(seconds=full_seconds if tests == ['tests'] else 1,
                    failed=failed, tests=list(tests), output_bytes=7)

    monkeypatch.setattr(selection, 'recommend_from_receipt', recommend)
    monkeypatch.setattr(selection, '_run', run)
    return root, recommendation, copies, calls


@pytest.mark.parametrize('path', ['sample.py', './sample.py'])
@pytest.mark.parametrize('unstable', [False, True])
def test_compare_confirms_missed_failure_and_preserves_copy(tmp_path, monkeypatch, unstable, path):
    """@verifies SPEC-SPECGRAPH-X5 [AC-02, AC-04]"""
    root, recommendation, copies, calls = _fixture(tmp_path, monkeypatch, unstable=unstable)
    report = selection.compare(root, dict(suite_returncode=0, capture_seconds=3),
                               [dict(path=path, find='value = 1', replace='value = 2')])
    row = report['mutations'][0]
    assert row['mutation'] == 'sample.py'
    assert row['changed'] == ['sample.py:1']
    assert calls == [['tests'], ['chosen'], ['missed'], ['missed']]
    assert row['omitted_relevant'] == ([] if unstable else ['missed'])
    assert row['policy_omitted'] == row['omitted_relevant']
    assert row['unstable_not_omitted'] == (['missed'] if unstable else [])
    assert row['policy_unstable'] == row['unstable_not_omitted']
    assert report['qualification'] == ('NOT_QUALIFIED_LOCAL_TRACE_ONLY' if unstable
                                        else 'SUSPENDED_MISSED_RELEVANT_TEST')
    assert report['correctness_preserved'] is unstable
    assert report['policy_correctness_preserved'] is unstable
    assert report['target_ratio'] == 0.30
    assert report['median_candidate_ratio'] == (0.5 + 1) / 4
    cost = row['observed_cost']
    assert [p['phase'] for p in cost['phases']] == [
        'full_audit', 'trace_candidate', 'confirmation', 'confirmation']
    assert cost['execution_count'] == 4 and cost['command_seconds'] == 7
    assert cost['phase_counts'] == dict(full_audit=1, trace_candidate=1, policy_candidate=0, confirmation=2)
    for key in ('command_seconds', 'command_wall_seconds', 'selection_seconds', 'setup_seconds',
                'cleanup_seconds', 'overhead_seconds', 'wall_seconds'):
        assert math.isfinite(cost[key]) and cost[key] >= 0
    assert sum(cost[k] for k in ('command_wall_seconds', 'selection_seconds', 'setup_seconds',
                                'cleanup_seconds', 'overhead_seconds')) == pytest.approx(cost['wall_seconds'])
    assert all(math.isfinite(p[k]) and p[k] >= 0 for p in cost['phases'] for k in ('seconds', 'wall_seconds'))
    assert not copies[0].exists()
    assert (root / 'sample.py').read_text(encoding='utf-8') == 'value = 1\n'
    assert (root / '.git/sentinel').read_text(encoding='utf-8') == 'excluded'


@pytest.mark.parametrize('field', ['capture', 'selection', 'execution'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), -1, True, False, None, '1'])
def test_compare_refuses_invalid_costs(tmp_path, monkeypatch, field, bad):
    """@verifies SPEC-SPECGRAPH-X5 [AC-04]"""
    root, recommendation, copies, calls = _fixture(tmp_path, monkeypatch)
    receipt = dict(suite_returncode=0, capture_seconds=3)
    if field == 'capture':
        receipt['capture_seconds'] = bad
    elif field == 'selection':
        recommendation['selection_seconds'] = bad
    else:
        monkeypatch.setattr(selection, '_run', lambda *a: dict(seconds=bad, failed=[], tests=[], output_bytes=0))
    with pytest.raises(ValueError, match='finite and nonnegative'):
        selection.compare(root, receipt, [dict(path='sample.py', find='value = 1', replace='value = 2')])
    assert all(not copy.exists() for copy in copies)
    assert (root / 'sample.py').read_text(encoding='utf-8') == 'value = 1\n'


@pytest.mark.parametrize('find', ['', 'value', 'absent'])
def test_compare_refuses_nonunique_find(tmp_path, monkeypatch, find):
    """@verifies SPEC-SPECGRAPH-X5 [AC-02]"""
    (tmp_path / 'sample.py').write_text('value = 1\nvalue = 1\n', encoding='utf-8')
    monkeypatch.setattr(selection, '_run', lambda *a: pytest.fail('runner must not execute'))
    with pytest.raises(ValueError, match='find must occur exactly once'):
        selection.compare(tmp_path, dict(suite_returncode=0, capture_seconds=0),
                          [dict(path='sample.py', find=find, replace='changed')])
    assert (tmp_path / 'sample.py').read_text(encoding='utf-8') == 'value = 1\nvalue = 1\n'


@pytest.mark.parametrize('path', [
    '../outside.py', '..\\outside.py', '/outside.py', 'C:/outside.py',
    './C:/outside.py', '././C:/outside.py', 'C:outside.py', './C:outside.py',
    'C:\\outside.py', '.\\C:\\outside.py', '.\\C:outside.py',
    '//server/share/outside.py', '\\\\server\\share\\outside.py',
    './\\\\server\\share\\outside.py',
    '././C:outside.py', '././C:\\outside.py', '.\\.\\C:outside.py',
    './//server/share/outside.py', '././//server/share/outside.py',
    '././\\\\server\\share\\outside.py', '.\\\\server\\share\\outside.py',
])
def test_compare_refuses_escaping_path(tmp_path, monkeypatch, path):
    """@verifies SPEC-SPECGRAPH-X5 [AC-02]"""
    monkeypatch.setattr(selection.shutil, 'copytree', lambda *a, **k: pytest.fail('copy must not begin'))
    with pytest.raises(ValueError, match='repository-relative path'):
        selection.compare(tmp_path, dict(suite_returncode=0, capture_seconds=0),
                          [dict(path=path, find='x', replace='y')])


def test_compare_zero_baseline_cost_has_unknown_ratio(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5 [AC-04]"""
    root, _, _, _ = _fixture(tmp_path, monkeypatch, unstable=True, full_seconds=0)
    report = selection.compare(root, dict(suite_returncode=0, capture_seconds=0),
                               [dict(path='sample.py', find='value = 1', replace='value = 2')])
    assert report['median_candidate_ratio'] is None
    assert report['qualification'] == 'NOT_QUALIFIED_LOCAL_TRACE_ONLY'
