"""Offline bindings built directly from synthetic local observations."""
from copy import deepcopy
import hashlib
import json

import pytest

from specgraph.test_selection import SCHEMA, _files
from specgraph.x5_scope import build_x5_scope


@pytest.fixture
def sample(tmp_path):
    (tmp_path / 'a.py').write_bytes(b'x = 1\r\ny = 2\r\n')
    (tmp_path / 'b.py').write_bytes(b'z = 3\n')
    tests = ['tests/test_a.py::test_a', 'tests/test_a.py::test_b']
    receipt = {'schema': SCHEMA, 'suite_returncode': 0, 'tests': tests,
               'fingerprints': _files(tmp_path), 'pytest_args': ['tests', '-q'],
               'executed_blocks': {t: ['a.py:1'] for t in tests},
               'environment': {'synthetic': True}}
    return tmp_path, receipt, tests


def bind(sample, **overrides):
    root, receipt, tests = sample
    args = dict(root=root, changed=['a.py:1'], receipt=receipt,
                selected=tests, mandatory=[tests[0]])
    args.update(overrides)
    return build_x5_scope(**args)


def test_determinism_and_preservation(sample):
    root, receipt, tests = sample
    original = deepcopy(receipt)
    content = {p.name: p.read_bytes() for p in root.iterdir()}
    scope = bind(sample)
    assert scope == bind(sample, changed=['./a.py:01', 'a.py:1'],
                         selected=[tests[1], tests[0], tests[0]])
    assert scope == bind(sample, receipt=dict(reversed(list(receipt.items()))))
    assert scope['project'] == root.resolve().as_posix()
    raw = json.dumps(receipt, sort_keys=True, ensure_ascii=False,
                     separators=(',', ':'), allow_nan=False).encode()
    assert scope['capture_sha256'] == hashlib.sha256(raw).hexdigest()
    assert receipt == original
    assert {p.name: p.read_bytes() for p in root.iterdir()} == content
    scope['tests'].clear()
    scope['target_fingerprints'].clear()
    assert receipt == original


def test_binding_sensitivity(sample, tmp_path):
    root, receipt, tests = sample
    baseline = bind(sample)
    assert bind(sample, changed=['a.py:2']) != baseline
    assert bind(sample, selected=[tests[0]]) != baseline
    assert bind(sample, mandatory=[]) != baseline
    capture = deepcopy(receipt)
    capture['environment']['synthetic'] = False
    assert bind(sample, receipt=capture) != baseline
    copy = tmp_path.parent / (tmp_path.name + '-copy')
    copy.mkdir()
    for name in ('a.py', 'b.py'):
        (copy / name).write_bytes((root / name).read_bytes())
    assert bind(sample, root=copy) != baseline
    (root / 'a.py').write_bytes(b'x = 9\ny = 2\n')
    updated = bind(sample)
    assert updated != baseline
    assert updated['changed'][0]['before_sha256'] == receipt['fingerprints']['a.py']
    assert updated['changed'][0]['after_sha256'] != updated['changed'][0]['before_sha256']


@pytest.mark.parametrize('changed', [[], ['a.py:1'] * 257, True, 'a.py:1',
    [True], [''], ['a.py'], ['a.py:0'], ['a.py:-1'], ['a.py:3'],
    ['a.py:True'], ['../a.py:1'], ['/a.py:1'], ['C:\\a.py:1'],
    ['a/../../a.py:1'], ['a.py:1\x00'], ['missing.py:1']])
def test_invalid_changes(sample, changed):
    with pytest.raises(ValueError):
        bind(sample, changed=changed)


