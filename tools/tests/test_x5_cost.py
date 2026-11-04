"""Offline cost accounting controls; synthetic timings are not operational evidence."""
import math
import runpy
from pathlib import Path

import pytest

import specgraph.test_selection as selection
from tools.x5_campaign import DELIVERY_COMPONENTS, assess, delivery_cost


def _report():
    return {'capture_seconds': 3, 'correctness_preserved': True,
            'policy_correctness_preserved': True,
            'mutations': [{'full_seconds': 20, 'selection_seconds': 1,
                           'selected_seconds': 2, 'policy_seconds': 2, 'policy_omitted': []}]}


def test_legacy_projection_is_not_observed_delivery():
    result = assess(_report())
    assert result['projection_label'] == 'PROJECTION_NOT_OPERATIONAL_SAVINGS'
    assert result['net_saved_seconds'] == 14
    observed = result['observed_qualification_cost']
    assert observed['total_seconds'] is None
    assert observed['phases']['confirmation']['seconds'] is None
    assert observed['coverage']['wall_seconds'] == 'UNKNOWN'
    assert result['delivery_cost']['status'] == 'UNKNOWN'
    assert all(v is None for v in result['delivery_cost']['components_seconds'].values())
    assert result['delivery_cost']['net_operational_savings'] is None
    assert result['selective_execution_qualified'] is False


@pytest.mark.parametrize('components', [None, {}])
def test_absent_delivery_components_preserve_unknown(components):
    value = delivery_cost(components, baseline_seconds=20, baseline_comparable=True)
    assert value['status'] == 'UNKNOWN'
    assert value['total_seconds'] is None
    assert value['net_operational_savings'] is None
    assert all(v is None for v in value['components_seconds'].values())
    assert all(v == 'UNKNOWN' for v in value['coverage'].values())


@pytest.mark.parametrize('components', [[], [0], True, False, '', 'capture', 0, 1, 0.0, (), object()])
def test_invalid_delivery_component_containers_are_rejected(components):
    with pytest.raises(ValueError):
        delivery_cost(components, baseline_seconds=20, baseline_comparable=True)


@pytest.mark.parametrize('extra_cost', [200, 0])
def test_unknown_delivery_component_is_rejected(extra_cost):
    costs = dict.fromkeys(DELIVERY_COMPONENTS, 0)
    costs['full_audits'] = extra_cost
    with pytest.raises(ValueError):
        delivery_cost(costs, baseline_seconds=20, baseline_comparable=True)


@pytest.mark.parametrize('component', DELIVERY_COMPONENTS)
def test_each_missing_component_preserves_unknown(component):
    costs = dict.fromkeys(DELIVERY_COMPONENTS, 0)
    costs[component] = None
    value = delivery_cost(costs, baseline_seconds=20, baseline_comparable=True)
    assert value['total_seconds'] is None
    assert value['coverage'][component] == 'UNKNOWN'
    assert value['net_operational_savings'] is None


@pytest.mark.parametrize('component', ['recapture', 'invalidation', 'retry', 'correction', 'audit'])
def test_explicit_lifecycle_costs_eliminate_apparent_gain(component):
    costs = dict.fromkeys(DELIVERY_COMPONENTS, 0)
    costs.update(capture=1, selection=1, execution=1)
    assert delivery_cost(costs, baseline_seconds=10, baseline_comparable=True)['net_operational_savings'] == 7
    assert delivery_cost(costs, baseline_seconds=10)['net_operational_savings'] is None
    costs[component] = 8
    value = delivery_cost(costs, baseline_seconds=10, baseline_comparable=True)
    assert value['total_seconds'] == 11
    assert value['net_operational_savings'] is None


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), True, False, -1])
@pytest.mark.parametrize('component', DELIVERY_COMPONENTS)
def test_invalid_delivery_costs_are_rejected(component, bad):
    with pytest.raises(ValueError):
        delivery_cost({component: bad})
    with pytest.raises(ValueError):
        delivery_cost(baseline_seconds=bad)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, -1])
@pytest.mark.parametrize('field', ['capture_seconds', 'full_seconds', 'selection_seconds', 'selected_seconds', 'policy_seconds'])
def test_invalid_projection_costs_are_rejected(field, bad):
    report = _report()
    if field == 'capture_seconds':
        report[field] = bad
    else:
        report['mutations'][0][field] = bad
    with pytest.raises(ValueError):
        assess(report)


