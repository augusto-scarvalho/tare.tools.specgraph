"""Offline bounded observer checks; one tiny real pytest invocation."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from specgraph import test_selection


@pytest.fixture
def observer(tmp_path, monkeypatch):
    monkeypatch.setenv('SG_X5_ROOT', str(tmp_path))
    monkeypatch.setenv('SG_X5_OUTPUT', str(tmp_path / 'observations.json'))
    monkeypatch.setenv('SG_X5_TRACE', '1')
    spec = importlib.util.spec_from_file_location(
        'bounded_x5_observer', Path(test_selection.__file__).with_name('_pytest_x5.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    return plugin, tmp_path


def finish(plugin, root, status=1):
    plugin.pytest_sessionfinish(None, status)
    data = (root / 'observations.json').read_bytes()
    assert len(data) <= 1024 * 1024
    return json.loads(data)


@pytest.mark.parametrize('kind,reason', [
    ('node', 'NODEID_LIMIT'), ('text', 'TEXT_LIMIT'),
    ('bytes', 'BYTE_LIMIT'), ('pairs', 'PAIR_LIMIT'),
])
def test_limits_emit_small_incomplete_receipts(observer, kind, reason):
    p, root = observer
    node = 'test_tiny.py::test_one'
    if kind == 'node':
        p.MAX_NODEIDS = 1
        p.pytest_collection_finish(SimpleNamespace(items=[
            SimpleNamespace(nodeid=node), SimpleNamespace(nodeid=node + '2')]))
    elif kind == 'text':
        p.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid='x' * 1025)]))
    elif kind == 'bytes':
        p.MAX_BYTES = 4200
        p.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid=node)]))
    else:
        p.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid=node)]))
        p.MAX_PAIRS = 1
        hook = p.pytest_runtest_protocol(SimpleNamespace(nodeid=node), None)
        old = sys.gettrace()
        next(hook)
        try:
            exec(compile('a = 1\nb = 2\nc = 3\n', str(root / 'tiny.py'), 'exec'), {})
        finally:
            with pytest.raises(StopIteration):
                next(hook)
        assert sys.gettrace() is old
        assert p.counters['pairs'] <= 1
    assert p.truncated and p.reason == reason
    snapshot = dict(p.counters)
    p.pytest_runtest_logreport(SimpleNamespace(nodeid=node, failed=True, when='call', duration=1))
    assert p.counters == snapshot
    receipt = finish(p, root)
    assert receipt['truncated'] is True and receipt['complete'] is False
    assert receipt['tests'] == [] and receipt['executed_blocks'] == {}
    assert receipt['exitstatus'] == 1


def test_reporting_missing_and_write_failure_do_not_change_result(observer, monkeypatch):
    p, root = observer
    p.reporting = False
    p.pytest_collection_finish(SimpleNamespace(items=[SimpleNamespace(nodeid='x')]))
    p.pytest_sessionfinish(None, 1)
    assert not (root / 'observations.json').exists()
    p.reporting = True
    monkeypatch.setenv('SG_X5_OUTPUT', str(root / 'missing' / 'receipt.json'))
    session = SimpleNamespace(exitstatus=1)
    p.pytest_sessionfinish(session, 1)
    assert session.exitstatus == 1


def test_import_without_reporting_environment(monkeypatch):
    for key in ('SG_X5_ROOT', 'SG_X5_OUTPUT', 'SG_X5_TRACE'):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location(
        'disabled_x5_observer', Path(test_selection.__file__).with_name('_pytest_x5.py'))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    assert not plugin.reporting and not plugin.trace_enabled
    plugin.pytest_collection_finish(SimpleNamespace(items=[]))
    plugin.pytest_sessionfinish(None, 1)


def test_real_small_run_durations_failure_and_trace(tmp_path):
    (tmp_path / 'test_tiny.py').write_text(
        'def test_ok():\n    assert 1 + 1 == 2\n'
        'def test_bad():\n    assert False\n', encoding='utf-8')
    output = tmp_path / 'observations.json'
    env = dict(os.environ, SG_X5_ROOT=str(tmp_path), SG_X5_OUTPUT=str(output),
               SG_X5_TRACE='1', PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    env.pop('PYTEST_ADDOPTS', None)
    env.pop('PYTEST_PLUGINS', None)
    plugin_dir = str(Path(test_selection.__file__).parent)
    env['PYTHONPATH'] = plugin_dir + os.pathsep + env.get('PYTHONPATH', '')
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', '_pytest_x5',
                          '-p', 'no:cacheprovider', 'test_tiny.py'],
                         cwd=tmp_path, env=env, capture_output=True, timeout=30)
    assert run.returncode == 1
    raw = output.read_bytes()
    assert len(raw) <= 1024 * 1024
    receipt = json.loads(raw)
    assert receipt['exitstatus'] == 1
    assert receipt['complete'] is True and receipt['truncated'] is False
    assert set(receipt['executed_blocks']) == set(receipt['tests'])
    assert receipt['failed'] == ['test_tiny.py::test_bad']
    assert 'test_tiny.py:2' in receipt['executed_blocks']['test_tiny.py::test_ok']
    for phases in receipt['durations'].values():
        assert set(phases) == {'setup', 'call', 'teardown'}
        assert all(seconds >= 0 for seconds in phases.values())
    assert all(receipt['counters'][phase] == 2 for phase in ('setup', 'call', 'teardown'))
