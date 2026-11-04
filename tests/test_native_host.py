"""Falsifiers for native edit scope, source projection and benefit claims."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import qualify_native_host as native


def response(text, finish='stop'):
    return {'choices': [{'finish_reason': finish, 'message': {'content': text}}]}


def edit(path='module.py', old='    return 1\n', new='    return 2\n'):
    return path + '\n```python\n<<<<<<< SEARCH\n' + old + '=======\n' + new + '>>>>>>> REPLACE\n```'


def test_gate_does_not_write_and_requires_exact_scope(tmp_path):
    source = b'def a():\n    return 1\n\ndef b():\n    return 3\n'
    task = {'path': 'module.py', 'target_range': [1, 2]}
    f = tmp_path / 'module.py'
    f.write_bytes(source)
    expected = native.permitted_edit(response(edit()), source, task)
    assert expected == source.replace(b'return 1', b'return 2')
    assert f.read_bytes() == source
    for proposal in [edit('../module.py'), edit('/module.py'), edit(old='    return 3\n'),
                     edit(new='    return 2\n\ndef injected():\n    pass\n')]:
        with pytest.raises(ValueError):
            native.permitted_edit(response(proposal), source, task)
    with pytest.raises(ValueError):
        native.permitted_edit(response(edit(), 'length'), source, task)


def rows():
    return [{'round': n, 'task': t, 'arm': a, 'success': True, 'total_wall_ms': 10 if a == 'full' else 9,
        'calls': [{'usage': {'total_tokens': 100 if a == 'full' else 80}}]}
        for n in (1, 2) for t in native.TASKS for a in native.ARMS]


def test_benefit_requires_each_round_and_exact_cells():
    rs = rows()
    assert native.decide(rs)['benefit'] == 'OBSERVED_IN_BOTH_LOCAL_ROUNDS'
    assert native.decide(rs[:-1])['status'] == 'INCOMPLETE'
    assert native.decide(rs[:-1] + [rs[0]])['status'] == 'INCOMPLETE'
    rs[-1]['total_wall_ms'] = 100
    assert native.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'


def test_absolute_native_path_must_resolve_to_same_authorized_file(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    source = b'def a():\n    return 1\n'
    target = root / 'module.py'
    target.write_bytes(source)
    task = {'path': 'module.py', 'target_range': [1, 2]}
    for spelling in ['module.py', str(target), './module.py']:
        assert native.permitted_edit(response(edit(spelling)), source, task, root) == source.replace(b'1', b'2')
    for spelling in [str(tmp_path / 'module.py'), '../module.py', 'other/module.py']:
        with pytest.raises(ValueError):
            native.permitted_edit(response(edit(spelling)), source, task, root)
    assert target.read_bytes() == source


def test_symlink_outside_authorized_root_is_rejected(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    other = tmp_path / 'outside.py'
    other.write_text('def a():\n    return 1\n')
    try:
        (root / 'module.py').symlink_to(other)
    except OSError:
        pytest.skip('host does not permit creating symlinks')
    with pytest.raises(ValueError):
        native.permitted_edit(response(edit()), other.read_bytes(),
            {'path': 'module.py', 'target_range': [1, 2]}, root)


@pytest.mark.parametrize('key', ['offline', 'provider_changed', 'global_analytics_changed', 'extra_requests', 'interventions'])
def test_drift_or_fixture_never_qualifies(key):
    rs = rows()
    rs[0][key] = True
    assert native.decide(rs)['status'] == 'REJECT_NATIVE_EDIT'
    assert native.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'


def test_missing_counters_and_failed_edits_never_claim_benefit():
    rs = rows()
    rs[0]['calls'][0]['usage'] = None
    assert native.decide(rs)['benefit'] == 'NOT_DEMONSTRATED'
    rs = rows()
    rs[-1]['success'] = False
    assert native.decide(rs)['status'] == 'REJECT_NATIVE_EDIT'


def test_projection_uses_published_source_and_exact_range(tmp_path):
    from specgraph.indexer import index_repository
    root = tmp_path / 'repo'
    root.mkdir()
    path = root / 'module.py'
    source = b'def target():\n    return 1\n\ndef unrelated():\n    return 2\n'
    path.write_bytes(source)
    (root / 'specgraph.yaml').write_text('project_name: test\nindexed_paths: [module.py]\ngoverned_paths: []\n')
    state = tmp_path / 'state'
    m = index_repository(root, state_dir=state)
    task = {'path': 'module.py', 'symbol': 'target', 'repo': 'test', 'base': 'a' * 40,
        'source_sha256': native.sha(source), 'snapshot': m['content_hash'], 'target_range': [1, 2]}
    path.unlink()  # No live-source fallback allowed.
    full = native.packet(task, root, state, 'full', tmp_path / 'edit')
    focused = native.packet(task, root, state, 'focused', tmp_path / 'edit')
    assert b'def unrelated' in full and b'def unrelated' not in focused
    assert b'def target' in focused
    assert json.loads(focused.splitlines()[0])['work_id'] == native.WORK
    for key, value in [('snapshot', 'bad'), ('source_sha256', 'bad'), ('target_range', [1, 3])]:
        bad = copy.deepcopy(task)
        bad[key] = value
        with pytest.raises(ValueError):
            native.packet(bad, root, state, 'focused', tmp_path / 'edit')