@pytest.mark.parametrize('policy_mode', ['FULL_SUITE', 'TRACE', 'COMBINED'])
def test_compare_records_real_calls_only(tmp_path, monkeypatch, policy_mode):
    (tmp_path / 'sample.py').write_text('value = 1', encoding='utf-8')
    recommendation = {'candidates': ['test_a'], 'candidate_command': ['python', '-m', 'pytest', 'test_a'],
                      'full_suite_command': ['python', '-m', 'pytest', 'tests'],
                      'policy': policy_mode, 'policy_tests': ['test_a'] if policy_mode == 'TRACE' else ['test_a', 'test_b'],
                      'static_candidates': [], 'mode': 'FULL_SUITE', 'unproven_blocks': [], 'selection_seconds': 0}
    monkeypatch.setattr(selection, 'recommend_from_receipt', lambda *a, **k: recommendation)
    calls = []

    def run(root, tests):
        calls.append(list(tests))
        failed = ['test_a'] if tests == ['test_a'] else ['test_a', 'test_b']
        return {'seconds': 2, 'failed': failed, 'tests': list(tests), 'output_bytes': 0}

    monkeypatch.setattr(selection, '_run', run)
    mutation = {'path': 'sample.py', 'find': 'value = 1', 'replace': 'value = 2'}
    report = selection.compare(tmp_path, {'suite_returncode': 0, 'capture_seconds': 3}, [mutation, mutation])
    for row in report['mutations']:
        cost = row['observed_cost']
        counts = cost['phase_counts']
        assert counts['full_audit'] == counts['trace_candidate'] == 1
        assert counts['policy_candidate'] == (1 if policy_mode == 'COMBINED' else 0)
        assert counts['confirmation'] == (2 if policy_mode == 'TRACE' else 1)
        assert cost['execution_count'] == sum(counts.values())
        assert cost['command_seconds'] == 2 * cost['execution_count']
        assert all(math.isfinite(cost[k]) and cost[k] >= 0 for k in
                   ('wall_seconds', 'setup_seconds', 'cleanup_seconds', 'overhead_seconds', 'selection_seconds'))
    observed = assess(report)['observed_qualification_cost']
    assert sum(r['observed_cost']['execution_count'] for r in report['mutations']) == len(calls)
    assert observed['total_seconds'] == 3 + sum(r['observed_cost']['wall_seconds'] for r in report['mutations'])
    assert observed['case_totals_seconds']['command_seconds'] == 2 * len(calls)


def test_small_real_compare_uses_existing_fixture(tmp_path):
    fixture_file = Path(__file__).resolve().parents[2] / 'tests/test_test_selection.py'
    project = runpy.run_path(str(fixture_file))['_project'](tmp_path)
    receipt = selection.capture(project, project / 'trace.json')
    mutation = {'path': 'src/specgraph/calc.py', 'find': 'return a + b', 'replace': 'return a - b'}
    report = selection.compare(project, receipt, [mutation])
    cost = report['mutations'][0]['observed_cost']
    assert cost['phase_counts']['full_audit'] == 1
    assert cost['phase_counts']['trace_candidate'] == 1
    assert cost['phase_counts']['confirmation'] >= 1
    assert cost['command_seconds'] == sum(p['seconds'] for p in cost['phases'])
    accounted = sum(cost[k] for k in ('command_wall_seconds', 'selection_seconds',
                                    'setup_seconds', 'cleanup_seconds', 'overhead_seconds'))
    assert accounted == pytest.approx(cost['wall_seconds'])
    observed = assess(report)['observed_qualification_cost']
    assert observed['total_seconds'] == receipt['capture_seconds'] + cost['wall_seconds']
    assert observed['status'] == 'OBSERVED_QUALIFICATION_COST'
    assert assess(report)['delivery_cost']['status'] == 'UNKNOWN'


@pytest.mark.parametrize('field', ['command_seconds', 'command_wall_seconds', 'selection_seconds',
                                   'setup_seconds', 'cleanup_seconds', 'overhead_seconds', 'wall_seconds'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, -1])
def test_invalid_observed_costs_are_rejected(field, bad):
    report = _report()
    report['mutations'][0]['observed_cost'] = {field: bad}
    with pytest.raises(ValueError):
        assess(report)


def test_confirm_keeps_original_two_argument_api(tmp_path, monkeypatch):
    monkeypatch.setattr(selection, '_run', lambda *args: {'failed': ['test_a']})
    assert selection._confirm(tmp_path, {'test_a', 'test_b'}) == (['test_a'], ['test_b'])
    assert selection._confirm(tmp_path, set()) == ([], [])
