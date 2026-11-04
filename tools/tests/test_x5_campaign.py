import stat

import pytest

from tools.x5_campaign import assess, cohort, remove_owned_copy


def test_fast_candidates_do_not_hide_capture_cost_or_missed_failures():
    report = {'capture_seconds': 150, 'correctness_preserved': True, 'policy_correctness_preserved': True,
              'mutations': [{'full_seconds': 90, 'selection_seconds': 2, 'selected_seconds': 1,
                             'policy_seconds': 5, 'policy_omitted': []}]}
    result = assess(report)
    assert result['decision'] == 'REVISE_COST' and result['policy_decision'] == 'REVISE_COST'
    assert result['net_saved_seconds'] == -63
    assert result['projection_label'] == 'PROJECTION_NOT_OPERATIONAL_SAVINGS'
    assert result['observed_qualification_cost']['total_seconds'] is None
    assert result['delivery_cost']['net_operational_savings'] is None
    report['capture_seconds'] = 10
    assert assess(report)['decision'] == 'EXPAND_TO_REAL_CHANGES'
    assert assess(report)['policy_decision'] == 'READY_FOR_SCOPED_GATE'
    report['correctness_preserved'] = report['policy_correctness_preserved'] = False
    report['mutations'][0]['policy_omitted'] = ['tests/test_x.py::test_y']
    assert assess(report)['decision'] == assess(report)['policy_decision'] == 'REVISE_COVERAGE'
    assert assess(report)['policy_omission_cases'] == 1
    assert assess(report)['selective_execution_qualified'] is False


def test_context_bytes_are_capped_like_the_verify_tool():
    report = {'capture_seconds': 1, 'correctness_preserved': True, 'policy_correctness_preserved': True,
              'mutations': [{'full_seconds': 9, 'selection_seconds': 0, 'selected_seconds': 1, 'policy_seconds': 2,
                             'policy_omitted': [], 'output_bytes': {'full': 50000, 'selected': 900, 'policy': 3000}}]}
    result = assess(report)
    assert result['context_bytes_capped_16k'] == {'full': 16384, 'selected': 900, 'policy': 3000}
    assert result['truncated_full_outputs'] == 1


def test_cleanup_removes_readonly_git_file_and_rejects_external_path(tmp_path):
    lot = tmp_path / 'owned'
    clone = lot / 'repo'
    objects = clone / '.git' / 'objects' / 'pack'
    objects.mkdir(parents=True)
    index = objects / 'pack.idx'
    index.write_bytes(b'read-only git pack')
    index.chmod(stat.S_IREAD)
    outside = tmp_path / 'preserved'
    outside.mkdir()
    with pytest.raises(ValueError):
        remove_owned_copy(outside, lot)
    assert outside.exists()
    remove_owned_copy(clone, lot)
    assert not clone.exists()


def test_real_change_cohort_counts_distinct_commits_and_modules():
    cases = [{'commit': 'a', 'path': 'src/specgraph/graph.py'}, {'commit': 'a', 'path': 'src/specgraph/cli.py'},
             {'commit': 'b', 'path': 'src/specgraph/graph.py'}]
    assert cohort(cases) == {'cases': 3, 'commits': 2,
                             'modules': ['src/specgraph/cli.py', 'src/specgraph/graph.py']}


def test_main_wall_includes_cleanup_and_labels_projection(tmp_path, monkeypatch):
    import json
    import sys
    from pathlib import Path
    import tools.x5_campaign as campaign

    clock = [100.0]
    monkeypatch.setattr(campaign.time, 'perf_counter', lambda: clock[0])
    monkeypatch.setattr(sys, 'argv', ['x5_campaign', '--output', str(tmp_path / 'evidence')])
    monkeypatch.delenv('GITHUB_STEP_SUMMARY', raising=False)
    monkeypatch.setattr(campaign.subprocess, 'check_output', lambda *a, **k: 'revision\n')

    def fake_git(command, **kwargs):
        if 'clone' in command:
            fixture = Path(command[-1]) / 'tools/fixtures/x5'
            fixture.mkdir(parents=True)
            (fixture / 'x5-campaign-mutations.json').write_text('[]', encoding='utf-8')
            (fixture / 'x5-real-changes.json').write_text('[]', encoding='utf-8')

    monkeypatch.setattr(campaign.subprocess, 'run', fake_git)

    def fake_capture(root, destination):
        destination.write_text('{}', encoding='utf-8')
        clock[0] += 2
        return {'environment': {}, 'tests': ['test_a']}

    monkeypatch.setattr(campaign, 'capture', fake_capture)
    report = {'capture_seconds': 2, 'correctness_preserved': True,
              'policy_correctness_preserved': True,
              'mutations': [{'full_seconds': 10, 'selection_seconds': 0,
                             'selected_seconds': 1, 'policy_seconds': 1, 'policy_omitted': []}]}

    def fake_compare(*args):
        clock[0] += 3
        return report

    monkeypatch.setattr(campaign, 'compare', fake_compare)
    cleanup = campaign.remove_owned_copy

    def observed_cleanup(copy, temporary_root):
        cleanup(copy, temporary_root)
        clock[0] += 7

    monkeypatch.setattr(campaign, 'remove_owned_copy', observed_cleanup)
    assert campaign.main() == 0
    value = json.loads((tmp_path / 'evidence/result.json').read_text(encoding='utf-8'))
    assert value['elapsed_seconds'] == 12
    assert value['assessment']['selective_execution_qualified'] is False
    assert value['assessment']['delivery_cost']['status'] == 'UNKNOWN'
    summary = (tmp_path / 'evidence/summary.md').read_text(encoding='utf-8')
    assert 'Campaign wall (elapsed_seconds): 12.00s' in summary
    assert 'PROJECTION_NOT_OPERATIONAL_SAVINGS' in summary
    assert 'No operational savings established' in summary
