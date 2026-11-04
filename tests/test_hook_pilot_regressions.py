"""Product regressions, including identity preservation found after the pilot."""
from pathlib import Path
import sys

import pytest

from specgraph.hook_context import context_hint
from test_hook_context import indexed

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from host_pilot_oracles import evaluate, TASKS


@pytest.mark.parametrize('task', TASKS)
def test_product_satisfies_frozen_behavioral_oracles(task):
    """@verifies SPEC-SPECGRAPH-NEW-TASK-PILOT:AC-02
    @verifies SPEC-SPECGRAPH-NEW-TASK-PILOT:AC-03
    """
    report = evaluate(Path(__file__).resolve().parents[1] / 'src/specgraph/hook_context.py', task)
    assert report['passed'], report


@pytest.mark.parametrize('spec_id', [None, 'SPEC-ONE'])
def test_missing_symbol_retains_requested_identity(indexed, spec_id):
    root, state = indexed
    result = context_hint(root, role='implementer', event='pre', symbol_id='src/lib.py:missing',
        spec_id=spec_id, work_id='WORK', source_ref='explicit:source', state_dir=state, timeout_ms=1000)
    assert result['status'] == 'MISS'
    assert result['symbol_id'] == 'src/lib.py:missing'
    assert result['work_id'] == 'WORK' and result['source_ref'] == 'explicit:source'


def test_dirty_symlink_cannot_escape_root(indexed, tmp_path):
    root, state = indexed
    other = tmp_path / 'outside.py'
    other.write_text('def outside(): pass\n')
    alias = root / 'alias.py'
    try:
        alias.symlink_to(other)
    except OSError:
        pytest.skip('host does not permit symlink creation')
    result = context_hint(root, role='implementer', event='pre', dirty_paths=(str(alias),),
                          state_dir=state, timeout_ms=1000)
    assert result['status'] == 'OUTSIDE_SCOPE'


@pytest.mark.parametrize('arm', ['full', 'focused'])
def test_reusable_oracle_rejects_archived_identity_loss(arm):
    root = Path(__file__).resolve().parents[1]
    candidate = (root / 'tests/fixtures/historical/identity-loss' / f'{arm}.py')
    report = evaluate(candidate, 'missing-symbol-with-spec')
    assert not report['passed'], report
    assert any('identity' in row['check'] and not row['passed'] for row in report['checks'])


@pytest.mark.parametrize('field', ['symbol_id', 'root', 'work_id', 'source_ref'])
def test_reusable_oracle_rejects_missing_identity_field(tmp_path, field):
    candidate = tmp_path / 'candidate.py'
    candidate.write_text(
        'from specgraph.hook_context import context_hint as original\n'
        'def context_hint(*args, **kwargs):\n'
        '    result = original(*args, **kwargs)\n'
        '    if result["status"] == "MISS":\n'
        f'        result.pop({field!r}, None)\n'
        '    return result\n', encoding='utf-8')
    report = evaluate(candidate, 'missing-symbol-with-spec')
    assert not report['passed'], report
    assert any('identity' in row['check'] and not row['passed'] for row in report['checks'])
