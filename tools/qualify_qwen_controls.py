"""Check live Jinja controls and a captured, bounded native-budget response.

No inference is generated here. Supply the raw request/response from a small
live probe and identity captured before that probe. Backend inspection uses the
same SSH/stdin collector as the pilot. This qualifies transport capability,
never correctness or an optimal reasoning budget.
"""
import argparse
import json
from pathlib import Path
import sys

from pilot_process import run_process
from pilot_profiles import PROFILES, digest, identity_fingerprint, qwen_profile, request_parameters, validate_wire
from pilot_transport import save


def qualify(host, before, request, response, output):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    profile = qwen_profile('native-budget-probe', thinking=True,
        effort=request['chat_template_kwargs']['reasoning_effort'],
        reasoning_tokens=request['reasoning_budget_tokens'],
        answer_tokens=request['max_tokens'] - request['reasoning_budget_tokens'] - 128,
        seed=request['seed'])
    validate_wire(request, profile)
    templates = {}
    for name, configured in PROFILES.items():
        wire = request_parameters(configured); wire.update(wire.pop('extra_body'))
        wire.update(model='qwen38', messages=[{'role': 'user', 'content': 'Template control; no inference.'}])
        templates[name] = wire
    reasoning = response['choices'][0]['message'].get('reasoning_content', '')
    collector = Path(__file__).with_name('pilot_provider.py').read_text(encoding='utf-8').split("if __name__ == '__main__':")[0]
    # Serialize into Python literals, never interpolate source into a shell.
    remote = collector + '\nINPUT = ' + repr({'templates': templates, 'reasoning': reasoning}) + '''
parser = argparse.ArgumentParser()
parser.add_argument('--alias', default='qwen38')
parser.add_argument('--port', type=int, default=18080)
parser.add_argument('--proof', required=True)
args = parser.parse_args()
def post(route, data):
    req = urllib.request.Request(f'http://127.0.0.1:{args.port}/' + route,
        json.dumps(data).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as result: return json.load(result)
result = {'identity': capture(args.alias, args.port, args.proof),
          'templates': {name: post('apply-template', body) for name, body in INPUT['templates'].items()},
          'reasoning_tokenization': post('tokenize', {'content': INPUT['reasoning'], 'add_special': False})}
print(json.dumps(result))
'''
    argv = [sys.executable if arg == '{python}' else arg for arg in host['identity_command']]
    proc = run_process(argv, cwd=Path(__file__).parent, dest=output / 'backend', timeout=180, stdin=remote.encode())
    if proc['exit_code'] != 0 or not proc['cleanup_verified']:
        raise ValueError('live backend inspection failed')
    inspected = json.loads((output / 'backend/stdout.txt').read_text(encoding='utf-8'))
    identity = identity_fingerprint(inspected['identity'])
    stable = identity == identity_fingerprint(before)
    checks = {}
    for name, configured in PROFILES.items():
        prompt = inspected['templates'][name]['prompt']
        enabled = prompt.endswith('<|im_start|>assistant\n<think>\n')
        if configured['thinking']:
            effort = configured['effort']
            correct = enabled and (('Reasoning effort is set to low.' in prompt) if effort == 'low'
                                   else 'Reasoning effort is set to ' not in prompt)
        else:
            correct = prompt.endswith('<|im_start|>assistant\n<think>\n\n</think>\n\n')
        checks[name] = {'passed': correct, 'profile_sha256': digest(configured), 'request': templates[name]}
    token_count = len(inspected['reasoning_tokenization']['tokens'])
    # Retokenizing the separated content can differ at the opening delimiter.
    # Keep raw IDs/counts; do not present this as provider usage accounting.
    cap = request['reasoning_budget_tokens']
    budget_effect = bool(reasoning) and cap - 2 <= token_count <= cap
    final = response['choices'][0]['message'].get('content')
    cache_zero = response.get('timings', {}).get('cache_n') == 0
    result = {'schema': 'tare.tools/qwen-transport-controls/1', 'identity_fingerprint': identity,
        'provider_stable': stable, 'profiles': checks, 'probe_request': request, 'probe_response': response,
        'native_reasoning_budget': cap, 'retokenized_reasoning_content_tokens': token_count,
        'native_budget_effect_observed': budget_effect, 'final_content_observed': bool(final),
        'cache_reuse_disabled_observed': cache_zero,
        'finish_reason': response['choices'][0]['finish_reason'], 'quality_qualified': False,
        'scope': 'template settings and native budget capability; not exact usage attribution or budget optimality',
        'qualified': stable and all(x['passed'] for x in checks.values()) and budget_effect and bool(final) and cache_zero}
    save(output / 'controls.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('host', 'before', 'request', 'response', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    result = qualify(read(args.host), read(args.before), read(args.request), read(args.response), args.output)
    print(json.dumps({k: result[k] for k in ('qualified', 'finish_reason', 'retokenized_reasoning_content_tokens', 'quality_qualified')}))
    sys.exit(0 if result['qualified'] else 1)
