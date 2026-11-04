"""Repeat X5 controls on an exact clean revision; never grant selective admission."""
import argparse
from datetime import datetime, timezone, date
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

from specgraph.test_selection import capture, compare

REVIEW_DATE = date(2026, 10, 1)


def remove_owned_copy(copy, temporary_root):
    """Remove only the fresh clone, including Windows read-only Git objects."""
    if not copy.resolve().is_relative_to(temporary_root.resolve()):
        raise ValueError('clone cleanup must stay inside its temporary directory')
    long_copy = Path('\\\\?\\' + str(copy)) if os.name == 'nt' else copy

    def retry_readonly(function, path, error):
        if not isinstance(error[1], PermissionError):
            raise error[1]
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    if long_copy.exists():
        shutil.rmtree(long_copy, onerror=retry_readonly)


DELIVERY_COMPONENTS = ('capture', 'recapture', 'selection', 'execution',
                       'invalidation', 'retry', 'correction', 'audit')


def _seconds(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError('cost seconds must be finite, nonnegative numbers, or None')
    return value


def _total(values):
    values = [_seconds(v) for v in values]
    return _seconds(sum(values)) if all(v is not None for v in values) else None


def delivery_cost(components=None, *, baseline_seconds=None, baseline_comparable=False):
    """Only executor-supplied observations can establish operational savings."""
    if components is None:
        components = {}
    if not isinstance(components, dict) or any(name not in DELIVERY_COMPONENTS for name in components):
        raise ValueError('delivery components must be None or a dict containing only DELIVERY_COMPONENTS keys')
    observed = {name: _seconds(components.get(name)) for name in DELIVERY_COMPONENTS}
    total = _total(observed.values())
    baseline = _seconds(baseline_seconds)
    complete = total is not None and baseline is not None and baseline_comparable is True
    return {'components_seconds': observed, 'total_seconds': total,
            'baseline_seconds': baseline, 'baseline_comparable': baseline_comparable is True,
            'coverage': {k: 'UNKNOWN' if v is None else 'OBSERVED' for k, v in observed.items()},
            'status': 'OBSERVED_COMPARABLE' if complete else 'UNKNOWN',
            'net_operational_savings': baseline - total if complete and total < baseline else None}


def _observed_cost(report, rows, full):
    fields = ('command_seconds', 'command_wall_seconds', 'selection_seconds', 'setup_seconds',
              'cleanup_seconds', 'overhead_seconds', 'wall_seconds')
    costs = [row.get('observed_cost') or {} for row in rows]
    totals = {key: _total(cost.get(key) for cost in costs) for key in fields}
    phase_totals = {}
    for name in ('full_audit', 'trace_candidate', 'policy_candidate', 'confirmation'):
        counts, times = [], []
        for cost in costs:
            phases = cost.get('phases')
            count = (cost.get('phase_counts') or {}).get(name)
            if phases is None or count is None:
                counts.append(None)
                times.append(None)
            else:
                counts.append(count)
                times.append(_total(p.get('seconds') for p in phases if p.get('phase') == name))
        phase_totals[name] = {'count': _total(counts), 'seconds': _total(times)}
    capture_seconds = _seconds(report.get('capture_seconds'))
    complete = bool(rows) and all(v is not None for v in totals.values()) and all(
        p['count'] is not None and p['seconds'] is not None for p in phase_totals.values())
    total = _total([capture_seconds, totals['wall_seconds']]) if complete else None
    return {'capture_seconds': capture_seconds, 'case_totals_seconds': totals,
            'phases': phase_totals, 'total_seconds': total,
            'cost_against_full_seconds': total - full if total is not None and full is not None else None,
            'cost_ratio_against_full': total / full if total is not None and full else None,
            'coverage': {**{k: 'UNKNOWN' if v is None else 'OBSERVED' for k, v in totals.items()},
                         'capture': 'UNKNOWN' if capture_seconds is None else 'OBSERVED',
                         'phases': 'OBSERVED' if complete else 'UNKNOWN'},
            'status': 'OBSERVED_QUALIFICATION_COST' if total is not None else 'UNKNOWN'}


def assess(report):
    """Count capture once per measured batch; repeated controls are not new scope."""
    rows = report['mutations']
    full = _total(row.get('full_seconds') for row in rows)
    candidates = _total(_total([row.get('selection_seconds'), row.get('selected_seconds')]) for row in rows)
    total = _total([report.get('capture_seconds'), candidates])
    policy = _total([report.get('capture_seconds'),
                     _total(_total([row.get('selection_seconds'), row.get('policy_seconds')]) for row in rows)])
    # Model-context proxy: Tare's verify tool passes at most the first 16 KiB of output.
    context = {kind: sum(min(row.get('output_bytes', {}).get(kind, 0), 16384) for row in rows)
               for kind in ('full', 'selected', 'policy')}
    return {'projection_label': 'PROJECTION_NOT_OPERATIONAL_SAVINGS',
            'observed_qualification_cost': _observed_cost(report, rows, full),
            'delivery_cost': delivery_cost(report.get('delivery_components_seconds'),
                                           baseline_seconds=report.get('delivery_baseline_seconds'),
                                           baseline_comparable=report.get('delivery_baseline_comparable', False)),
            'full_seconds': full, 'capture_plus_candidates_seconds': total,
            'net_saved_seconds': full - total if full is not None and total is not None else None,
            'total_cost_ratio': total / full if full and total is not None else None,
            'decision': 'REVISE_COVERAGE' if not report['correctness_preserved'] else
                        'UNKNOWN' if total is None or full is None else
                        'REVISE_COST' if total >= full else 'EXPAND_TO_REAL_CHANGES',
            # Traced ∪ static-import tests, full suite for unreached files.
            'policy_seconds': policy, 'policy_cost_ratio': policy / full if full and policy is not None else None,
            'context_bytes_capped_16k': context,
            'truncated_full_outputs': sum(row.get('output_bytes', {}).get('full', 0) > 16384 for row in rows),
            'policy_omission_cases': sum(bool(row['policy_omitted']) for row in rows),
            'policy_decision': 'REVISE_COVERAGE' if not report['policy_correctness_preserved'] else
                               'UNKNOWN' if policy is None or full is None else
                               'REVISE_COST' if policy >= full else 'READY_FOR_SCOPED_GATE',
            'selective_execution_qualified': False}


def cohort(real_cases):
    """Distinct real changes are commits; repeated runs of them add stability, not scope."""
    return {'cases': len(real_cases), 'commits': len({c['commit'] for c in real_cases}),
            'modules': sorted({c['path'] for c in real_cases})}


def main():
    started = time.perf_counter()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(root):
        raise ValueError('campaign evidence must stay outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    with tempfile.TemporaryDirectory(prefix='x5-campaign-') as td:
        copy = Path(td) / 'repo'
        try:
            subprocess.run(['git', '-c', 'core.longpaths=true', 'clone', '--shared', '--no-checkout',
                            str(root), str(copy)], check=True)
            subprocess.run(['git', '-C', str(copy), '-c', 'core.longpaths=true', 'checkout',
                            '--detach', commit], check=True)
            receipt = capture(copy, Path(td) / 'capture.json')
            mutation_bytes = (copy/'tools/fixtures/x5/x5-campaign-mutations.json').read_bytes()
            real_bytes = (copy/'tools/fixtures/x5/x5-real-changes.json').read_bytes()
            real = json.loads(real_bytes)
            report = compare(copy, receipt, json.loads(mutation_bytes) + real)
            value = {'schema': 'specgraph-x5-campaign/1', 'revision': commit,
                     'observed_at': datetime.now(timezone.utc).isoformat(),
                     'review_due': False, 'review_date': REVIEW_DATE.isoformat(),
                     'review_decision': {'recorded_on': '2026-10-14',
                                         'decision': 'BROAD_ADOPTION_DECLINED_FOR_CURRENT_CYCLE',
                                         'authority': 'NONE'},
                     'scope': 'fixed controls plus real historical fixes undone at this revision; '
                              'not independent attestation',
                     'real_changes': cohort(real),
                     'environment': receipt['environment'], 'baseline_tests': len(receipt['tests']),
                     'case_set_sha256': hashlib.sha256(mutation_bytes + real_bytes).hexdigest(),
                     'capture_sha256': hashlib.sha256((Path(td)/'capture.json').read_bytes()).hexdigest(),
                     'elapsed_seconds': time.perf_counter() - started,
                     'assessment': assess(report), 'comparison': report}
        finally:
            remove_owned_copy(copy, Path(td))
    # Wall includes clone, capture, comparison and owned-copy/temp cleanup.
    value['elapsed_seconds'] = time.perf_counter() - started
    (output/'result.json').write_text(json.dumps(value, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    assessment = value['assessment']
    observed = assessment['observed_qualification_cost']
    summary = ('## X5 qualification campaign\n\n'
               f"Revision: `{commit}`. Historical review deadline: **{REVIEW_DATE}**. "
               'Broad adoption declined on 2026-10-14; no operational savings established.\n\n'
               f"Campaign wall (elapsed_seconds): {value['elapsed_seconds']:.2f}s.\n\n"
               'PROJECTION_NOT_OPERATIONAL_SAVINGS: '
               f"full={assessment['full_seconds']}s; capture + candidates="
               f"{assessment['capture_plus_candidates_seconds']}s; policy={assessment['policy_seconds']}s.\n\n"
               f"Projection decisions: {assessment['decision']} / {assessment['policy_decision']}.\n"
               f"Observed qualification cost: {observed['total_seconds']}s ({observed['status']}).\n"
               f"Against full: {observed['cost_against_full_seconds']}s; coverage: {observed['coverage']}.\n\n"
               f"Delivery cost: {assessment['delivery_cost']['status']}; "
               f"components: {assessment['delivery_cost']['components_seconds']}.\n"
               'No operational savings established. Selective execution remains unqualified.\n\n'
               f"Real changes: {value['real_changes']['cases']} cases from "
               f"{value['real_changes']['commits']} commits in {len(value['real_changes']['modules'])} modules.\n"
               f"Policy omission cases: {assessment['policy_omission_cases']}.\n"
               'Repeated runs measure stability, not additional coverage. New adoption requires '
               'scoped executor evidence and complete comparable delivery costs.\n')
    (output/'summary.md').write_text(summary, encoding='utf-8')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as stream:
            stream.write(summary)
    print(summary)
    if not report['correctness_preserved']:
        print('::warning::X5 found omitted failing tests; selective execution remains suspended.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
