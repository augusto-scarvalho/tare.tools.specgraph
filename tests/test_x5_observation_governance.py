"""Focused offline falsifier for incomplete X5 observation receipts."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from specgraph import test_selection


def test_run_rejects_incomplete_receipt_with_nonempty_inventory(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5 [AC-01, AC-03]

    An incomplete local observation must not be accepted as a usable run merely
    because its inventory is nonempty and pytest returned zero.
    """
    def synthetic_pytest(command, *, cwd, env, capture_output, text):
        assert Path(cwd) == tmp_path.resolve()
        assert capture_output and text
        Path(env['SG_X5_OUTPUT']).write_text(json.dumps({
            'source_origin': None,
            'tests': ['test_tiny.py::test_ok'],
            'executed_blocks': {'test_tiny.py::test_ok': []},
            'failed': [],
            'exitstatus': 0,
            'complete': False,
            'truncated': True,
            'reason': 'BYTE_LIMIT',
        }), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    monkeypatch.setattr(test_selection.subprocess, 'run', synthetic_pytest)
    with pytest.raises(RuntimeError, match='(?i)incomplete|truncat'):
        test_selection._run(tmp_path, ['test_tiny.py'], trace=True)


@pytest.mark.parametrize('flags', [{'complete': False}, {'truncated': True}])
def test_run_rejects_each_explicit_incomplete_flag(tmp_path, monkeypatch, flags):
    """@verifies SPEC-SPECGRAPH-X5:AC-03"""
    def run(command, **kwargs):
        Path(kwargs['env']['SG_X5_OUTPUT']).write_text(json.dumps({
            'tests': ['test_a.py::test_a'], 'executed_blocks': {}, **flags,
        }), encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(test_selection.subprocess, 'run', run)
    with pytest.raises(RuntimeError, match='incomplete/truncated'):
        test_selection._run(tmp_path, [], trace=True)


@pytest.mark.parametrize('case', ['legacy', 'complete', 'empty', 'collection', 'origin', 'missing'])
def test_run_legacy_and_existing_rejections(tmp_path, monkeypatch, case):
    """@verifies SPEC-SPECGRAPH-X5:AC-01"""
    def run(command, **kwargs):
        assert kwargs['cwd'] == tmp_path.resolve()
        assert 'SG_X5_TRACE' not in kwargs['env']
        assert Path(kwargs['env']['PYTHONPATH'].split(__import__('os').pathsep)[1], '_pytest_x5.py').is_file()
        payload = {'tests': ['test_a.py::test_a'], 'executed_blocks': {}, 'failed': []}
        if case == 'complete':
            payload.update(complete=True, truncated=False)
        if case == 'empty':
            payload['tests'] = []
        if case == 'origin':
            payload['source_origin'] = str(tmp_path.parent / 'foreign' / '__init__.py')
        if case != 'missing':
            Path(kwargs['env']['SG_X5_OUTPUT']).write_text(json.dumps(payload), encoding='utf-8')
        return SimpleNamespace(returncode=2 if case == 'collection' else 0, stdout='diagnostic', stderr='')
    monkeypatch.setenv('SG_X5_TRACE', '1')
    monkeypatch.setattr(test_selection.subprocess, 'run', run)
    if case in ('legacy', 'complete'):
        result = test_selection._run(tmp_path, [])
        assert result['tests'] == ['test_a.py::test_a']
        assert 'executed_blocks' not in result
        assert result['returncode'] == 0 and result['seconds'] >= 0
        assert result['output_bytes'] == len('diagnostic')
    else:
        pattern = {'empty': 'collection/execution', 'collection': 'collection/execution',
                   'origin': 'outside worktree', 'missing': 'did not produce an inventory'}[case]
        with pytest.raises(RuntimeError, match=pattern):
            test_selection._run(tmp_path, [])


def test_fingerprints_environment_and_capture(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5:AC-01
    @verifies SPEC-SPECGRAPH-X5:AC-03
    @verifies SPEC-SPECGRAPH-X5:AC-04

    Synthetic timing qualifies accounting fields, not operational savings.
    """
    import hashlib
    import os
    files = {'src/a.py': b'x = 1\n', 'pyproject.toml': b'[tool.example]\n',
             'requirements-dev.txt': b'pytest\n', '.git/hidden.py': b'no',
             '__pycache__/hidden.py': b'no', 'image.png': b'no'}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    expected = {name: hashlib.sha256(files[name]).hexdigest()
                for name in sorted(('src/a.py', 'pyproject.toml', 'requirements-dev.txt'))}
    assert test_selection._files(tmp_path) == expected
    assert test_selection._sha(b'abc') == hashlib.sha256(b'abc').hexdigest()
    monkeypatch.setattr(test_selection, 'version', lambda name: 'synthetic-pytest')
    monkeypatch.setenv('PYTEST_ADDOPTS', '-q')
    env = test_selection._environment()
    assert env == {'python': test_selection.sys.version, 'executable': test_selection.sys.executable,
                   'platform': test_selection.platform.platform(), 'pytest': 'synthetic-pytest',
                   **{key: os.environ.get(key) for key in ('PYTHONPATH', 'PYTEST_ADDOPTS', 'PYTEST_PLUGINS')}}
    def missing(name):
        raise test_selection.PackageNotFoundError(name)
    monkeypatch.setattr(test_selection, 'version', missing)
    assert test_selection._environment()['pytest'] is None
    monkeypatch.setattr(test_selection, '_environment', lambda: env)
    def run(root, args, *, trace):
        assert root == tmp_path.resolve() and args == ['tests/test_a.py'] and trace
        return {'tests': ['tests/test_a.py::test_a'], 'executed_blocks': {'tests/test_a.py::test_a': ['src/a.py:1']},
                'seconds': 2.5, 'returncode': 0, 'failed': []}
    monkeypatch.setattr(test_selection, '_run', run)
    receipt = test_selection.capture(tmp_path, tmp_path / 'receipt.json', pytest_args=['tests/test_a.py'])
    assert receipt == json.loads((tmp_path / 'receipt.json').read_text(encoding='utf-8'))
    assert receipt['fingerprints'] == expected and receipt['environment'] == env
    assert receipt['environment_sha256'] == hashlib.sha256(json.dumps(env, sort_keys=True).encode()).hexdigest()
    assert receipt['config_sha256'] == hashlib.sha256(json.dumps({'pyproject.toml': expected['pyproject.toml']}, sort_keys=True).encode()).hexdigest()
    assert receipt['suite_seconds'] == 2.5 and receipt['scan_seconds'] >= 0
    assert receipt['capture_seconds'] == receipt['scan_seconds'] + 2.5
    assert receipt['attestation'] == 'NONE_LOCAL_OBSERVATION_ONLY'
    assert receipt['dependency_closure'] == 'UNKNOWN'
    assert receipt['relation'] == 'EXECUTED_COVERAGE_LOCAL_UNATTESTED'
    assert receipt['complete'] is True and receipt['truncated'] is False


def test_command_short_and_argfile(tmp_path):
    """@verifies SPEC-SPECGRAPH-X5:AC-01"""
    args = ['tests/test_a.py::test_a']
    short = test_selection.pytest_command(args, tmp_path)
    assert short[-1:] == args and not (tmp_path / 'pytest-args.txt').exists()
    args = ['tests/test_a.py::test_' + str(i) + 'x' * 200 for i in range(200)]
    long = test_selection.pytest_command(args, tmp_path)
    assert long[:-1] == short[:-1]
    assert long[-1] == '@' + str(tmp_path / 'pytest-args.txt')
    assert (tmp_path / 'pytest-args.txt').read_text(encoding='utf-8').splitlines() == args
