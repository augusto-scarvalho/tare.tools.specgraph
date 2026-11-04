"""Behavioral discrimination and paired accounting guardrails."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tools')]
from economy_oracles import TASKS, SELECTIONS, evaluate
from economy_controls import variants
from qualify_economy_pilot import ORDER, decide, direct_packet

@pytest.mark.parametrize('task', TASKS)
def test_new_defect_and_hidden_controls(tmp_path, task):
    frozen = ROOT / 'tests/fixtures/historical/economy/frozen/prepared' / task
    metadata = json.loads((frozen / 'task.json').read_text(encoding='utf-8'))
    original = frozen / 'corpus' / SELECTIONS[task][0]
    assert hashlib.sha256(original.read_bytes()).hexdigest() == metadata['source_sha256']
    baseline = evaluate(original, task)
    assert not baseline['passed']
    assert all(r['kind'] == 'regression' for r in baseline['checks'] if not r['passed'])
    for name, body in variants(original.read_text(encoding='utf-8'), task).items():
        candidate = tmp_path / (name + '.py')
        candidate.write_bytes(body.encode())
        result = evaluate(candidate, task)
        assert result['passed'] is (name == 'reference'), result


def test_pair_decision_requires_complete_quality_and_counters():
    records = [{'round': n, 'task': t, 'arm': a, 'success': True, 'whole_wall_ms': 10 if a == 'full' else 9,
                'calls': [{'usage': {'total_tokens': 100 if a == 'full' else 50}}]} for n,t,a in ORDER]
    assert decide(records)['token_benefit'] == 'OBSERVED_IN_ALL_SIX_PAIRS'
    assert decide(records[:-1])['status'] == 'INCOMPLETE'
    assert decide(records[:-1] + [records[0]])['status'] == 'INCOMPLETE'
    for field, value in [('success', False), ('extra_requests', 1), ('provider_changed', True)]:
        changed = copy.deepcopy(records); changed[0][field] = value
        assert decide(changed)['status'] == 'REJECT_PILOT'
        assert decide(changed)['token_benefit'] == 'NOT_DEMONSTRATED'
    missing = copy.deepcopy(records); missing[0]['calls'][0]['usage'] = None
    assert decide(missing)['token_benefit'] == 'NOT_DEMONSTRATED'
    slow = copy.deepcopy(records)
    next(r for r in slow if r['arm'] == 'focused')['whole_wall_ms'] = 11
    assert decide(slow)['time_benefit'] == 'NOT_DEMONSTRATED'
