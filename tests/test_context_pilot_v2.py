"""Regression tests for the methodological failures found in the frozen matrix."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'tools'))
import qualify_context_pilot as pilot
from pilot_process import run_process
from pilot_profiles import PROFILES, digest, identity_fingerprint, qwen_profile, request_parameters, validate_wire, validate_controls
from pilot_transport import aider_completion, observer, request_purpose, summarize_calls
from pilot_workspace import classify_changes, context_packet, export_checkout, inventory, validate_trial_paths


def test_thinking_has_separate_budget_correct_jinja_route_and_answer_reserve():
    profile = qwen_profile('test', thinking=True, effort='low', reasoning_tokens=512, answer_tokens=1024)
    wire = request_parameters(profile); wire.update(wire.pop('extra_body'))
    wire.update(model='qwen38', messages=[{'role': 'user', 'content': 'repair'}])
    validate_wire(wire, profile)
    assert wire['max_tokens'] >= 512 + 1024
    assert wire['chat_template_kwargs'] == {'enable_thinking': True, 'reasoning_effort': 'low'}
    assert wire['reasoning_budget_tokens'] == 512 and wire['cache_prompt'] is False and wire['seed'] == 0
    with pytest.raises(ValueError, match='inside'):
        validate_wire(wire | {'reasoning_effort': 'low'}, profile)
    with pytest.raises(ValueError, match='max_tokens'):
        validate_wire(wire | {'max_tokens': 512}, profile)
    with pytest.raises(ValueError):
        qwen_profile('bad', thinking=False, reasoning_tokens=512)


@pytest.mark.parametrize('timeout,parent_wait', [(5, 0), (.4, 8)])
def test_child_cannot_mutate_workspace_after_parent_exit_or_timeout(tmp_path, timeout, parent_wait):
    marker = tmp_path / 'escaped.txt'
    child = 'import time,pathlib; time.sleep(1.5); pathlib.Path(' + repr(str(marker)) + ').write_text("escaped")'
    script = ('import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) +
              '],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); time.sleep(' + str(parent_wait) + ')')
    result = run_process([sys.executable, '-c', script], cwd=tmp_path, dest=tmp_path / 'run', timeout=timeout)
    assert result['cleanup_verified']
    assert result['timeout'] == bool(parent_wait)
    if not parent_wait: assert result['exit_code'] == 0
    time.sleep(1.6)
    assert not marker.exists()


def test_cache_allowlist_does_not_excuse_modified_tracked_files_or_symlinks():
    before = {'src/unit.py': {'kind': 'file', 'sha256': 'old'}, '.pytest_cache/tracked': {'kind': 'file', 'sha256': 'old'}}
    after = {'src/unit.py': {'kind': 'file', 'sha256': 'new'}, '.pytest_cache/tracked': {'kind': 'file', 'sha256': 'new'},
             '.pytest_cache/new': {'kind': 'file', 'sha256': 'new'}, '.pytest_cache/link': {'kind': 'symlink', 'target': 'outside'}}
    result = classify_changes(before, after, ['src/unit.py'], ['.pytest_cache'])
    assert result['auxiliary_files'] == ['.pytest_cache/new']
    assert result['outside_scope'] == ['.pytest_cache/link', '.pytest_cache/tracked']
    assert not result['scope_valid']


def test_schedule_balances_order_and_preserves_every_pair():
    tasks = [{'id': 'alpha'}, {'id': 'beta'}]; hosts = [{'id': 'one'}, {'id': 'two'}]
    rows = pilot.schedule(tasks, hosts, 2, 13)
    assert rows == pilot.schedule(tasks, hosts, 2, 13)
    assert len(rows) == 16
    for task in tasks:
        for host in hosts:
            pair = [r for r in rows if r['task'] == task['id'] and r['host'] == host['id']]
            assert pair[0]['arm'] != pair[2]['arm']
            assert {r['arm'] for r in pair[:2]} == {'full', 'focused'}


def test_interleaving_preserves_cells_and_balance_without_adjacent_same_host():
    tasks = [{'id': 'alpha'}, {'id': 'beta'}]
    hosts = [{'id': 'q'}, {'id': 'o'}]
    paired = pilot.schedule(tasks, hosts, 2, 13)
    rows = pilot.schedule(tasks, hosts, 2, 13, 'interleaved-hosts')
    assert sorted(map(json.dumps, rows)) == sorted(map(json.dumps, paired))
    assert [row['host'] for row in rows] == ['q', 'o'] * 8
    for host in hosts:
        assert [r for r in rows if r['host'] == host['id']] == [r for r in paired if r['host'] == host['id']]
    with pytest.raises(ValueError, match='at least two hosts'):
        pilot.schedule(tasks, hosts[:1], 2, 13, 'interleaved-hosts')
    with pytest.raises(ValueError, match='unsupported scheduling'):
        pilot.schedule(tasks, hosts, 2, 13, 'random-typo')


def test_windows_path_preflight_checks_later_focused_cells_before_inference():
    cell = {'task': 'task', 'host': 'host', 'repeat': 1, 'arm': 'full'}
    cells = [cell, cell | {'arm': 'focused'}]
    short = Path('C:/s')
    full_prefix = short / 'r1-host-task-full' / 'workspace'
    boundary_name = 'a' * (239 - len(str(full_prefix).encode('utf-16-le')) // 2 - 1)
    validate_trial_paths(short, 'task', [boundary_name], [cell], windows=True)
    with pytest.raises(ValueError, match='shorter output root'):
        validate_trial_paths(short, 'task', [boundary_name], cells, windows=True)
    name = 'nested/' + 'a' * 175 + '.json'
    validate_trial_paths(short, 'task', [name], cells, windows=True)
    long = Path('C:/checkout/very-long-reconciliation-directory')
    with pytest.raises(ValueError, match='shorter output root'):
        validate_trial_paths(long, 'task', [name], cells, windows=True)
    # The guard counts Windows UTF-16 units, including supplementary characters.
    with pytest.raises(ValueError):
        validate_trial_paths(short, 'task', ['nested/' + '\U0001f600' * 105], cells, windows=True)


def test_aider_unknown_alias_gets_explicit_metadata_and_history_budget(tmp_path):
    host = {'kind': 'aider-opus', 'model': 'opus-4.6-bedrock', 'profile': {'max_tokens': 4096, 'temperature': 0}}
    with pytest.raises(ValueError, match='explicit effective'):
        pilot.aider_context_limits(host)
    host.update(context_window_tokens=1_000_000, context_window_source='qualified route', aider_history_tokens=500_000)
    argv = pilot.aider_argv(host, 'full', tmp_path, tmp_path, 'http://fixture/v1', {'editable_files': [], 'context_files': []})
    metadata = json.loads((tmp_path / 'model-metadata.json').read_text())
    assert metadata['openai/opus-4.6-bedrock']['max_input_tokens'] == 1_000_000
    assert argv[argv.index('--max-chat-history-tokens') + 1] == '500000'
    assert '--model-metadata-file' in argv and '--no-show-model-warnings' not in argv
    host['aider_history_tokens'] = 999_999
    with pytest.raises(ValueError, match='answer reserve'):
        pilot.aider_context_limits(host)


def test_identity_rejects_alias_only_and_detects_restart():
    with pytest.raises(ValueError): identity_fingerprint({'model': 'qwen38'})
    identity = {'binary_sha256': 'a'*64, 'model_sha256': 'b'*64, 'template_sha256': 'c'*64,
                'argv': ['server'], 'effective_parameters': {'n_ctx': 32768}, 'model_hash_verified': True, 'pid': 1}
    assert identity_fingerprint(identity) != identity_fingerprint(identity | {'pid': 2})
    with pytest.raises(ValueError): identity_fingerprint(identity | {'model_hash_verified': False})


def test_profile_gate_rejects_changed_budget_or_backend():
    profile = PROFILES['qwen-low-2048']
    controls = {'schema': 'tare.tools/qwen-transport-controls/1', 'qualified': True, 'identity_fingerprint': 'same',
        'profiles': {profile['id']: {'passed': True, 'profile_sha256': digest(profile)}}}
    validate_controls(controls, profile, {'fingerprint': 'same'})
    with pytest.raises(ValueError): validate_controls(controls, profile | {'max_tokens': 2048}, {'fingerprint': 'same'})
    with pytest.raises(ValueError): validate_controls(controls, profile, {'fingerprint': 'restarted'})


def test_observer_preserves_multi_edit_response_and_counts_unmeasured_retry(tmp_path):
    profile = PROFILES['qwen-instruct']; response = {'choices': [{'finish_reason': 'stop', 'message': {'content':
        'Explanation\na.py\n<<<<<<< SEARCH\na\n=======\nb\n>>>>>>> REPLACE\n'
        'a.py\n<<<<<<< SEARCH\nc\n=======\nd\n>>>>>>> REPLACE'}}],
        'usage': {'prompt_tokens': 9, 'completion_tokens': 7, 'total_tokens': 16}}
    wire = request_parameters(profile); wire.update(wire.pop('extra_body'))
    wire.update(model='qwen38', messages=[{'role': 'user', 'content': 'fix'}])
    with observer(tmp_path, profile, endpoint='', max_calls=2, offline_response=lambda *_: response) as (url, calls):
        req = urllib.request.Request(url + '/chat/completions', json.dumps(wire).encode(), {'Content-Type': 'application/json'})
        with urllib.request.urlopen(req) as result: assert json.load(result) == response
    assert calls[0]['response_delivered'] and calls[0]['usage']['total_tokens'] == 16
    result = summarize_calls([calls[0] | {'forwarded': True}, {'forwarded': True, 'usage': None}])
    assert result['total_tokens'] is None and result['unobserved_upstream_tokens']
    # A successfully repaired second response counts as host completion;
    # the earlier incomplete response still belongs to cost/attempt accounting.
    final = {'response_delivered': True, 'has_final_content': True, 'finish_reasons': ['stop']}
    truncated = final | {'finish_reasons': ['length']}
    process = {'exit_code': 0, 'timeout': False}
    assert aider_completion(process, [truncated, final])['completed']
    assert not aider_completion(process, [final, truncated])['completed']


def test_history_summaries_cost_tokens_but_never_count_as_edit_retries_or_completion():
    marker = '*Briefly* summarize this partial conversation about programming.\nInclude less detail.'
    assert request_purpose({'messages': [{'role': 'system', 'content': marker}]}) == 'history_summary'
    assert request_purpose({'messages': [{'role': 'user', 'content': marker}]}) == 'task'
    task = {'purpose': 'task', 'forwarded': True, 'usage': {'total_tokens': 100},
            'response_delivered': True, 'has_final_content': True, 'finish_reasons': ['length']}
    summary = task | {'purpose': 'history_summary', 'usage': {'total_tokens': 40}, 'finish_reasons': ['stop']}
    usage = summarize_calls([task, summary, summary])
    assert usage['total_tokens'] == 180 and usage['attempted_requests'] == 3
    assert usage['task_requests'] == 1 and usage['auxiliary_requests'] == 2 and usage['extra_attempts'] == 0
    assert not aider_completion({'exit_code': 0, 'timeout': False}, [task, summary])['completed']


def test_description_retrieval_includes_dependency_and_counts_fallback(tmp_path):
    root = tmp_path / 'repo'; (root / 'src').mkdir(parents=True)
    (root / 'specgraph.yaml').write_text('project_name: test\nindexed_paths: [src]\npython_source_roots: [src]\ngoverned_paths: []\n')
    (root / 'src/lib.py').write_text('def leaf():\n    return 1\n\ndef caller():\n    return leaf()\n\ndef unrelated():\n    return 42\n')
    packet, metrics, traces = context_packet(root, tmp_path / 'state', 'caller', ['src/lib.py'])
    assert b'def caller' in packet and b'def leaf' in packet
    assert b'def unrelated' not in packet and metrics['fallback_deliveries'] == 0
    assert metrics['query_api_calls'] >= 2 and metrics['packet_source_reads'] == 1
    assert metrics['query_wall_ms'] + metrics['index_wall_ms'] <= metrics['context_total_wall_ms']
    assert metrics['index_physical_source_reads'] is None
    packet, metrics, _ = context_packet(root, tmp_path / 'miss-state', 'xyzzynonexistent', ['src/lib.py'])
    assert metrics['fallback_deliveries'] == 1 and b'def unrelated' in packet


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repo'; (root / 'src').mkdir(parents=True); (root / 'tests').mkdir()
    (root / 'src/unit.py').write_text('def answer():\n    return 0\n')
    (root / 'src/helper.py').write_text('VALUE = 42\n')
    (root / 'tests/test_smoke.py').write_text('from helper import VALUE\ndef test_value():\n    assert VALUE == 42\n')
    (root / 'pyproject.toml').write_text('[project]\nname="pilot-fixture"\nversion="0.0.0"\n')
    (root / 'specgraph.yaml').write_text('project_name: fixture\nindexed_paths: [src]\npython_source_roots: [src]\ngoverned_paths: []\n')
    (root / 'hidden').mkdir(); (root / 'hidden/answer.txt').write_text('do not expose')
    for args in (['init'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-m', 'fixture']):
        subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True)
    commit = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'], capture_output=True, check=True, text=True).stdout.strip()
    return root, commit


def test_full_export_keeps_dependencies_tests_and_hides_oracles(repository, tmp_path):
    root, commit = repository
    result = export_checkout(root, commit, tmp_path / 'copy', ['hidden'])
    assert 'src/helper.py' in result['exported'] and 'tests/test_smoke.py' in result['exported']
    assert result['excluded_files'] == ['hidden/answer.txt']
    assert not (tmp_path / 'copy/.git').exists()


def test_prepare_executes_full_checkout_freezes_hidden_controls_and_rejects_drift(repository, tmp_path, monkeypatch):
    root, commit = repository
    oracle = tmp_path / 'private_oracle.py'
    oracle.write_text('import json,sys\nfrom unit import answer\nfrom helper import VALUE\np=answer()==VALUE\n'
        'print(json.dumps({"passed":p,"checks":[{"kind":"regression","passed":p}]}))\nsys.exit(0 if p else 1)\n')
    fixed = tmp_path / 'fixed.py'; fixed.write_text('def answer():\n    return 42\n')
    manifest = {'schema': pilot.SCHEMA, 'seed': 0, 'repeats': 2, 'wall_seconds': 30, 'max_aider_calls': 3,
        'tasks': [{'id': 'fixture', 'novelty': 'diagnostic', 'description': 'Fix answer', 'success_criteria': 'Returns 42',
            'repository': str(root), 'commit': commit, 'exclude': ['hidden'], 'context_files': ['src/unit.py', 'src/helper.py'],
            'editable_files': ['src/unit.py'], 'auxiliary_prefixes': ['.pytest_cache'], 'oracle': str(oracle),
            'reference_files': {'src/unit.py': str(fixed)},
            'readiness_commands': [['{python}', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests']]}],
        'hosts': [{'id': 'stub', 'kind': 'codex', 'model': 'stub', 'effort': 'low', 'executable': 'stub', 'tool_policy': 'fixture'}]}
    path = tmp_path / 'manifest.json'; path.write_text(json.dumps(manifest))
    monkeypatch.setattr(pilot, '_host_identity', lambda _: {'version': 'stub'})
    result = pilot.prepare(path, tmp_path / 'prepared')
    assert result['status'] == 'PREPARED' and result['cells'] == 4
    p = pilot.load_protocol(tmp_path / 'prepared')
    task = p['tasks'][0]
    assert not Path(task['oracle']).is_relative_to(Path(task['prepared_source']))
    assert (Path(task['prepared_source']) / 'src/helper.py').exists()
    (Path(task['prepared_source']) / 'src/unit.py').write_text('changed')
    with pytest.raises(ValueError, match='frozen input changed'):
        pilot.load_protocol(tmp_path / 'prepared')


def test_behavior_is_evaluated_despite_scope_violation_and_partial_host(repository, tmp_path, monkeypatch):
    root, commit = repository; source = tmp_path / 'source'
    export_checkout(root, commit, source, ['hidden'])
    oracle = tmp_path / 'oracle.py'
    oracle.write_text('import json,sys\nfrom unit import answer\nfrom helper import VALUE\np=answer()==VALUE\n'
                      'print(json.dumps({"passed":p,"checks":[{"kind":"regression","passed":p}]}))\nsys.exit(0 if p else 1)\n')
    task = {'id': 'repair', 'prepared_source': str(source), 'source_inventory': inventory(source),
        'description': 'Fix answer', 'success_criteria': 'Returns 42', 'context_files': ['src/unit.py'],
        'editable_files': ['src/unit.py'], 'oracle': str(oracle), 'auxiliary_prefixes': ['.pytest_cache']}
    host = {'id': 'stub', 'kind': 'codex', 'provider': {}, 'model': 'stub', 'effort': 'low'}
    script = ('from pathlib import Path; import json; Path("src/unit.py").write_text("def answer():\\n    return 42\\n");'
              'Path("src/forbidden.py").write_text("oops"); print(json.dumps({"type":"turn.failed"}))')
    monkeypatch.setattr(pilot, 'cli_argv', lambda *_: [sys.executable, '-c', script])
    monkeypatch.setattr(pilot, 'capture_provider', lambda *_: {})
    p = {'tasks': [task], 'hosts': [host], 'wall_seconds': 10}
    result = pilot.trial(p, tmp_path, {'host': 'stub', 'task': 'repair', 'repeat': 1, 'arm': 'full'})
    assert result['evaluation']['behavior_passed'] is True
    assert not result['scope']['scope_valid'] and not result['completion']['completed'] and not result['success']
    assert result['failure_dimensions'] == ['EDIT_SCOPE_VIOLATION', 'HOST_INCOMPLETE']


@pytest.mark.parametrize('arm', ['full', 'focused'])
@pytest.mark.parametrize('profile_id', ['qwen-instruct', 'qwen-low-2048'])
def test_real_aider_native_edit_without_observer_edit_filter(tmp_path, arm, profile_id):
    pytest.importorskip('aider')
    workspace = tmp_path / 'workspace'; workspace.mkdir()
    target = workspace / 'unit.py'
    target.write_text('def first():\n    return 1\n\ndef second():\n    return 2\n\n# FULL_ONLY_SENTINEL\n')
    (tmp_path / 'prompt.txt').write_text('Fix both functions.\nunit.py\n```python\ndef first():\n    return 1\n\ndef second():\n    return 2\n```\n')
    response = {'id': 'stub', 'object': 'chat.completion', 'model': 'qwen38',
        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content':
          'I will update both functions.\n\nunit.py\n```python\n<<<<<<< SEARCH\n    return 1\n=======\n    return 3\n>>>>>>> REPLACE\n```\n\n'
          'unit.py\n```python\n<<<<<<< SEARCH\n    return 2\n=======\n    return 4\n>>>>>>> REPLACE\n```\n'}}],
        'usage': {'prompt_tokens': 20, 'completion_tokens': 40, 'total_tokens': 60}}
    host = {'kind': 'aider-qwen', 'model': 'qwen38', 'profile': PROFILES[profile_id],
            'context_window_tokens': 32768, 'aider_history_tokens': 16000, 'context_window_source': 'fixture'}
    with observer(tmp_path / 'calls', host['profile'], endpoint='', max_calls=2, offline_response=lambda *_: response) as (url, calls):
        argv = pilot.aider_argv(host, arm, workspace, tmp_path, url, {'editable_files': ['unit.py'], 'context_files': ['unit.py']})
        proc = run_process(argv, cwd=workspace, dest=tmp_path / 'host', timeout=45, env=pilot.environment(workspace))
    assert proc['exit_code'] == 0 and proc['cleanup_verified'], (tmp_path / 'host/stderr.txt').read_text()
    assert len(calls) == 1 and calls[0]['response_delivered'], calls
    assert 'return 3' in target.read_text() and 'return 4' in target.read_text()
    request = json.loads((tmp_path / 'calls/call-1/request.json').read_text())
    content = json.dumps(request['messages'])
    assert ('FULL_ONLY_SENTINEL' in content) == (arm == 'full')
    assert 'aider_headless.py' in ' '.join(argv) if arm == 'full' else 'pilot_aider.py' in ' '.join(argv)
