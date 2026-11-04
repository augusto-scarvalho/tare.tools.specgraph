"""Bounded Aider diff trials: native /run delivery and native file application.

The observer validates an exact authorized edit but never writes that edit.
No automatic hook, autonomous loop, resume or multi-root session is implied.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

try:
    from . import qualify_context_selection as base
except ImportError:
    import qualify_context_selection as base

WORK = 'SPECGRAPH-FOLLOWUP-HOST-ADOPTION-20260908'
ARMS = ('full', 'focused')
TASKS = ('exact-file-selection', 'receipt-write-release')
wire, sha, save, read = base.wire, base.sha, base.save, base.read


def packet(task, root, state, arm, edit_root):
    """Read only the published source and exact symbol, with bounded SQL work."""
    from specgraph.storage import RepositoryIndex
    if arm not in ARMS:
        raise ValueError('unknown context arm')
    started = time.perf_counter()
    with RepositoryIndex(root, state_dir=state, timeout_s=1) as index:
        index.connection.set_progress_handler(lambda: int(time.perf_counter() - started >= 1), 100)
        if index.metadata()['content_hash'] != task['snapshot']:
            raise ValueError('published snapshot changed')
        row = index.connection.execute(
            'SELECT body,sha256 FROM sources WHERE file_path=? AND length(CAST(body AS BLOB))<=32768',
            (task['path'],)).fetchone()
        if row is None or row[1] != task['source_sha256'] or sha(row[0].encode()) != row[1]:
            raise ValueError('published source identity mismatch')
        body = row[0]
        selected = [1, len(body.splitlines())]
        if arm == 'focused':
            symbol = index.connection.execute('SELECT start_line,end_line FROM symbols WHERE symbol_id=?',
                (task['path'] + ':' + task['symbol'],)).fetchone()
            if symbol is None or list(symbol) != task['target_range']:
                raise ValueError('published exact symbol mismatch')
            selected = list(symbol)
            body = ''.join(body.splitlines(keepends=True)[selected[0] - 1:selected[1]])
    header = {'authority': 'NONE', 'work_id': WORK, 'source_root': str(root),
        'edit_root': str(edit_root), 'source_ref': task['repo'] + ':' + task['base'],
        'snapshot': task['snapshot'], 'file': task['path'], 'sha256': task['source_sha256'],
        'lines': selected, 'scope': 'explicit historical file; omitted dependencies not verified'}
    result = wire(header) + b'```python\n' + body.encode() + b'\n```\n'
    if len(result) > 32768 or time.perf_counter() - started > 1:
        raise ValueError('context byte or time budget exceeded')
    return result


def permitted_edit(response, source, task, edit_root=None):
    observed = base.observe_edit_response(response)
    if observed['response_status'] != 'EDIT_PROPOSAL':
        raise ValueError(observed['response_status'])
    proposal = observed['answer']
    if edit_root is not None:
        # Compare actual file identity, not the spelling chosen by the host.
        # Never rewrite the response sent to Aider or relax the source check.
        root = Path(edit_root).resolve()
        target = (root / task['path']).resolve()
        candidate = Path(proposal['path'])
        candidate = (candidate if candidate.is_absolute() else root / candidate).resolve()
        if not target.is_relative_to(root) or candidate != target:
            raise ValueError('edit must resolve to the exact authorized file')
        proposal = proposal | {'path': task['path']}
    if task.get('edit_scope') == 'function-body-v2':
        from scoped_edit import checked_replacement
        return checked_replacement(source, task, proposal)
    return base.checked_replacement(source, task, proposal)


def decide(records):
    expected = {(n, t, a) for n in (1, 2) for t in TASKS for a in ARMS}
    actual = [(r['round'], r['task'], r['arm']) for r in records]
    complete = len(actual) == len(expected) and set(actual) == expected
    rounds = []
    for n in (1, 2):
        row = {'round': n}
        for arm in ARMS:
            xs = [r for r in records if r['round'] == n and r['arm'] == arm]
            measured = len(xs) == 2 and all(len(r['calls']) == 1 and r['calls'][0].get('usage') is not None for r in xs)
            row[arm] = {'successes': sum(r['success'] for r in xs), 'trials': len(xs),
                'tokens': sum(r['calls'][0]['usage']['total_tokens'] for r in xs) if measured else None,
                'total_wall_ms': sum(r['total_wall_ms'] for r in xs)}
        a, b = row['full'], row['focused']
        row['gain'] = (b['successes'] == 2 and b['successes'] >= a['successes']
            and a['tokens'] is not None and b['tokens'] is not None
            and b['tokens'] < a['tokens'] and b['total_wall_ms'] <= a['total_wall_ms'])
        rounds.append(row)
    stable = all(not any(r.get(k) for k in ('offline', 'provider_changed', 'global_analytics_changed',
        'extra_requests', 'interventions')) for r in records)
    qualified = complete and stable and all(r['success'] for r in records if r['arm'] == 'focused')
    return {'status': 'QUALIFIED_BOUNDED_NATIVE_EDIT' if qualified else 'REJECT_NATIVE_EDIT' if complete else 'INCOMPLETE',
        'benefit': 'OBSERVED_IN_BOTH_LOCAL_ROUNDS' if qualified and all(r['gain'] for r in rounds) else 'NOT_DEMONSTRATED',
        'rounds': rounds, 'automatic_roles': [], 'host_work_complete': False,
        'scope': 'Aider diff plus external oracle; explicit /run, two known defects, no sustained benefit claim'}


def frozen(directory):
    p = read(directory / 'protocol.json')
    if sha(wire(p)) != read(directory / 'protocol-lock.json')['sha256']:
        raise ValueError('protocol changed')
    for path, digest in p['files'].items():
        if sha(Path(path).read_bytes()) != digest:
            raise ValueError('frozen source changed: ' + path)
    if importlib.metadata.version('aider-chat') != p['aider_version']:
        raise ValueError('host version changed')
    return p


def prepare(args):
    if not re.fullmatch('[0-9a-f]{40}', args.implementation_commit or ''):
        raise ValueError('full implementation commit from checkout owner required')
    out = args.output.resolve()
    out.mkdir(exist_ok=False)
    engine = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(engine / 'src'))
    from specgraph.indexer import index_repository
    rows = []
    files = {}
    for identity in TASKS:
        old = args.cases / identity
        task = read(old / 'task.json')
        dest = out / 'prepared' / identity
        source = dest / 'corpus' / task['path']
        source.parent.mkdir(parents=True)
        shutil.copyfile(old / 'source.py', source)
        for name in ('oracle.py', 'fixed.py', 'task.json'):
            shutil.copyfile(old / name, dest / name)
        if sha(source.read_bytes()) != task['source_sha256'] or sha((dest / 'oracle.py').read_bytes()) != task['oracle_sha256']:
            raise ValueError('historical source/oracle changed')
        cfg = dest / 'corpus/specgraph.yaml'
        cfg.write_bytes(('project_name: ' + task['repo'] + '\nindexed_paths: [' + task['path'].split('/')[0]
            + ']\ngoverned_paths: []\npython_source_roots: [src, "."]\n').encode())
        started = time.perf_counter()
        metadata = index_repository(dest / 'corpus', state_dir=dest / 'state')
        # New extractor versions legitimately publish a new snapshot for the
        # same source. Preserve the historical ID and freeze this exact build.
        task['historical_snapshot'] = task['snapshot']
        task['snapshot'] = metadata['content_hash']
        task['extractor_version'] = metadata['extractor_version']
        save(dest / 'task.json', task)
        row = {'id': identity, 'task': str(dest / 'task.json'), 'root': str(dest / 'corpus'),
            'state': str(dest / 'state'), 'source': str(source), 'oracle': str(dest / 'oracle.py'),
            'index_ms': (time.perf_counter() - started) * 1000}
        checks = []
        for candidate in (source, dest / 'fixed.py'):
            proc = subprocess.run([sys.executable, '-B', str(dest / 'oracle.py'), str(candidate)],
                capture_output=True, timeout=10)
            checks.append({'exit_code': proc.returncode, 'stdout': proc.stdout.decode(), 'stderr': proc.stderr.decode()})
        if checks[0]['exit_code'] == 0 or checks[1]['exit_code'] != 0:
            raise ValueError('historical oracle does not discriminate')
        row['oracle_preflight'] = checks
        row['packet_bytes'] = {a: len(packet(task, row['root'], row['state'], a, 'TRIAL_COPY')) for a in ARMS}
        rows.append(row)
        for path in (source, dest / 'oracle.py', dest / 'fixed.py', dest / 'task.json', cfg):
            files[str(path)] = sha(path.read_bytes())
    import aider.coders.base_coder, aider.coders.editblock_coder, aider.commands, aider.io, aider.args
    paths = list((engine / 'src').rglob('*.py')) + [Path(__file__), engine / 'tools/aider_headless.py',
        engine / 'tools/qualify_context_selection.py', engine / 'tools/response_contract.py',
        engine / 'tools/native_edit_contract.py', args.contract.resolve(), args.registration.resolve(),
        Path(aider.coders.base_coder.__file__), Path(aider.coders.editblock_coder.__file__),
        Path(aider.commands.__file__), Path(aider.io.__file__), Path(aider.args.__file__)]
    for path in paths:
        files[str(path.resolve())] = sha(path.read_bytes())
    p = {'schema': 'tare.tools/native-host-edit/1', 'work_id': WORK, 'authority': 'NONE',
        'implementation_commit': args.implementation_commit,
        'tasks': rows, 'files': files, 'engine': str(engine / 'src'),
        'runner': str(Path(__file__).resolve()), 'headless': str(engine / 'tools/aider_headless.py'),
        'model': 'qwen38', 'provider': base.provider(), 'aider_version': importlib.metadata.version('aider-chat'),
        'litellm_version': importlib.metadata.version('litellm'), 'python': sys.executable,
        'analytics_before': base.analytics(), 'automatic_roles': [], 'rounds': 2,
        'budgets': {'real_calls': 8, 'calls_per_trial': 1, 'output_tokens': 2048, 'retry_budget': 0,
            'packet_bytes': 32768, 'query_ms': 1000, 'request_bytes': base.MAX_REQUEST,
            'response_bytes': base.MAX_RESPONSE, 'trial_seconds': 150},
        'decision_rule': 'Four focused repairs correct, no correctness loss, lower total tokens and no greater wall time in each round; no drift, intervention or unknown counters.',
        'scope': 'Explicit exact-symbol selection, known historical repairs; native editor, external oracle; no automatic hooks/resume/subagents/multi-root qualification'}
    save(out / 'protocol.json', p)
    save(out / 'protocol-lock.json', {'sha256': sha(wire(p))})
    return {'status': 'PREPARED_NO_INFERENCE', 'protocol_sha256': sha(wire(p)),
        'tasks': [{k: x[k] for k in ('id', 'packet_bytes', 'index_ms')} for x in rows]}


def emit(directory, task_id, arm, dest):
    p = frozen(directory)
    sys.path.insert(0, p['engine'])
    t = next(x for x in p['tasks'] if x['id'] == task_id)
    started = time.perf_counter()
    body = packet(read(t['task']), t['root'], t['state'], arm, dest / 'workspace')
    (dest / 'packet.txt').write_bytes(body)
    save(dest / 'emission.json', {'bytes': len(body), 'sha256': sha(body),
        'query_wall_ms': (time.perf_counter() - started) * 1000, 'bridge': 'NATIVE_AIDER_RUN_ON_DEMAND'})
    sys.stdout.buffer.write(body)


def offline_probe_edit(source, task):
    """Exercise native editing without repeating an arbitrarily large function."""
    lines = source.decode().splitlines(keepends=True)
    old = lines[task['target_range'][0] - 1]
    if source.decode().count(old) != 1:
        raise ValueError('offline probe requires a unique declaration line')
    indent = old[:len(old) - len(old.lstrip())]
    new = indent + '# native transport preflight\n' + old
    return task['path'] + '\n```python\n<<<<<<< SEARCH\n' + old + '=======\n' + new + '>>>>>>> REPLACE\n```'


def trial(directory, task_id, arm, number, *, offline=False, response_fixture=None):
    started = time.perf_counter()
    if response_fixture is not None and not offline:
        raise ValueError('recorded responses are offline fixtures only')
    p = frozen(directory)
    if not offline and base.provider() != p['provider']:
        raise ValueError('provider instance drift before trial')
    t = next(x for x in p['tasks'] if x['id'] == task_id)
    task = read(t['task'])
    dest = directory / (f'r{number}-{task_id}-{arm}' if not offline else 'offline-preflight')
    dest.mkdir()
    workspace = dest / 'workspace'
    target = workspace / task['path']
    target.parent.mkdir(parents=True)
    source = Path(t['source']).read_bytes()
    target.write_bytes(source)
    calls, gate = [], threading.Lock()
    result = {'round': number, 'task': task_id, 'arm': arm, 'offline': offline, 'calls': calls,
        'success': False, 'extra_requests': 0, 'interventions': 0}
    class Observer(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != '/v1/chat/completions':
                self.send_error(404)
                return
            with gate:
                if calls:
                    result['extra_requests'] += 1
                    self.send_error(400, 'no further model calls authorized')
                    return
                record = {'usage': None, 'forwarded': False, 'delivered': False}
                calls.append(record)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= base.MAX_REQUEST:
                    raise ValueError('request byte budget')
                raw = self.rfile.read(length)
                req = json.loads(raw)
                base.validate_request(req, 'direct-answer', model=p.get('model', 'qwen38'))
                context = (dest / 'packet.txt').read_text(encoding='utf-8').strip()
                record['packet_observed'] = any(isinstance(m.get('content'), str) and context in m['content'] for m in req['messages'])
                if not record['packet_observed']:
                    raise ValueError('native context delivery absent')
                if p.get('projected_editable'):
                    contents = [m.get('content', '') for m in req['messages'] if isinstance(m.get('content'), str)]
                    record['editable_registered'] = any('This existing file has been added to the chat and is authorized for editing:' in c for c in contents)
                    record['conflicting_no_files'] = any('I am not sharing any files that you can edit yet.' in c for c in contents)
                    if not record['editable_registered'] or record['conflicting_no_files']:
                        raise ValueError('editable file registration conflicts with host instructions')
                    if sum(c.count(context) for c in contents) != 1:
                        raise ValueError('context packet must be delivered exactly once')
                (dest / 'request.json').write_bytes(raw)
                record.update(request_sha256=sha(raw), request_bytes=len(raw))
                save(dest / 'attempt.json', record | {'state': 'RESERVED'})
                provider_started = time.perf_counter()
                if offline:
                    # Fixed transport fixture, never scored as inference or benefit.
                    content = offline_probe_edit(source, task)
                    decoded = {'id': 'offline', 'object': 'chat.completion', 'created': 0, 'model': 'qwen38',
                        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': content}}],
                        'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
                    if response_fixture is not None:
                        decoded = response_fixture
                    body = wire(decoded)
                else:
                    record['forwarded'] = True
                    save(dest / 'attempt.json', record | {'state': 'FORWARD_ATTEMPT'})
                    if p.get('inference_backend') == 'bedrock':
                        from bedrock_observed import converse
                        decoded = converse(req, dest)
                        body = wire(decoded)
                    else:
                        req = urllib.request.Request(base.ENDPOINT + '/chat/completions', data=raw,
                            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer local-key'})
                        with urllib.request.urlopen(req, timeout=115) as response:
                            body = response.read(base.MAX_RESPONSE + 1)
                        if len(body) > base.MAX_RESPONSE:
                            raise ValueError('response byte budget')
                        decoded = json.loads(body)
                record['provider_wall_ms'] = (time.perf_counter() - provider_started) * 1000
                (dest / 'response.json').write_bytes(body)
                record.update(response_sha256=sha(body), usage=base.actual_usage(decoded.get('usage')),
                    **base.observe_edit_response(decoded))
                expected = permitted_edit(decoded, source, task, workspace)
                record['expected_sha256'] = sha(expected)
                record['delivered'] = True
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                record['error'] = type(exc).__name__ + ':' + str(exc)[:200]
                self.send_error(400, 'bounded native edit rejected')
            finally:
                save(dest / 'attempt.json', record | {'state': 'FINISHED'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Observer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    helper = [sys.executable, '-B', p['runner'], 'emit', '--output', str(directory),
        '--task', task_id, '--arm', arm, '--trial-dir', str(dest)]
    if os.name == 'nt':
        if any(any(c in v for c in '%&|<>^\r\n') for v in helper):
            raise ValueError('unsupported shell-sensitive path')
        command = subprocess.list2cmdline(helper)
    else:
        import shlex
        command = shlex.join(helper)
    (dest / 'commands.txt').write_text('/run ' + command + '\n', encoding='utf-8')
    (dest / 'prompt.txt').write_text(task['prompt'] + '\nEdit only ' + task['path']
        + ('. The existing file is registered as editable in this chat. ' if p.get('projected_editable')
           else '. The existing file is in this workspace and may be edited although not added to chat. ')
        + 'Use exactly one SEARCH/REPLACE block, with a unique exact SEARCH within the supplied target function. '
        'Preserve literal source characters and indentation. Return only the edit block; no prose or commands.', encoding='utf-8')
    (dest / 'config.yml').write_text('{}\n')
    (dest / 'empty.env').write_text('')
    model_name = 'openai/' + p.get('model', 'qwen38')
    save(dest / 'model.yml', [{'name': model_name, 'edit_format': 'diff',
        'extra_params': {'max_tokens': 2048, 'num_retries': 0, 'temperature': 0,
            'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}}])
    env = {k: v for k, v in os.environ.items() if k.upper() in {'PATH', 'PATHEXT', 'SYSTEMROOT',
        'WINDIR', 'COMSPEC', 'TEMP', 'TMP', 'HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA'}}
    env.update(PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
    projected = ['--authorized-file', str(target)] if p.get('projected_editable') else []
    argv = [sys.executable, '-B', p['headless'], *projected, '--model', model_name, '--openai-api-base',
        f'http://127.0.0.1:{server.server_port}/v1', '--openai-api-key', 'local-key',
        '--model-settings-file', str(dest / 'model.yml'), '--config', str(dest / 'config.yml'),
        '--env-file', str(dest / 'empty.env'), '--edit-format', 'diff', '--map-tokens', '0',
        '--line-endings', 'lf', '--max-chat-history-tokens', '65536',
        '--no-git', '--no-auto-commits', '--no-dirty-commits', '--no-stream', '--no-pretty',
        '--no-auto-lint', '--no-auto-test', '--no-suggest-shell-commands', '--no-check-update',
        '--no-show-release-notes', '--no-analytics', '--no-detect-urls', '--no-show-model-warnings',
        '--yes-always', '--timeout', '120', '--load', str(dest / 'commands.txt'),
        '--message-file', str(dest / 'prompt.txt'), '--chat-history-file', str(dest / 'host-chat.md'),
        '--input-history-file', str(dest / 'host-input.txt')]
    save(dest / 'argv.json', argv)
    host_start = time.perf_counter()
    stdout = stderr = b''
    try:
        proc = subprocess.Popen(argv, cwd=workspace, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=130)
            result['exit_code'] = proc.returncode
        except subprocess.TimeoutExpired:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True, timeout=5)
            else:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            result.update(exit_code=None, timeout=True)
        result['host_wall_ms'] = (time.perf_counter() - host_start) * 1000
        final = target.read_bytes()
        # Only inspect the host's output; never synthesize or write its correction.
        result['candidate_sha256'] = sha(final)
        result['workspace_files'] = sorted(x.relative_to(workspace).as_posix() for x in workspace.rglob('*') if x.is_file())
        result['native_edit_observed'] = (len(calls) == 1 and calls[0].get('delivered')
            and sha(final) == calls[0].get('expected_sha256') and final != source
            and result['workspace_files'] == [task['path']]
            and b'Applied edit to ' in stdout)
        if result.get('exit_code') == 0 and result['native_edit_observed']:
            if offline and response_fixture is None:
                result['success'] = True
            else:
                check_start = time.perf_counter()
                check = subprocess.run([sys.executable, '-B', t['oracle'], str(target)],
                    cwd=workspace, env=env, capture_output=True, timeout=10)
                (dest / 'verification.stdout').write_bytes(check.stdout)
                (dest / 'verification.stderr').write_bytes(check.stderr)
                result.update(verification_ms=(time.perf_counter() - check_start) * 1000,
                    verification_exit_code=check.returncode, success=check.returncode == 0)
    except Exception as exc:
        result['host_error'] = type(exc).__name__ + ':' + str(exc)[:200]
    finally:
        server.shutdown()
        server.server_close()
        result['total_wall_ms'] = (time.perf_counter() - started) * 1000
        (dest / 'stdout.txt').write_bytes(stdout[:65536])
        (dest / 'stderr.txt').write_bytes(stderr[:8192])
    result['analytics_after'] = base.analytics()
    result['global_analytics_changed'] = result['analytics_after'] != p['analytics_before']
    result['browser_requests_suppressed'] = stderr.count(b'browser_open_suppressed')
    if not offline:
        try:
            result['provider_after'] = base.provider()
            result['provider_changed'] = result['provider_after'] != p['provider']
        except Exception as exc:
            result.update(provider_changed=True, provider_error=type(exc).__name__)
    save(dest / 'receipt.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'emit', 'probe', 'run'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cases', type=Path)
    parser.add_argument('--contract', type=Path)
    parser.add_argument('--registration', type=Path)
    parser.add_argument('--implementation-commit')
    parser.add_argument('--task', choices=TASKS)
    parser.add_argument('--arm', choices=ARMS)
    parser.add_argument('--trial-dir', type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.action == 'prepare':
        result = prepare(args)
    elif args.action == 'emit':
        emit(args.output, args.task, args.arm, args.trial_dir.resolve())
        return
    elif args.action == 'probe':
        result = trial(args.output, TASKS[0], 'focused', 0, offline=True)
    else:
        if not args.execute:
            parser.error('run requires --execute')
        if frozen(args.output)['schema'] != 'tare.tools/native-host-edit/1':
            raise ValueError('offline replay protocols cannot authorize inference')
        probe = read(args.output / 'offline-preflight/receipt.json')
        if not probe['success'] or probe['global_analytics_changed'] or probe['extra_requests']:
            raise ValueError('native offline preflight must pass first')
        records = []
        for n in (1, 2):
            for t in TASKS:
                for a in (ARMS if n == 1 else tuple(reversed(ARMS))):
                    r = trial(args.output, t, a, n)
                    records.append(r)
                    save(args.output / 'results.json', {'records': records, 'decision': decide(records)})
                    print(json.dumps({k: r[k] for k in ('round', 'task', 'arm', 'success', 'total_wall_ms')}), flush=True)
                    if r.get('provider_changed') or r['global_analytics_changed']:
                        raise ValueError('environment drift; stopped without retry')
        result = decide(records)
        save(args.output / 'decision.json', result)
    sys.stdout.buffer.write(wire(result))


if __name__ == '__main__':
    main()
