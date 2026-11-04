"""Replay rejected absolute-path responses through native Aider; zero inference.

Only relocate the response's path to the new isolated root. Code edits remain
identical to the captured responses. Original paired outcomes stay unchanged.
"""
import argparse
import copy
import json
from pathlib import Path
import sys

import qualify_native_host as native


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    evidence, output = args.evidence.resolve(), args.output.resolve()
    original = native.read(evidence / 'protocol.json')
    if native.sha(native.wire(original)) != native.read(evidence / 'protocol-lock.json')['sha256']:
        raise ValueError('original protocol changed')
    output.mkdir(exist_ok=False)
    records = []
    for prior in native.read(evidence / 'results.json')['records']:
        if prior['task'] != 'receipt-write-release':
            continue
        identity = f"r{prior['round']}-{prior['task']}-{prior['arm']}"
        src = evidence / 'trials' / identity
        response = native.read(src / 'response.json')
        before = native.base.observe_edit_response(response)['answer']
        header = json.loads((src / 'packet.txt').read_text(encoding='utf-8').splitlines()[0])
        expected_original = (Path(header['edit_root']) / header['file']).resolve()
        if Path(before['path']).resolve() != expected_original:
            raise ValueError('recorded path did not identify the original authorized file')
        dest = output / identity
        dest.mkdir()
        relocated = str(dest / 'offline-preflight/workspace' / header['file'])
        fixture = copy.deepcopy(response)
        content = fixture['choices'][0]['message']['content']
        if not content.startswith(before['path'] + '\n'):
            raise ValueError('only a leading path relocation is supported')
        fixture['choices'][0]['message']['content'] = relocated + content[len(before['path']):]
        after = native.base.observe_edit_response(fixture)['answer']
        if any(before[k] != after[k] for k in ('old', 'new')):
            raise ValueError('replay must preserve exact original code edits')
        p = copy.deepcopy(original)
        p.update(schema='tare.tools/native-host-path-replay/1', model_inference_authorized=False,
            scope='Offline replay; path relocation only; original utility decision unchanged',
            original_response_sha256=native.sha((src / 'response.json').read_bytes()),
            fixture_sha256=native.sha(native.wire(fixture)), original_trial=identity,
            original_protocol_sha256=native.sha(native.wire(original)))
        # The current gate is deliberately distinct from the archived v1.0 gate.
        p['files'] = {path: native.sha(Path(path).read_bytes()) for path in original['files']}
        p['files'][str(Path(__file__).resolve())] = native.sha(Path(__file__).read_bytes())
        p['budgets']['real_calls'] = 0
        p['implementation_commit'] = 'LOCAL_PATH_FIX; hashes frozen, not original inference implementation'
        native.save(dest / 'protocol.json', p)
        native.save(dest / 'protocol-lock.json', {'sha256': native.sha(native.wire(p))})
        native.save(dest / 'fixture.json', fixture)
        r = native.trial(dest, prior['task'], prior['arm'], prior['round'],
            offline=True, response_fixture=fixture)
        records.append({'original_trial': identity, 'success': r['success'],
            'native_edit_observed': r.get('native_edit_observed'), 'extra_requests': r['extra_requests'],
            'global_analytics_changed': r['global_analytics_changed'],
            'verification_exit_code': r.get('verification_exit_code'),
            'real_model_calls': sum(bool(c['forwarded']) for c in r['calls']),
            'total_wall_ms': r['total_wall_ms'], 'original_response_sha256': p['original_response_sha256'],
            'fixture_sha256': p['fixture_sha256'], 'code_replacement_unchanged': True})
        native.save(output / 'results.json', records)
        print(json.dumps(records[-1]), flush=True)
    passed = len(records) == 4 and all(r['success'] and r['real_model_calls'] == 0
        and r['extra_requests'] == 0 and not r['global_analytics_changed'] for r in records)
    native.save(output / 'decision.json', {'status': 'PASS' if passed else 'FAIL',
        'scope': 'Native offline regression of path fix; not new inference or paired benefit',
        'real_model_calls': 0, 'original_decision_unchanged': True})
    if not passed:
        sys.exit(1)


if __name__ == '__main__':
    main()
