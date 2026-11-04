"""Bounded helper falsifiers; local observations never confer admission."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from specgraph import traceability as t
from specgraph.evidence import classify_test_relation
from specgraph.hook_context import context_hint
from specgraph.lexical import indexed_text, match_expression, query_terms
from specgraph.serialization import canonical_json
from test_traceability_mappings import mapped


@pytest.mark.parametrize('value', ['', '../escape', '/root', './x', 'a//b', 'a\\b', 'C:x', 'a\nx', 42, 'x' * 1025])
def test_relative_rejects_noncanonical_paths(value):
    """@verifies SPEC-SPECGRAPH-C5 [AC-01]"""
    with pytest.raises(ValueError):
        t._relative(value)


def config(paths, excluded=False):
    return SimpleNamespace(intent_mapping_paths=paths, excluded_paths=[],
                           is_path_excluded=lambda *_: excluded)


def test_explicit_selection_and_file_limits(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-C5 [AC-01]"""
    path = tmp_path / 'map.json'
    path.write_bytes(b'{}')
    assert t.mapping_files(tmp_path, config([])) == ({}, [])
    files, errors = t.mapping_files(tmp_path, config(['map.json']))
    assert list(files) == ['map.json'] and not errors
    for selection in (['missing.json'], ['map.txt'], ['../escape.json'], ['map.json'] * 17):
        assert t.mapping_files(tmp_path, config(selection))[1]
    assert t.mapping_files(tmp_path, config(['map.json'], True))[1]
    assert t.mapping_files(tmp_path, config(['map.json', 'map.json']))[1]
    path.write_bytes(b' ' * t.MAX_MAPPING_BYTES)
    assert not t.mapping_files(tmp_path, config(['map.json']))[1]
    path.write_bytes(b' ' * (t.MAX_MAPPING_BYTES + 1))
    assert t.mapping_files(tmp_path, config(['map.json']))[1]
    path.write_bytes(b'{}')
    monkeypatch.setattr(type(path), 'is_symlink', lambda self: self == path)
    assert t.mapping_files(tmp_path, config(['map.json']))[1]
    with pytest.raises(ValueError, match='escapes'):
        t._read(tmp_path, 'map.json', None, 32)


def test_read_memory_and_disk_bounds(tmp_path):
    """@verifies SPEC-SPECGRAPH-C5 [AC-01]"""
    for maximum in (t.MAX_SOURCE_BYTES, t.MAX_MAPPING_BYTES):
        raw = b'x' * maximum
        assert t._read(tmp_path, 'x', {'x': raw}, maximum) == raw
        (tmp_path / 'x').write_bytes(raw)
        assert t._read(tmp_path, 'x', None, maximum) == raw
        for bad in (raw + b'x', 'text', bytearray(b'x')):
            with pytest.raises(ValueError, match='byte limit'):
                t._read(tmp_path, 'x', {'x': bad}, maximum)
        (tmp_path / 'x').write_bytes(raw + b'x')
        with pytest.raises(ValueError, match='byte limit'):
            t._read(tmp_path, 'x', None, maximum)
    with pytest.raises(ValueError):
        t._read(tmp_path, '../escape', None, 32)
    with pytest.raises(FileNotFoundError):
        t._read(tmp_path, 'missing', None, 32)


def test_nested_duplicate_keys_and_mapping_row_limit(mapped):
    """@verifies SPEC-SPECGRAPH-C5 [AC-01]"""
    from specgraph.graph import CausalTraceabilityMatrix
    root, path, payload, _ = mapped
    with pytest.raises(ValueError, match='duplicate JSON key'):
        json.loads('{"outer":{"x":1,"x":2}}', object_pairs_hook=t._unique_object)
    assert t._unique_object([('a', 1), ('b', 2)]) == {'a': 1, 'b': 2}
    payload['mappings'] *= t.MAX_MAPPINGS + 1
    path.write_text(json.dumps(payload), encoding='utf-8')
    matrix = CausalTraceabilityMatrix.build(root)
    assert any('invalid or unavailable envelope' in e for e in matrix.parse_errors)
    assert not [e for e in matrix.edges if e.get('origin') == 'declared_exact_mapping']


def test_class_binding_does_not_bind_child(mapped):
    """@verifies SPEC-SPECGRAPH-C5 [AC-01]"""
    from specgraph.graph import CausalTraceabilityMatrix
    root, path, payload, _ = mapped
    payload['mappings'][0]['target_id'] = 'fixture:src/code.py:Service'
    path.write_text(json.dumps(payload), encoding='utf-8')
    matrix = CausalTraceabilityMatrix.build(root)
    edges = [e for e in matrix.edges if e.get('origin') == 'declared_exact_mapping']
    assert [e['from'] for e in edges] == ['src/code.py:Service']
    assert 'src/code.py:Service.run' in [n.symbol_id for n in matrix.get_orphan_code()]


@pytest.mark.parametrize('event', ['post', 'stop', 'end'])
def test_hook_complete_utf8_or_exact_digest(tmp_path, event):
    """@verifies SPEC-SPECGRAPH-C7 [AC-01]"""
    common = {'role': 'auditor', 'event': event, 'max_bytes': 512}
    small = context_hint(tmp_path, **common, work_id='ação')
    assert small['status'] == 'SILENT'
    assert len((canonical_json(small) + '\n').encode('utf-8')) <= 512
    identity = '漢\\"' * 2000
    large = context_hint(tmp_path, **common, work_id=identity)
    original = {'schema': 'tare.tools/specgraph-hook-context/1', 'authority': 'NONE',
                'role': 'auditor', 'event': event, 'root': str(tmp_path.resolve()),
                'work_id': identity, 'source_ref': None, 'status': 'SILENT',
                'reason': 'notification_only; no stop gate'}
    assert large['status'] == 'BUDGET_REQUIRED'
    assert large['selection_digest'] == hashlib.sha256(canonical_json(original).encode()).hexdigest()
    wire = (canonical_json(large) + '\n').encode('utf-8')
    assert len(wire) <= 512 and json.loads(wire) == large
    assert 'work_id' not in large


@pytest.mark.parametrize('relation,expected', [('TEST_REFERENCES_SYMBOL', 'STATIC_REFERENCE'),
    ('DECLARED_VERIFIES', 'DECLARED_ONLY'), ('EXECUTED_COVERAGE', 'EXECUTED_COVERAGE'),
    ('JUnit', 'UNKNOWN'), ('', 'UNKNOWN')])
def test_relation_categories(relation, expected):
    """@verifies SPEC-SPECGRAPH-C5 [AC-03]"""
    assert classify_test_relation(relation) == expected


def test_normalized_literals_and_expression_modes():
    """@verifies SPEC-SPECGRAPH-LEXICAL-INTENT [AC-02]"""
    assert indexed_text('HTTPServer sessão_expirada CamelCase') == 'http server sessao expirada camel case'
    terms = query_terms('Como renovar sessão_expirada?')
    assert terms == ['renovar', 'sessao', 'expirada']
    assert match_expression(terms) == '"renovar" OR "sessao" OR "expirada"'
    assert match_expression(terms, identifier=True) == '"renovar" AND "sessao" AND "expirada"'
    assert match_expression([]) == ''
