"""Decision falsifiers; the pilot may never promote partial or unmetered runs."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import qualify_new_task_pilot as pilot


def rows():
    return [{'task': t, 'arm': a, 'success': True, 'whole_wall_ms': 100 if a == 'full' else 90,
             'calls': [{'usage': {'total_tokens': 100 if a == 'full' else 90}}]} for t, a in pilot.ORDER]


def test_exact_four_cells_and_each_pair_required():
    rs = rows()
    assert pilot.decide(rs)['benefit'] == 'OBSERVED_IN_TWO_PAIRS'
    assert pilot.decide(rs[:-1])['status'] == 'INCOMPLETE'
    assert pilot.decide(rs[:-1] + [rs[0]])['status'] == 'INCOMPLETE'
    rs[1]['whole_wall_ms'] = 101
    assert pilot.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'


@pytest.mark.parametrize('key', ['offline', 'provider_changed', 'global_analytics_changed', 'extra_requests', 'interventions'])
def test_no_promotion_of_drift_or_offline(key):
    rs = rows()
    rs[0][key] = True
    assert pilot.decide(rs)['status'] == 'REJECT_PILOT'
    assert pilot.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'


def test_missing_metrics_and_wrong_repairs_block_benefit():
    rs = rows()
    rs[0]['calls'][0]['usage'] = None
    assert pilot.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'
    rs = rows()
    rs[1]['success'] = False
    assert pilot.decide(rs)['status'] == 'REJECT_PILOT'
    assert pilot.decide(rows())['automatic_roles'] == []
    assert pilot.decide(rows())['sustained_benefit'] is False


def test_native_offline_probe_fits_large_function_and_preserves_code():
    source = ('def large(\n    value=1,\n):\n' + '    # padding\n' * 2000 + '    return value\n').encode()
    task = {'path': 'large.py', 'target_range': [1, len(source.splitlines())]}
    text = pilot.native.offline_probe_edit(source, task)
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]}
    assert len(text.encode()) < 512
    assert pilot.native.permitted_edit(response, source, task) == b'# native transport preflight\n' + source