@pytest.mark.parametrize('field,value', [
    ('schema', True), ('suite_returncode', False), ('suite_returncode', 1),
    ('tests', []), ('tests', True), ('tests', ['not-a-nodeid']),
    ('pytest_args', []), ('pytest_args', 'tests'), ('pytest_args', [True]),
    ('pytest_args', ['bad\x00arg']), ('fingerprints', {}),
    ('executed_blocks', True), ('executed_blocks', {}),
    ('environment', {'nonfinite': float('nan')}),
    ('environment', {1: 'non-JSON-key'}),
])
def test_invalid_capture_fields(sample, field, value):
    receipt = deepcopy(sample[1])
    receipt[field] = value
    with pytest.raises(ValueError):
        bind(sample, receipt=receipt)


@pytest.mark.parametrize('value', [True, 'A' * 64, '0' * 63, 'z' * 64, None])
def test_invalid_hashes(sample, value):
    receipt = deepcopy(sample[1])
    receipt['fingerprints']['a.py'] = value
    with pytest.raises(ValueError):
        bind(sample, receipt=receipt)


def test_duplicate_inventory_and_malformed_edges(sample):
    receipt = deepcopy(sample[1])
    receipt['tests'].append(receipt['tests'][0])
    with pytest.raises(ValueError):
        bind(sample, receipt=receipt)
    for blocks in (True, [True], ['a.py:0'], ['../a.py:1'], ['missing.py:1']):
        receipt = deepcopy(sample[1])
        receipt['executed_blocks'][sample[2][0]] = blocks
        with pytest.raises(ValueError):
            bind(sample, receipt=receipt)


@pytest.mark.parametrize('override', [
    {'selected': True}, {'mandatory': True}, {'selected': [True]},
    {'selected': ['tests/test_a.py::unknown']},
    {'mandatory': ['tests/test_a.py::unknown']}, {'selected': []},
    {'receipt': True}, {'root': True}, {'root': 'missing-project-directory'},
])
def test_invalid_arguments(sample, override):
    with pytest.raises(ValueError):
        bind(sample, **override)


def test_mandatory_subset(sample):
    with pytest.raises(ValueError):
        bind(sample, selected=[sample[2][1]])


@pytest.mark.parametrize('operation', ['unlisted', 'added', 'removed'])
def test_full_fingerprint_comparison(sample, operation):
    root = sample[0]
    if operation == 'unlisted':
        (root / 'b.py').write_bytes(b'z = 4\n')
    elif operation == 'added':
        (root / 'new.py').write_bytes(b'new = True\n')
    else:
        (root / 'b.py').unlink()
    with pytest.raises(ValueError):
        bind(sample)


@pytest.mark.parametrize('value', [(1, 2), {1, 2}, b'bytes', float('inf')])
def test_non_json_capture_rejected(sample, value):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    receipt = deepcopy(sample[1])
    receipt['environment']['invalid'] = value
    with pytest.raises(ValueError, match='Invalid X5 scope'):
        bind(sample, receipt=receipt)


@pytest.mark.parametrize('node', ['../tests/test_a.py::test_a',
    'tests/test_a.py::test_a\x7f', 'tests/test_a.py::test_a\n',
    './tests/test_a.py::test_a', 'tests/test_a.py::'])
def test_invalid_nodeids_rejected(sample, node):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    receipt = deepcopy(sample[1])
    receipt['tests'] = [node]
    receipt['executed_blocks'] = {node: ['a.py:1']}
    with pytest.raises(ValueError, match='Invalid X5 scope'):
        bind(sample, receipt=receipt, selected=[node], mandatory=[])


def test_canonical_scope_digest_sensitivity(sample):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    def digest(scope):
        return hashlib.sha256(json.dumps(scope, sort_keys=True, ensure_ascii=False,
            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
    baseline = digest(bind(sample))
    tests = sample[2]
    assert digest(bind(sample, changed=['./a.py:01', 'a.py:1'],
                       selected=[tests[1], tests[0], tests[1]])) == baseline
    receipt = deepcopy(sample[1])
    receipt['environment']['synthetic'] = False
    for override in ({'changed': ['a.py:2']}, {'receipt': receipt},
                     {'selected': [tests[0]]}, {'mandatory': []}):
        assert digest(bind(sample, **override)) != baseline
