"""Diagnostic host composition through public Kernel/Runtime APIs; no second loop."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def validate_host(host):
    sys.path[:0] = [host['kernel_path'], str(Path(host['runtime_path'])/'src')]
    from compute_plane.http_transport import validate
    config = host['http_config']
    validate(config)
    if config.get('host') != 'http' or config['model'] != host['model']:
        raise ValueError('HTTP host/model configuration mismatch')
    if host['kind'] == 'http-qwen':
        from pilot_profiles import validate_wire
        validate_wire({**config['http']['parameters'], 'model': config['model'],
                       'messages': [{'role': 'user', 'content': 'preflight; no inference'}]}, host['profile'])


def execute(request):
    sys.path[:0] = [request['kernel_path'], str(Path(request['runtime_path'])/'src')]
    from compute_plane.agent_session import Session
    from compute_plane.http_runtime import HttpTurnExecutor
    from tare_tools_agent_runtime import AgentRuntime, BindingRef, RunBudget
    config = request['config']
    if config.get('api_key_env') == 'AWS_BEARER_TOKEN_BEDROCK':
        import keyring
        credential = keyring.get_password('universal-agent-harness', 'AWS_BEARER_TOKEN_BEDROCK')
        if not credential:
            raise ValueError('Bedrock keyring credential unavailable')
        os.environ[config['api_key_env']] = credential

    class DiagnosticAuthority:
        def is_current(self, binding): return True
        def submit_terminal_claim(self, claim): return True

    with Session(config, request['custody']) as session:
        runtime = AgentRuntime(HttpTurnExecutor(session), DiagnosticAuthority())
        outcome = runtime.run(binding=BindingRef(request['id'], 'diagnostic', 'pilot', 1, 'no-production-authority'),
            instruction=request['instruction'], budget=RunBudget(max_turns=request['max_turns'], wall_clock_seconds=request['wall_seconds']))
        calls = session.state.get('model_observations', [])
        local_valid = []
        for call in calls:
            if request.get('qwen_profile'):
                from pilot_profiles import validate_wire
                artifact = Path(call['artifact'])
                if not artifact.is_file():
                    local_valid.append(False); continue
                payload = json.loads(artifact.read_text(encoding='utf-8'))
                for raw in payload['calls']:
                    try:
                        validate_wire(raw['request'], request['qwen_profile'])
                        local_valid.append(raw.get('response', {}).get('timings', {}).get('cache_n') == 0)
                    except (ValueError, KeyError):
                        local_valid.append(False)
        observations = session.state['observations']
        result = {'completed': outcome.status.value == 'SUCCEEDED', 'status': outcome.status.value,
            'calls': calls, 'tokens': session.state['tokens'], 'turns': session.state['turns'],
            'receipts': session.state['receipts'], 'authority': 'NONE',
            'wire_profile_verified': all(local_valid) if local_valid else None,
            'token_counter_queries': sum(o.get('kind') in {'apply_template','tokenize','count_tokens'} for o in observations),
            'observed_http_requests': sum('request_sha256' in o for o in observations),
            'model_process_cleanup_verified': all(o['cleanup_verified'] for o in observations if o.get('kind') == 'http_process')}
    return result


if __name__ == '__main__':
    request = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    result = execute(request)
    Path(request['result']).write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'calls','receipts'}}), flush=True)
