"""Real offline Kernel qualification of one known pure-Python fixture, not adoption."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from specgraph import test_selection as selection
from specgraph.evidence import AttestationVerdict, evidence_payload_sha256
from specgraph.x5_scope import build_x5_scope

kernel = pytest.importorskip('compute_plane.managed_work')
process = pytest.importorskip('compute_plane.managed_process')


def test_real_executor_scoped_c5_and_invalidation(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5:AC-01
    @verifies SPEC-SPECGRAPH-X5:AC-02
    @verifies SPEC-SPECGRAPH-X5:AC-03

    Completeness is limited to these authored files and this installed environment.
    No repository test suite, private evidence, signer or synthetic Kernel is used.
    """
    root = tmp_path.resolve()
    project = root.as_posix()
    contract = 'AC-1: the known pure Python fixture returns one; all four tests pass.\n'
    files = {
        'src/calc/__init__.py': 'def value():\n    return 1\n',
        'tests/test_trace.py': (
            'from pathlib import Path\nimport calc\n'
            'def test_trace():\n'
            '    assert Path(calc.__file__).resolve() == Path(__file__).resolve().parents[1] / \"src/calc/__init__.py\"\n'
            '    assert calc.value() == 1\n'),
        'tests/test_static.py': (
            'from calc import value\nCOLLECTED = value()\n'
            'def test_static():\n    assert COLLECTED == 1\n'),
        'tests/test_required.py': 'def test_required():\n    assert 2 + 2 == 4\n',
        'tests/test_other.py': 'def test_other():\n    assert True\n',
        'pyproject.toml': '[tool.pytest.ini_options]\npythonpath = [\"src\"]\n',
        'SPEC.md': contract,
    }
    # Keep the strings above ordinary Python source, not escaped fixture syntax.
    files = {name: text.replace('\\"', '"') for name, text in files.items()}
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    monkeypatch.setenv('PYTHONPATH', str(Path(selection.__file__).resolve().parents[1]))
    monkeypatch.setenv('PYTEST_DISABLE_PLUGIN_AUTOLOAD', '1')
    for name in ('PYTEST_ADDOPTS', 'PYTEST_PLUGINS'):
        monkeypatch.delenv(name, raising=False)

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def digest(value):
        return sha(json.dumps(value, sort_keys=True, ensure_ascii=False,
                              separators=(',', ':'), allow_nan=False).encode())

    baseline = selection.capture(root, root / 'baseline.json', pytest_args=['tests'])
    assert baseline['suite_returncode'] == 0 and len(baseline['tests']) == 4
    source = root / 'src/calc/__init__.py'
    source.write_text('def value():\n    return 1 + 0\n', encoding='utf-8')
    changed = ['src/calc/__init__.py:2']
    trace = 'tests/test_trace.py::test_trace'
    static = 'tests/test_static.py::test_static'
    mandatory = ['tests/test_required.py::test_required']
    selected = sorted([trace, static, *mandatory])
    assert changed[0] in baseline['executed_blocks'][trace]
    assert changed[0] not in baseline['executed_blocks'][static]
    target_path = root / 'target.json'
    observations = []

    def runner(argv, *, cwd, timeout, env, **kwargs):
        # Kernel supplies a restricted environment; retain the explicit import path.
        child_env = dict(env, PYTHONPATH=str(Path(selection.__file__).resolve().parents[1]),
                         PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
        metadata, stdout, stderr = process.run_pipe_process(
            argv, cwd=cwd, timeout=timeout, env=child_env)
        assert timeout == 90
        assert metadata['started'] and metadata['cleanup_verified']
        assert not any(metadata[k] for k in ('timeout', 'cancelled', 'output_limit'))
        assert metadata['exit_code'] == 0, (metadata, stdout, stderr)
        observations.append((tuple(argv), dict(metadata), sha(stdout), sha(stderr)))
        return subprocess.CompletedProcess(argv, metadata['exit_code'], stdout, stderr)

    command = (sys.executable, '-m', 'specgraph.cli', '--root', str(root),
               'capture-tests', str(target_path), '--test-path', 'tests')
    check = kernel.VerificationCommand('AC-1', command, timeout_seconds=90)
    tracked = sorted(files)
    progress = kernel.observe_work_progress(
        workspace=root, exit_criteria=['AC-1'], checks=[check],
        tracked_paths=tracked, runner=runner)
    assert progress.remaining_obligations == 0 and progress.verified_evidence_count == 1
    assert sha(progress.evidence_bytes) == progress.evidence_sha256
    receipt_path = root / 'kernel-receipt.json'
    receipt_path.write_bytes(progress.evidence_bytes)
    kernel_receipt = json.loads(receipt_path.read_bytes())['receipts'][0]
    assert kernel_receipt['status'] == 'PASS' and kernel_receipt['exit_code'] == 0
    assert kernel_receipt['command_sha256'] == sha(json.dumps(
        list(command), ensure_ascii=False, separators=(',', ':')).encode())
    assert kernel_receipt['stdout_sha256'] == observations[0][2]
    assert kernel_receipt['stderr_sha256'] == observations[0][3]
    target = json.loads(target_path.read_text(encoding='utf-8'))
    assert target['suite_returncode'] == 0
    assert set(target['tests']) == set(baseline['tests'])
    assert target['fingerprints'] == selection._files(root)
    assert target['fingerprints']['src/calc/__init__.py'] != baseline['fingerprints']['src/calc/__init__.py']
    assert changed[0] in target['executed_blocks'][trace]
    assert changed[0] not in target['executed_blocks'][static]
    for blocks in target['executed_blocks'].values():
        for block in blocks:
            name, line = block.rsplit(':', 1)
            path = (root / name).resolve(strict=True)
            assert path.is_relative_to(root)
            assert name in target['fingerprints']
            assert 1 <= int(line) <= len(path.read_bytes().splitlines())

    def current():
        fingerprints = selection._files(root)
        source_hash = sha((root / 'SPEC.md').read_bytes())
        return {
            'snapshot': {'repository': project, 'revision_seq': 2,
                         'content_hash': digest(fingerprints)},
            'config_sha256': sha(json.dumps({k: v for k, v in fingerprints.items()
                if Path(k).suffix in selection.CONFIG}, sort_keys=True).encode()),
            'environment_sha256': sha(json.dumps(selection._environment(), sort_keys=True).encode()),
            'source_hashes': {f'{project}:SPEC.md': source_hash},
            'criterion_hashes': {f'{project}:SPEC.md#AC-1': source_hash},
            'dependency_hashes': {f'{project}:{k}': v for k, v in fingerprints.items()},
            'artifact_hashes': {p.as_uri(): sha(p.read_bytes())
                                for p in (target_path, receipt_path)},
        }

    no_c5 = selection.recommend_from_receipt(changed, baseline, root=root, mandatory=mandatory)
    assert no_c5['mode'] == 'FULL_SUITE'
    assert no_c5['policy_tests'] == selected
    assert static not in no_c5['candidates']
    scope = build_x5_scope(root, changed, baseline, selected, mandatory)
    observed = current()
    record = dict(schema='tare.tools/execution-evidence/1', evidence_id='fixture-target',
                  executor='kernel-fixture-executor', observed_at='2026-10-14',
                  relation='EXECUTED_COVERAGE', outcome='PASSED', command=list(command),
                  **{k: observed[k] for k in ('snapshot', 'config_sha256',
                     'environment_sha256', 'source_hashes', 'criterion_hashes', 'artifact_hashes')})
    record.update(dependencies={'complete': True, 'hashes': observed['dependency_hashes']},
                  coverage={'method': 'Kernel-contained target capture of known fixture',
                            'tests': [f'{project}:{t}' for t in target['tests']],
                            'symbols': sorted({f'{project}:{b}' for blocks in
                                               target['executed_blocks'].values() for b in blocks})},
                  x5_scope_sha256=digest(scope), attestation_ref=receipt_path.as_uri())
    accepted_subject = evidence_payload_sha256(record)
    accepted_observation = digest(observed)
    accepted_receipt = progress.evidence_sha256
    accepted_process = digest(observations[0])

    def verifier(claim):
        valid = (evidence_payload_sha256(claim) == accepted_subject
                 and claim.get('attestation_ref') == receipt_path.as_uri()
                 and sha(receipt_path.read_bytes()) == accepted_receipt
                 and sha(progress.evidence_bytes) == accepted_receipt
                 and digest(current()) == accepted_observation
                 and digest(observations[0]) == accepted_process)
        return AttestationVerdict('VERIFIED' if valid else 'REJECTED',
                                  accepted_subject, receipt_path.as_uri(), 'fixture-executor-custody')

    def recommend(claim=record):
        return selection.recommend_from_receipt(
            changed, baseline, root=root, mandatory=mandatory, c5_evidence=claim,
            c5_current=current(), attestation_verifier=verifier)

    result = recommend()
    assert result['mode'] == 'SELECTIVE_SCOPED_C5'
    assert result['tests'] == result['policy_tests'] == selected
    assert result['command'] == [sys.executable, '-m', 'pytest', *selected]
    assert verifier(dict(record, evidence_id='different')).status == 'REJECTED'
    # Re-read observations and re-evaluate immediately before executing.
    result = recommend()
    assert result['mode'] == 'SELECTIVE_SCOPED_C5'
    selected_run = runner(result['command'], cwd=root, timeout=90, env=dict(__import__('os').environ))
    assert selected_run.returncode == 0 and observations[-1][1]['cleanup_verified']
    artifact_bytes = target_path.read_bytes()
    target_path.write_bytes(artifact_bytes + b' ')
    assert verifier(record).status == 'REJECTED'
    assert recommend()['mode'] == 'FULL_SUITE'
    target_path.write_bytes(artifact_bytes)
    assert recommend()['mode'] == 'SELECTIVE_SCOPED_C5'
    source.write_text('def value():\n    return 2\n', encoding='utf-8')
    assert verifier(record).status == 'REJECTED'
    assert recommend()['mode'] == 'FULL_SUITE'
