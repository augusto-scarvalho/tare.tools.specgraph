"""Observe Aider requests without filtering, rewriting or applying model edits."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
import urllib.request

from pilot_profiles import response_usage, validate_wire


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def request_purpose(request):
    """Recognize the installed Aider summarizer by its system instruction.

    This is a host-specific observation, not semantic classification of user
    text. Preserve all requests and their tokens regardless of purpose.
    """
    marker = '*Briefly* summarize this partial conversation about programming.'
    return 'history_summary' if any(m.get('role') == 'system' and isinstance(m.get('content'), str)
        and m['content'].startswith(marker) for m in request.get('messages', [])) else 'task'


@contextmanager
def observer(dest, profile, *, endpoint, max_calls, request_timeout=120, backend='qwen', offline_response=None):
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    calls = []; guard = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass

        def do_POST(self):
            with guard:
                number = len(calls) + 1
                row = {'number': number, 'usage': None, 'forwarded': False, 'response_delivered': False}
                calls.append(row)
            folder = dest / f'call-{number}'; folder.mkdir()
            start = time.perf_counter(); status = 200
            try:
                if self.path != '/v1/chat/completions':
                    raise ValueError('unexpected endpoint')
                if number > max_calls:
                    raise ValueError('request budget exhausted')
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 4 * 1024 * 1024:
                    raise ValueError('invalid request byte budget')
                raw = self.rfile.read(size); request = json.loads(raw)
                row['purpose'] = request_purpose(request)
                save(folder / 'request.json', request)
                if backend == 'qwen':
                    validate_wire(request, profile)
                elif request.get('max_tokens') != profile['max_tokens'] or request.get('temperature') != profile['temperature'] or request.get('stream', False):
                    raise ValueError('Bedrock request profile mismatch')
                if offline_response is not None:
                    response = offline_response(request, number)
                elif backend == 'bedrock':
                    from bedrock_observed import converse
                    row['forwarded'] = True
                    response = converse(request, folder)
                else:
                    row['forwarded'] = True
                    req = urllib.request.Request(endpoint.rstrip('/') + '/chat/completions', raw,
                                                 {'Content-Type': 'application/json'})
                    with urllib.request.urlopen(req, timeout=request_timeout) as upstream:
                        response = json.loads(upstream.read(4 * 1024 * 1024 + 1))
                save(folder / 'response.json', response)
                row['usage'] = response_usage(response)
                row['finish_reasons'] = [c.get('finish_reason') for c in response.get('choices', [])]
                # Record content independently of completion; don't parse an edit here.
                row['has_final_content'] = any(bool(c.get('message', {}).get('content')) for c in response.get('choices', []))
                row['upstream_response_received'] = True
                body = json.dumps(response).encode()
            except Exception as exc:
                status = 400 if not row['forwarded'] else 502
                row['error'] = f'{type(exc).__name__}: {exc}'
                body = json.dumps({'error': {'message': row['error'], 'type': 'pilot_observer'}}).encode()
            try:
                self.send_response(status); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
                row['response_delivered'] = status == 200
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                row['delivery_error'] = True
            finally:
                row['wall_ms'] = (time.perf_counter() - start) * 1000
                save(folder / 'receipt.json', row)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    # server_close waits for outstanding upstream calls, bounded by request_timeout.
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def summarize_calls(calls):
    forwarded = [c for c in calls if c.get('forwarded')]
    measured = bool(forwarded) and all(c.get('usage') is not None for c in forwarded)
    auxiliary = [c for c in calls if c.get('purpose') == 'history_summary']
    task_requests = len(calls) - len(auxiliary)
    return {'attempted_requests': len(calls), 'upstream_requests': len(forwarded),
        'task_requests': task_requests, 'auxiliary_requests': len(auxiliary),
        'extra_attempts': max(0, task_requests - 1), 'upstream_usage_complete': measured,
        'total_tokens': sum(c['usage']['total_tokens'] for c in forwarded) if measured else None,
        'transport_errors': [c['number'] for c in calls if c.get('error') or c.get('delivery_error')],
        'unobserved_upstream_tokens': any(c.get('usage') is None for c in forwarded)}


def aider_completion(process, calls):
    task_calls = [c for c in calls if c.get('purpose') != 'history_summary']
    last = task_calls[-1] if task_calls else {}
    # Earlier failed/truncated responses can be repaired within the declared
    # attempt budget. Keep their costs/errors without vetoing a completed retry.
    complete = bool(process['exit_code'] == 0 and not process['timeout']
        and last.get('response_delivered') and last.get('has_final_content') and last.get('finish_reasons') == ['stop'])
    return {'completed': complete, 'reason': 'AIDER_EXIT_AND_FINAL_RESPONSE' if complete else 'INCOMPLETE_HOST_OR_RESPONSE'}
