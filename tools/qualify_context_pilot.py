"""Versioned context pilot: complete workspaces, native baseline and explicit budgets.

prepare freezes a task manifest and validates hidden grading controls without
inference. run consumes that lock once. Historic qualification runners/evidence
remain unchanged. This is a historical Aider-era experiment, not an installation check.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pilot_process import run_process
from pilot_profiles import PROFILES, digest, identity_fingerprint, request_parameters, validate_controls
from pilot_workspace import (classify_changes, context_packet, export_checkout, full_packet,
                             inventory, relative_path, sha, validate_trial_paths)
from pilot_transport import aider_completion, observer, save, summarize_calls
from host_completion import completion_status

SCHEMA = 'tare.tools/context-pilot/2'
ENGINE = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def schedule(tasks, hosts, repeats, seed, policy='paired', arms=None):
    if policy not in ('paired', 'interleaved-hosts'):
        raise ValueError('unsupported scheduling policy')
    selected_arms = ['full', 'focused'] if arms is None else arms
    if selected_arms not in (['full', 'focused'], ['full', 'focused', 'graphify']):
        raise ValueError('unsupported context arms')
    if type(repeats) is not int or repeats < (1 if len(selected_arms) == 3 else 2):
        raise ValueError('at least two repetitions required for balanced pairs')
    rng = random.Random(seed); rows = []
    pairs = [(task['id'], host['id']) for task in tasks for host in hosts]
    first = {pair: rng.randrange(len(selected_arms)) for pair in pairs}
    for repeat in range(1, repeats + 1):
        ordered = list(pairs); rng.shuffle(ordered)
        for pair in ordered:
            offset = (first[pair] + repeat - 1) % len(selected_arms)
            rotated = selected_arms[offset:] + selected_arms[:offset]
            rows.extend({'task': pair[0], 'host': pair[1], 'repeat': repeat, 'arm': arm} for arm in rotated)
    if policy == 'interleaved-hosts':
        if len(hosts) < 2:
            raise ValueError('interleaving requires at least two hosts')
        queues = [[row for row in rows if row['host'] == host['id']] for host in hosts]
        rows = [row for group in zip(*queues, strict=True) for row in group]
    return rows


def environment(workspace):
    env = os.environ.copy()
    for key in ('CLAUDE_CODE_EFFORT_LEVEL', 'ANTHROPIC_MODEL', 'CODEX_THREAD_ID', 'CLAUDECODE'):
        env.pop(key, None)
    env.update(PYTHONIOENCODING='utf-8', PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               PYTHONPATH=str(Path(workspace) / 'src') + os.pathsep + str(workspace))
    return env


def storage_binding(output):
    """Keep selector and child hosts on one explicit per-lot state root."""
    state = os.environ.get('TARE_SPECGRAPH_STATE_DIR')
    run = os.environ.get('TARE_EXPERIMENT_RUN_DIR')
    if not state and not run:
        return {'mode': 'native-default'}  # Historical standalone callers.
    if not state or not run:
        raise ValueError('pilot state override requires a managed experiment lot')
    from experiment_storage import plain_path
    root = plain_path(run).resolve()
    published = plain_path(state).resolve()
    output = plain_path(output).resolve()
    if not (root / '.experiment.json').is_file() or published != root / 'state/specgraph':
        raise ValueError('pilot state must belong to the managed experiment lot')
    if output == root or not output.is_relative_to(root):
        raise ValueError('pilot output must be inside the managed experiment lot')
    return {'mode': 'managed-lot', 'run_root': str(root), 'state_dir': str(published)}


def command(argv, **values):
    # argv, never a shell. Only whole, documented tokens are substituted.
    substitutions = {'{python}': sys.executable, **{'{' + k + '}': str(v) for k, v in values.items()}}
    return [substitutions.get(arg, arg) for arg in argv]


def grade(task, workspace, dest):
    proc = run_process([sys.executable, '-B', task['oracle'], str(workspace)], cwd=workspace,
        dest=dest, timeout=task.get('oracle_timeout_seconds', 60), env=environment(workspace))
    try:
        result = json.loads((Path(dest) / 'stdout.txt').read_text(encoding='utf-8'))
        valid = (type(result.get('passed')) is bool and isinstance(result.get('checks'), list)
                 and bool(result['checks']) and all(type(c.get('passed')) is bool for c in result['checks'])
                 and result['passed'] == all(c['passed'] for c in result['checks'])
                 and proc['exit_code'] == (0 if result['passed'] else 1))
    except (ValueError, AttributeError):
        result = None; valid = False
    return {'behavior_passed': result['passed'] if valid and proc['cleanup_verified'] else None,
            'oracle_contract_valid': valid, 'report': result, 'process': proc}


def capture_provider(host, dest):
    if host['kind'] not in ('aider-qwen', 'http-qwen'):
        return {'provider': host['kind'], 'model': host['model'],
                'backend_identity': 'NOT_EXPOSED', 'host_identity': host['identity']}
    proc = run_process(command(host['identity_command']), cwd=ENGINE, dest=dest, timeout=180,
                       stdin=Path(host['identity_stdin']).read_bytes() if host.get('identity_stdin') else b'')
    if proc['exit_code'] != 0 or not proc['cleanup_verified']:
        raise ValueError('provider identity collector failed')
    identity = read(Path(dest) / 'stdout.txt')
    identity['fingerprint'] = identity_fingerprint(identity)
    return identity


def _host_identity(host):
    if host['kind'].startswith('http-'):
        return {'python': sys.version, 'python_binary_sha256': sha(Path(sys.executable).read_bytes()),
                'pydantic': importlib.metadata.version('pydantic'), 'host': 'Kernel Session / public Agent Runtime port'}
    if host['kind'].startswith('aider-'):
        return {'aider': importlib.metadata.version('aider-chat'),
                'litellm': importlib.metadata.version('litellm'), 'python': sys.version}
    exe = Path(host['executable'])
    probe = subprocess.run([str(exe), '--version'], capture_output=True, timeout=15, check=True)
    return {'executable_sha256': sha(exe.read_bytes()), 'version': probe.stdout.decode('utf-8', errors='replace').strip()}


def prepare(manifest, output):
    start = time.perf_counter(); p = read(manifest)
    if p.get('schema') != SCHEMA:
        raise ValueError('unsupported manifest schema')
    p['storage_binding'] = storage_binding(output)
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    tasks = p['tasks']; hosts = p['hosts']
    for entries in (tasks, hosts):
        names = [e['id'] for e in entries]
        if not names or len(names) != len(set(names)) or any(not n.replace('-', '').replace('_', '').isalnum() for n in names):
            raise ValueError('nonempty unique safe IDs required')
    order = schedule(tasks, hosts, p['repeats'], p['seed'], p.get('schedule_policy', 'paired'), p.get('arms'))
    if 'graphify' in p.get('arms', []):
        if any(not host['kind'].startswith('http-') for host in hosts):
            raise ValueError('Graphify context comparison currently requires HTTP hosts')
        if not p.get('graphify', {}).get('python_paths'):
            raise ValueError('pinned Graphify module paths required')
    if type(p.get('wall_seconds')) is not int or not 1 <= p['wall_seconds'] <= 1800:
        raise ValueError('explicit bounded wall_seconds required')
    if type(p.get('max_aider_calls')) is not int or not 1 <= p['max_aider_calls'] <= 10:
        raise ValueError('explicit bounded max_aider_calls required')
    files = {}
    for path in list((ENGINE / 'tools').glob('*.py')) + list((ENGINE / 'src').rglob('*.py')):
        files[str(path)] = sha(path.read_bytes())
    for folder in p.get('graphify', {}).get('python_paths', []):
        module_root = Path(folder) / 'graphify' if (Path(folder) / 'graphify').is_dir() else Path(folder)
        for path in module_root.rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix in ('.py', '.pyd', '.dll'):
                files[str(path)] = sha(path.read_bytes())
    for task in tasks:
        if not task.get('description') or not task.get('success_criteria') or task.get('novelty') not in ('new', 'diagnostic'):
            raise ValueError('task description, preregistered criteria and explicit novelty required')
        repo = Path(task['repository']).resolve(); task['repository'] = str(repo)
        for name in task['context_files'] + task['editable_files']:
            relative_path(name)
        if not set(task['editable_files']).issubset(task['context_files']):
            raise ValueError('editable files must be in public context manifest')
        folder = output / 'prepared' / task['id']; folder.mkdir(parents=True)
        oracle = Path(task['oracle']).resolve()
        if oracle.is_relative_to(repo):
            raise ValueError('oracle must be stored outside the agent repository')
        hidden = folder / 'oracle.py'; shutil.copyfile(oracle, hidden); task['oracle'] = str(hidden)
        files[str(hidden)] = sha(hidden.read_bytes())
        source = folder / 'source'
        export = export_checkout(repo, task['commit'], source, task['exclude'])
        validate_trial_paths(output, task['id'], export['exported'], order)
        hidden_hashes = {sha(hidden.read_bytes()), *(sha(Path(path).read_bytes()) for path in task.get('reference_files', {}).values())}
        if hidden_hashes.intersection(entry['sha256'] for entry in export['exported'].values()):
            raise ValueError('export contains a byte-identical hidden oracle or reference answer')
        save(folder / 'export.json', export); task['prepared_source'] = str(source)
        task['source_inventory'] = export['exported']
        if not all((source / name).is_file() for name in task['context_files']):
            raise ValueError('missing public context files in export')
        if not (source / 'pyproject.toml').is_file() or not (source / 'tests').is_dir() or not (source / 'src').is_dir():
            raise ValueError('this pilot requires the complete Python project and its existing tests')
        checks = []
        if not task.get('readiness_commands'):
            raise ValueError('executable checkout readiness commands required')
        for i, argv in enumerate(task['readiness_commands']):
            row = run_process(command(argv, workspace=source), cwd=source, dest=folder / f'readiness-{i}',
                              timeout=120, env=environment(source))
            checks.append(row)
            if row['exit_code'] != 0 or not row['cleanup_verified']:
                raise ValueError('checkout readiness failed; do not interpret it as a model failure')
        baseline = grade(task, source, folder / 'baseline-grade')
        if baseline['behavior_passed'] is not False or not any(c.get('kind') == 'regression' and not c['passed'] for c in baseline['report']['checks']):
            raise ValueError('baseline must fail a declared regression with a valid oracle result')
        if any(not c['passed'] for c in baseline['report']['checks'] if c.get('kind') != 'regression'):
            raise ValueError('baseline non-regression controls must pass')
        control = folder / 'control'; shutil.copytree(source, control)
        if not task.get('reference_files'):
            raise ValueError('hidden positive reference control required')
        for name, path in task['reference_files'].items():
            relative_path(name)
            if name not in task['editable_files'] or Path(path).resolve().is_relative_to(repo):
                raise ValueError('reference must be hidden and affect an editable file')
            target = control / name; target.write_bytes(Path(path).read_bytes())
        positive = grade(task, control, folder / 'positive-grade')
        if positive['behavior_passed'] is not True:
            raise ValueError('positive grading control failed')
        # Restore byte-exact source after all preflight commands and oracles.
        shutil.rmtree(source)
        export_checkout(repo, task['commit'], source, task['exclude'])
        save(folder / 'preflight.json', {'readiness': checks, 'baseline': baseline, 'positive': positive})
        for name in ('preflight.json', 'export.json'):
            files[str(folder / name)] = sha((folder / name).read_bytes())
        for name, entry in task['source_inventory'].items():
            files[str(source / name)] = entry['sha256']
        task.pop('reference_files')
    for host in hosts:
        if host['kind'] in ('aider-qwen', 'http-qwen'):
            host['profile'] = host.get('profile') or PROFILES[host['profile_id']]
            host['model'] = host['profile']['model']
            if not host.get('identity_command'):
                raise ValueError('strong backend identity collector required')
            if host.get('identity_stdin'):
                path = Path(host['identity_stdin']).resolve(); host['identity_stdin'] = str(path)
                files[str(path)] = sha(path.read_bytes())
        elif host['kind'] == 'aider-opus':
            host['model'] = 'opus-4.6-bedrock'
            host['profile'] = {'max_tokens': 4096, 'temperature': 0, 'thinking': False,
                               'cache_policy': 'provider-managed; exact cache state not exposed'}
        elif host['kind'] == 'http-opus':
            host['model'] = host['http_config']['model']
        elif host['kind'] not in ('claude', 'codex', 'agy'):
            raise ValueError('unsupported host')
        if host['kind'].startswith('http-'):
            if type(host.get('max_turns')) is not int or not 1 <= host['max_turns'] <= 30:
                raise ValueError('explicit bounded HTTP turn budget required')
            for name in ('kernel_path', 'runtime_path'):
                folder = Path(host[name]).resolve(); host[name] = str(folder)
                if not folder.is_dir(): raise ValueError('managed HTTP source directory missing')
                for path in folder.rglob('*.py'):
                    files[str(path)] = sha(path.read_bytes())
            from pilot_http import validate_host
            validate_host(host)
        if host['kind'].startswith('aider-'):
            aider_context_limits(host)
        if not host.get('tool_policy'):
            raise ValueError('explicit host tool policy required')
        host['identity'] = _host_identity(host)
        host['provider'] = capture_provider(host, output / ('identity-' + host['id']))
        if host['kind'] in ('aider-qwen', 'http-qwen'):
            controls_path = Path(host['transport_controls']).resolve()
            controls = read(controls_path)
            validate_controls(controls, host['profile'], host['provider'])
            host['transport_controls'] = str(controls_path)
            files[str(controls_path)] = sha(controls_path.read_bytes())
    p.update(engine=str(ENGINE), files=files, order=order, preparation_wall_ms=(time.perf_counter()-start)*1000,
        investigator_tokens=None, shared_setup_tokens=None, investigator_retries=0,
        comparison_scope='paired within host and profile; tool policies and tokenizers differ across hosts')
    save(output / 'protocol.json', p); save(output / 'protocol-lock.json', {'sha256': digest(p)})
    return {'status': 'PREPARED', 'cells': len(order), 'preparation_wall_ms': p['preparation_wall_ms']}


def load_protocol(output):
    p = read(output / 'protocol.json')
    if digest(p) != read(output / 'protocol-lock.json')['sha256']:
        raise ValueError('protocol changed')
    if p.get('storage_binding', {'mode': 'native-default'}) != storage_binding(output):
        raise ValueError('pilot storage binding changed after preparation')
    for path, expected in p['files'].items():
        if sha(Path(path).read_bytes()) != expected:
            raise ValueError('frozen input changed: ' + path)
    for host in p['hosts']:
        if _host_identity(host) != host['identity']:
            raise ValueError('host executable or version changed')
    return p


def aider_context_limits(host):
    window = host.get('context_window_tokens')
    history = host.get('aider_history_tokens')
    if type(window) is not int or window <= host['profile']['max_tokens'] or not host.get('context_window_source'):
        raise ValueError('explicit effective Aider context window and source required')
    if type(history) is not int or not 0 < history <= window - host['profile']['max_tokens']:
        raise ValueError('explicit Aider history token limit with answer reserve required')
    return window, history


def aider_argv(host, arm, workspace, dest, endpoint, task):
    import yaml
    model = 'openai/' + host['model']
    window, history = aider_context_limits(host)
    save(dest / 'model-metadata.json', {model: {'max_input_tokens': window,
        'max_output_tokens': host['profile']['max_tokens'], 'max_tokens': host['profile']['max_tokens'],
        'litellm_provider': 'openai', 'mode': 'chat'}})
    params = request_parameters(host['profile']) if host['kind'] == 'aider-qwen' else {
        'max_tokens': host['profile']['max_tokens'], 'temperature': host['profile']['temperature']}
    params['num_retries'] = 0
    (dest / 'model.yml').write_text(yaml.safe_dump([{'name': model, 'edit_format': 'diff',
        'extra_params': params, 'weak_model_name': model, 'use_repo_map': False}]), encoding='utf-8')
    (dest / 'config.yml').write_text('{}\n'); (dest / 'empty.env').write_text('')
    editable = [str(workspace / name) for name in task['editable_files']]
    save(dest / 'editable.json', editable)
    head = ([str(ENGINE / 'tools/pilot_aider.py'), str(dest / 'editable.json')] if arm == 'focused'
            else [str(ENGINE / 'tools/aider_headless.py')])
    argv = [sys.executable, '-B', *head, '--model', model, '--openai-api-base', endpoint,
        '--openai-api-key', 'local-observer', '--model-settings-file', str(dest / 'model.yml'),
        '--model-metadata-file', str(dest / 'model-metadata.json'), '--max-chat-history-tokens', str(history),
        '--config', str(dest / 'config.yml'), '--env-file', str(dest / 'empty.env'), '--edit-format', 'diff',
        '--map-tokens', '0', '--line-endings', 'lf', '--no-git', '--no-auto-commits', '--no-dirty-commits',
        '--no-stream', '--no-pretty', '--no-auto-lint', '--no-auto-test', '--no-suggest-shell-commands',
        '--no-check-update', '--no-show-release-notes', '--no-analytics', '--no-detect-urls',
        '--yes-always', '--timeout', '120',
        '--message-file', str(dest / 'prompt.txt'), '--chat-history-file', str(dest / 'host-chat.md'),
        '--input-history-file', str(dest / 'host-input.txt')]
    for path in editable: argv += ['--file', path]
    # Read-only dependencies use native Aider in both arms; focused dependencies
    # are delivered through the packet to avoid silently sending full files.
    if arm == 'full':
        for name in task['context_files']:
            if name not in task['editable_files']: argv += ['--read', str(workspace / name)]
    return argv


def cli_argv(host, workspace, dest, wall):
    from qualify_host_matrix import cli_argv as legacy_argv
    argv = legacy_argv({'host': host['kind'], 'model': host['model'], 'effort': host['effort']}, dest)
    argv[0] = host['executable']
    if host['kind'] == 'agy':
        argv[argv.index('--print-timeout') + 1] = str(max(1, wall - 10)) + 's'
    if host['kind'] == 'codex':
        argv[argv.index('model_reasoning_effort="low"')] = 'model_reasoning_effort=' + json.dumps(host['effort'])
    return argv


def trial(p, output, cell):
    started = time.perf_counter(); row = dict(cell)
    row['started_at_utc'] = datetime.now(timezone.utc).isoformat()
    task = next(t for t in p['tasks'] if t['id'] == cell['task'])
    host = next(h for h in p['hosts'] if h['id'] == cell['host'])
    dest = output / f"r{cell['repeat']}-{cell['host']}-{cell['task']}-{cell['arm']}"
    dest.mkdir(); workspace = dest / 'workspace'
    shutil.copytree(task['prepared_source'], workspace)
    before = inventory(workspace)
    if before != task['source_inventory']:
        raise ValueError('exported workspace differs from preflight')
    provider_before = capture_provider(host, dest / 'provider-before')
    if provider_before.get('fingerprint') != host['provider'].get('fingerprint'):
        raise ValueError('provider changed before trial')
    if cell['arm'] == 'focused':
        if os.environ.get('TARE_SPECGRAPH_STATE_DIR'):
            raise ValueError('pilot requires the native default SpecGraph state shared with host child processes')
        packet, metrics, traces = context_packet(workspace, None, task['description'], task['context_files'],
            max_bytes=p.get('context_max_bytes', 32768), success_criteria=task['success_criteria'])
        metrics['query_input'] = 'description_plus_explicit_identifiers_from_success_criteria'
        save(dest / 'selection.json', traces)
    elif cell['arm'] == 'graphify':
        from pilot_graphify import select_context
        packet, metrics, traces = select_context(workspace, dest / 'graphify-state', task['description'],
            task['context_files'], success_criteria=task['success_criteria'],
            max_bytes=p.get('context_max_bytes', 32768), **p['graphify'])
        save(dest / 'selection.json', traces)
    elif host['kind'].startswith('aider-'):
        # The baseline lets Aider read/send files natively. Preparing a duplicate
        # full packet here would charge artificial source-reading overhead.
        packet = b''
        metrics = {'index_builds': 0, 'index_wall_ms': 0, 'query_wall_ms': 0, 'query_api_calls': 0,
            'context_total_wall_ms': 0, 'packet_source_reads': 0, 'packet_source_bytes': 0,
            'packet_bytes': 0, 'host_source_reads': None, 'fallback_deliveries': 0,
            'delivery': 'native Aider file messages; inspect captured request for actual bytes'}
    else:
        packet, metrics = full_packet(workspace, task['context_files'])
    (dest / 'packet.txt').write_bytes(packet); save(dest / 'context.json', metrics)
    prompt = (task['description'] + '\n\nSuccess criteria:\n' + task['success_criteria'] +
        '\nWork in the existing project. Editable files: ' + ', '.join(task['editable_files']) +
        '. Preserve other source files. You may inspect this project and use the host tools available to you. '
        'Do not delegate, browse, install dependencies or inspect parent directories. '
        'Hidden evaluation follows completion.\n')
    if not host['kind'].startswith('http-') and (cell['arm'] == 'focused' or not host['kind'].startswith('aider-')):
        prompt += '\nSupplied source context (omitted code remains on disk):\n' + packet.decode()
    (dest / 'prompt.txt').write_text(prompt, encoding='utf-8')
    row.update(context=metrics, provider_before=provider_before, investigator_interventions=0,
               investigator_retries=0, hidden_host_retries=None, source_inventory_sha256=digest(before))
    if host['kind'].startswith('http-'):
        config = {**host['http_config'], 'repositories': {'repo': str(workspace)},
            'role': 'implementer', 'writable': {'repo': task['editable_files']},
            'source_context': [{'identity': sha(packet), 'text': packet.decode()}],
            'verifiers': {str(i): {'repository': 'repo', 'argv': command(argv, workspace=workspace)}
                for i, argv in enumerate(task['readiness_commands'])}}
        prompt += ('\nThe separate source snapshots were read by the host from this workspace and include whole-file SHA256 hashes. '
                   'You may edit from those observations; read more source ranges when necessary. '
                   'Run every configured verifier after the final edit. Their names are in the host contract.')
        (dest / 'prompt.txt').write_text(prompt, encoding='utf-8')
        save(dest / 'http-request.json', {'id': dest.name, 'config': config, 'instruction': prompt,
            'kernel_path': host['kernel_path'], 'runtime_path': host['runtime_path'],
            'custody': str(dest / 'session'), 'result': str(dest / 'http-result.json'),
            'max_turns': host['max_turns'], 'wall_seconds': max(1, p['wall_seconds']-5),
            'qwen_profile': host.get('profile') if host['kind'] == 'http-qwen' else None})
        argv = [sys.executable, '-B', str(ENGINE / 'tools/pilot_http.py'), str(dest / 'http-request.json')]
        save(dest / 'argv.json', argv)
        process = run_process(argv, cwd=workspace, dest=dest / 'host', timeout=p['wall_seconds'], env=environment(workspace))
        observed = read(dest / 'http-result.json') if (dest / 'http-result.json').is_file() else {}
        row['calls'] = observed.get('calls', [])
        tokens = observed.get('tokens', {})
        complete_usage = all(type(tokens.get(k)) is int for k in ('input','output'))
        row['usage'] = {'input_tokens': tokens.get('input'), 'output_tokens': tokens.get('output'),
            'total_tokens': tokens['input'] + tokens['output'] if complete_usage else None,
            'attempted_requests': len(row['calls']), 'transport_errors': [c['error'] for c in row['calls'] if c.get('error')],
            'token_counter_queries': observed.get('token_counter_queries'),
            'observed_http_requests': observed.get('observed_http_requests'),
            'reported_cost_usd': None, 'unobserved_upstream_tokens': not complete_usage}
        row['completion'] = {'completed': bool(observed.get('completed')) and process['exit_code'] == 0 and not process['timeout'],
            'status': observed.get('status', 'HOST_RESULT_MISSING')}
        row['cache_policy_verified'] = observed.get('wire_profile_verified')
        row['host_tool_receipts'] = observed.get('receipts', [])
        if observed and not observed.get('model_process_cleanup_verified'):
            process['cleanup_verified'] = False
    elif host['kind'].startswith('aider-'):
        with observer(dest / 'calls', host['profile'], endpoint=host.get('endpoint', ''),
                      max_calls=p['max_aider_calls'], backend='qwen' if host['kind'] == 'aider-qwen' else 'bedrock') as (endpoint, calls):
            argv = aider_argv(host, cell['arm'], workspace, dest, endpoint, task)
            save(dest / 'argv.json', argv)
            process = run_process(argv, cwd=workspace, dest=dest / 'host', timeout=p['wall_seconds'], env=environment(workspace))
        row['calls'] = calls; row['usage'] = summarize_calls(calls)
        if host['kind'] == 'aider-qwen':
            forwarded = [c for c in calls if c.get('forwarded')]
            row['cache_policy_verified'] = bool(forwarded) and all(
                ((c.get('usage') or {}).get('timings') or {}).get('cache_n') == 0 for c in forwarded)
        else:
            row['cache_policy_verified'] = None  # provider-managed, not experimentally cold
        row['completion'] = aider_completion(process, calls)
        # An unmatched edit can exit zero; behavior and changed-file evidence are
        # separate, so completion alone never makes a successful trial.
    else:
        from qualify_host_matrix import cli_usage
        argv = cli_argv(host, workspace, dest, p['wall_seconds']); save(dest / 'argv.json', argv)
        stdin = json.dumps({'event': 'user', 'message': {'content': prompt}}).encode() + b'\n' if host['kind'] == 'agy' else prompt.encode()
        process = run_process(argv, cwd=workspace, dest=dest / 'host', timeout=p['wall_seconds'], env=environment(workspace), stdin=stdin)
        events = []
        for line in (dest / 'host/stdout.txt').read_text(encoding='utf-8', errors='replace').splitlines():
            try:
                value = json.loads(line)
                if isinstance(value, dict): events.append(value)
            except ValueError: pass
        row['usage'] = cli_usage(events, host['kind'])
        row['completion'] = completion_status(host['kind'], events, (dest / 'host/stderr.txt').read_bytes(),
            process['exit_code'], timed_out=process['timeout'])
    row['process'] = process
    if not process['cleanup_verified']:
        row.update(success=False, evaluation={'behavior_passed': None, 'reason': 'CLEANUP_UNVERIFIED'})
        save(dest / 'receipt.json', row)
        raise RuntimeError('host cleanup unverified; workspace cannot be graded or reused')
    # Inventory before evaluation: pytest artifacts from the grader are not host edits.
    row['scope'] = classify_changes(before, inventory(workspace), task['editable_files'], task['auxiliary_prefixes'])
    row['evaluation'] = grade(task, workspace, dest / 'verification')  # unconditional after quiescence
    row['provider_after'] = capture_provider(host, dest / 'provider-after')
    row['provider_stable'] = row['provider_after'].get('fingerprint') == provider_before.get('fingerprint')
    row['configuration_valid'] = row['provider_stable'] and (host['kind'] not in ('aider-qwen', 'http-qwen') or row['cache_policy_verified'])
    row['success'] = (row['evaluation']['behavior_passed'] is True and row['scope']['scope_valid']
                      and row['completion']['completed'] and row['configuration_valid'])
    row['failure_dimensions'] = [name for name, failed in (
        ('BEHAVIOR_FAILED', row['evaluation']['behavior_passed'] is False),
        ('EVALUATION_UNAVAILABLE', row['evaluation']['behavior_passed'] is None),
        ('EDIT_SCOPE_VIOLATION', not row['scope']['scope_valid']),
        ('HOST_INCOMPLETE', not row['completion']['completed']),
        ('CONFIGURATION_NOT_VERIFIED', not row['configuration_valid']),
        ('TRANSPORT_ERROR', bool((row.get('usage') or {}).get('transport_errors')))) if failed]
    row['whole_wall_ms'] = (time.perf_counter() - started) * 1000
    row['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    save(dest / 'receipt.json', row)
    return row


def report(p, rows):
    if 'graphify' in p.get('arms', []):
        comparisons = {}
        for baseline, candidate in [('full', 'focused'), ('full', 'graphify'), ('focused', 'graphify')]:
            selected = [dict(r, arm='full' if r['arm'] == baseline else 'focused')
                        for r in rows if r['arm'] in (baseline, candidate)]
            comparisons[candidate + '_vs_' + baseline] = report(dict(p, arms=['full', 'focused']), selected)
        return {'schema': SCHEMA, 'comparisons': comparisons, 'integral_savings_claim': False,
                'planned_cells': len(p['order']), 'observed_cells': len(rows)}
    groups = []
    for host in p['hosts']:
        pairs = []
        for task in p['tasks']:
            for repeat in range(1, p['repeats'] + 1):
                pair = {r['arm']: r for r in rows if r['host'] == host['id'] and r['task'] == task['id'] and r['repeat'] == repeat}
                if set(pair) != {'full', 'focused'}: continue
                totals = [(pair[a].get('usage') or {}).get('total_tokens') for a in ('full', 'focused')]
                pairs.append({'task': task['id'], 'repeat': repeat, 'both_successful': all(r['success'] for r in pair.values()),
                    'token_delta': totals[1] - totals[0] if all(type(v) is int for v in totals) else None,
                    'wall_delta_ms': pair['focused']['whole_wall_ms'] - pair['full']['whole_wall_ms']})
        deltas = [x['token_delta'] for x in pairs if x['token_delta'] is not None]
        complete = len(pairs) == len(p['tasks']) * p['repeats']
        groups.append({'host': host['id'], 'pairs': pairs, 'complete': complete,
            'all_pairs_correct': complete and all(x['both_successful'] for x in pairs),
            'mean_token_delta': statistics.mean(deltas) if len(deltas) == len(pairs) and deltas else None,
            'token_delta_range': [min(deltas), max(deltas)] if deltas else None,
            'token_delta_sample_stdev': statistics.stdev(deltas) if len(deltas) >= 2 else None,
            'uncertainty': 'descriptive repeat variation; no population-level confidence claim'})
    return {'schema': SCHEMA, 'groups': groups, 'shared_preparation_wall_ms': p['preparation_wall_ms'],
        'investigator_tokens': None, 'shared_setup_tokens': None, 'integral_savings_claim': False,
        'new_task_utility': all(t['novelty'] == 'new' for t in p['tasks']),
        'cross_host_model_ranking': False}


def run(output):
    p = load_protocol(output)
    with (output / 'run-reserved.json').open('x', encoding='utf-8') as stream:
        json.dump({'no_investigator_retries': True, 'order': p['order']}, stream)
    rows = []
    for cell in p['order']:
        try:
            row = trial(p, output, cell)
        except Exception as exc:
            save(output / 'runner-failure.json', {'cell': cell, 'error': f'{type(exc).__name__}: {exc}',
                'classification': 'EXECUTOR_OR_PREFLIGHT_FAILURE', 'remaining_cells': 'NOT_RUN'})
            raise
        rows.append(row)
        save(output / 'results.json', rows); save(output / 'summary.json', report(p, rows))
        print(json.dumps({**cell, 'success': row['success'], 'behavior': row['evaluation']['behavior_passed']}), flush=True)
        if not row['configuration_valid'] or not row['evaluation']['process']['cleanup_verified']:
            raise RuntimeError('configuration drift or evaluator cleanup failure; remaining cells stopped')
    return report(p, rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'run', 'report'))
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); out = args.output.resolve()
    if args.action == 'prepare': result = prepare(args.manifest, out)
    elif args.action == 'run': result = run(out)
    else: result = report(read(out / 'protocol.json'), read(out / 'results.json'))
    print(json.dumps(result, indent=2))
