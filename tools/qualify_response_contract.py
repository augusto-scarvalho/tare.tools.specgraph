"""Freeze and execute a response-profile ablation, with X1 inputs unchanged."""
import argparse
import json
from pathlib import Path

try:
    from . import qualify_context_selection as base
    from .response_contract import PROFILES, response_decision
    from .native_edit_contract import native_decision
except ImportError:
    import qualify_context_selection as base
    from response_contract import PROFILES, response_decision
    from native_edit_contract import native_decision


def prepare(args):
    base.prepare(args)
    p = base.read(args.output / 'protocol.json')
    p.update(schema='tare.tools/response-contract/1', work_id='SPECGRAPH-RESPONSE-CONTRACT-20260908',
             response_profiles=list(PROFILES), context_policy='simple',
             scope='Response profile on two reused historical repairs; not context-selector comparison',
             decision_rule='All four direct-answer patches pass; complete counters and stable provider; tokens and total wall time no greater than control in either round. Otherwise reject or report incomplete.')
    if args.native_edit:
        p.update(schema='tare.tools/native-edit-format/1',response_profiles=['native-edit'],
                 scope='Four native edit proposals on reused tasks; no paired savings claim',
                 decision_rule='Four complete source-exact patches pass all frozen checks with known counters, stable instance and no global side effects; otherwise reject or report incomplete.')
    for file in [Path(__file__), args.tests, args.transport_receipt, args.work_registration]:
        file = file.resolve()
        p['files'][str(file)] = base.sha(file.read_bytes())
    if base.read(args.transport_receipt).get('status') != 'PASS':
        raise ValueError('native offline client transport proof required before inference')
    base.save(args.output / 'protocol.json', p)
    base.save(args.output / 'protocol-lock.json', {'sha256':base.sha(base.wire(p))})
    return {'status':'FROZEN_NO_INFERENCE', 'sha256':base.sha(base.wire(p)), 'trials':4 if args.native_edit else 8}


def run(out):
    p = base.frozen(out)
    native = p['schema'] == 'tare.tools/native-edit-format/1'
    if p['schema'] not in {'tare.tools/response-contract/1','tare.tools/native-edit-format/1'}:
        raise ValueError('response protocol required')
    if (out / 'results.json').exists() or any(out.glob('r*-*')):
        raise ValueError('single-use experiment: refuse replay into existing custody')
    rows = []
    tasks = [t['id'] for t in p['tasks']]
    for number in (1, 2):
        for task in tasks:
            for profile in (('native-edit',) if native else PROFILES if number == 1 else tuple(reversed(PROFILES))):
                result = base.trial(out, task, 'simple', number, response_profile=profile)
                rows.append(result)
                decision = (native_decision if native else response_decision)(rows, tasks)
                base.save(out / 'results.json', {'records':rows,'decision':decision})
                print(json.dumps({k:result[k] for k in ('round','task','response_profile','success','total_wall_ms')}),flush=True)
                if result.get('global_analytics_changed') or result.get('provider_changed'):
                    raise ValueError('instance/configuration changed; no automatic retry')
    base.save(out / 'decision.json', decision)
    return decision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','run'])
    parser.add_argument('--output', type=Path, required=True)
    for name in ('prepared','selector','contract','tests','transport-receipt','work-registration'):
        parser.add_argument('--'+name,type=Path)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--native-edit',action='store_true')
    args = parser.parse_args()
    args.output = args.output.resolve()
    if args.action == 'run' and not args.execute:
        parser.error('run requires --execute')
    result = prepare(args) if args.action == 'prepare' else run(args.output)
    print(base.wire(result).decode(),end='')


if __name__ == '__main__':
    main()
