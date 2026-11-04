"""Falsifiers for the new pilot's evaluator and decision boundary, no inference."""
import ast
import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from distinct_task_oracles import evaluate, TASKS
from qualify_distinct_task_pilot import decide, ORDER, SELECTIONS


def original_source(task):
    name = Path(SELECTIONS[task][0]).name
    return (ROOT / 'tests/fixtures/historical/distinct' / (name + '.txt')).read_text()


@pytest.mark.parametrize('task,failed_count', [(TASKS[0], 6), (TASKS[1], 3)])
def test_original_source_has_new_defect_and_preserves_existing_behavior(tmp_path, task, failed_count):
    candidate = tmp_path / 'original.py'
    candidate.write_text(original_source(task))
    report = evaluate(candidate, task)
    failed = [row for row in report['checks'] if not row['passed']]
    assert not report['passed'] and len(failed) == failed_count
    assert all(row['kind'] == 'regression' for row in failed)


@pytest.mark.parametrize('task', TASKS)
def test_oracle_rejects_always_refuse_candidate(tmp_path, task):
    relative, symbol = SELECTIONS[task]
    source = original_source(task)
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == symbol)
    replacement = ({'status': 'BUSY', 'reconciliation': {'acknowledged': False}}
                   if task == TASKS[0] else {'status': 'SOURCE_INVALID', 'documents': []})
    lines = source.splitlines(keepends=True)
    # Keep the declaration and imports; deliberately refuse every valid request.
    body = node.body[0].lineno - 1
    candidate = tmp_path / 'candidate.py'
    candidate.write_text(''.join(lines[:body]) + f'    return {replacement!r}\n' + ''.join(lines[node.end_lineno:]))
    report = evaluate(candidate, task)
    assert not report['passed']
    assert any(row['kind'] == 'preservation' and not row['passed'] for row in report['checks'])


def records():
    return [{'task': task, 'arm': arm, 'success': True, 'whole_wall_ms': 10 if arm == 'full' else 9,
             'calls': [{'usage': {'total_tokens': 100 if arm == 'full' else 90}}]}
            for task, arm in ORDER]


def test_decision_requires_all_four_correct_and_measured_without_drift():
    assert decide(records())['benefit'] == 'OBSERVED_IN_TWO_PAIRS'
    for mutation in ({'success': False}, {'calls': [{'usage': None}]}, {'extra_requests': 1},
                     {'provider_changed': True}, {'interventions': 1}, {'whole_wall_ms': None}):
        changed = copy.deepcopy(records())
        changed[1].update(mutation)
        assert decide(changed)['benefit'] == 'NOT_DEMONSTRATED'
    for changed in ([], records()[:2], [records()[0]] * 4):
        assert decide(changed)['status'] == 'INCOMPLETE'
        assert decide(changed)['benefit'] == 'NOT_DEMONSTRATED'


@pytest.mark.parametrize('task', TASKS)
def test_reviewed_product_passes_both_complete_oracles(task):
    report = evaluate(ROOT / SELECTIONS[task][0], task)
    assert report['passed'], [row for row in report['checks'] if not row['passed']]


def test_oracle_rejects_lease_checked_against_pre_lock_time(tmp_path):
    source = original_source(TASKS[0])
    source = source.replace(
        'owned = state.get("owner") == owner and state["journal_id"] == journal_id',
        'owned = (state.get("owner") == owner and state["journal_id"] == journal_id\n'
        '                 and state.get("lease_until", 0.0) > finished)')
    candidate = tmp_path / 'pre_lock_clock.py'
    candidate.write_text(source)
    report = evaluate(candidate, TASKS[0])
    failed = [row['check'] for row in report['checks'] if not row['passed']]
    assert failed == ['lease-expires-while-final-lock-is-acquired-no-ack',
                      'lease-expires-while-final-lock-is-acquired-preserves-observation']
