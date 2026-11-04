"""Offline functional falsifiers; declarations are not execution attestation."""
import ast
import json
from dataclasses import asdict

import pytest

from specgraph.schema_parser import (
    ASTPydanticVisitor, JSONSchemaParser, SQLSchemaParser,
    SchemaContract, SchemaField, SchemaParser,
)


def test_sql_structure_and_mutations(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-02]"""
    path = tmp_path / 'items.sql'
    table = 'CREATE TABLE items (id INTEGER NOT NULL, amount NUMERIC(10,2), PRIMARY KEY(id), FOREIGN KEY(id) REFERENCES other(id), UNIQUE(id), CHECK(id > 0), CONSTRAINT positive CHECK(id > 0));'
    view = 'CREATE VIEW listing AS SELECT id FROM items;'
    text = '-- @spec spec-demo [ac-01, AC-02]\n' + table + '\n' + view
    path.write_text(text, encoding='utf-8')
    contracts, errors = SQLSchemaParser.parse_file(path, tmp_path)
    assert errors == []
    assert [(c.schema_id, c.kind, c.start_line, c.end_line, c.raw_definition) for c in contracts] == [
        ('items.sql:items', 'sql_table', 2, 2, table),
        ('items.sql:listing', 'sql_view', 3, 3, view),
    ]
    assert contracts[0].fields == [SchemaField('id', 'INTEGER', True), SchemaField('amount', 'NUMERIC(10,2)', False)]
    assert contracts[1].fields == []
    assert all(c.file_path == 'items.sql' and c.linked_specs == ['SPEC-DEMO:AC-01', 'SPEC-DEMO:AC-02'] for c in contracts)
    path.write_text(text + '\nALTER TABLE items ADD x INT;', encoding='utf-8')
    recognized, errors = SQLSchemaParser.parse_file(path, tmp_path)
    assert recognized == contracts
    assert len(errors) == 1 and 'Unrecognized SQL DDL content' in errors[0]
    path.write_text(text.replace(' NOT NULL', '').replace('spec-demo', 'spec-other'), encoding='utf-8')
    changed, errors = SQLSchemaParser.parse_file(path, tmp_path)
    assert errors == [] and changed != contracts
    assert not changed[0].fields[0].required
    assert changed[0].linked_specs == ['SPEC-OTHER:AC-01', 'SPEC-OTHER:AC-02']
    assert SQLSchemaParser._extract_spec_tags('-- @spec demo\n-- @spec demo') == ['DEMO']


def test_json_properties_tags_and_mutations(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-03]"""
    path = tmp_path / 'payload.json'
    data = {'title': 'Payload', 'properties': {'id': {'type': 'integer', 'description': 'Identifier'}, 'unknown': {}, 'flag': False}, 'required': ['id'], '$spec': ' spec-demo:ac-01 ', 'description': '@spec spec-demo [AC-01, AC-02]\n' + 'x' * 2200}
    raw = json.dumps(data, indent=2)
    path.write_text(raw, encoding='utf-8')
    contracts, errors = JSONSchemaParser.parse_file(path, tmp_path)
    assert errors == [] and len(contracts) == 1
    c = contracts[0]
    assert (c.schema_id, c.kind, c.file_path, c.start_line, c.end_line) == ('payload.json:Payload', 'json_schema', 'payload.json', 1, raw.count('\n') + 1)
    assert c.fields == [SchemaField('id', 'integer', True, 'Identifier'), SchemaField('unknown', 'any', False), SchemaField('flag', 'any', False)]
    assert c.linked_specs == ['SPEC-DEMO:AC-01', 'SPEC-DEMO:AC-02']
    assert c.raw_definition == raw[:2000]
    data['required'] = ['flag']
    data['properties']['id']['description'] = 'Changed'
    path.write_text(json.dumps(data), encoding='utf-8')
    changed, errors = JSONSchemaParser.parse_file(path, tmp_path)
    assert errors == [] and changed != contracts
    assert not changed[0].fields[0].required and changed[0].fields[2].required
    assert changed[0].fields[0].description == 'Changed'
    for ordinary in ({'hello': 'world'}, []):
        path.write_text(json.dumps(ordinary), encoding='utf-8')
        assert JSONSchemaParser.parse_file(path, tmp_path) == ([], [])
    path.write_text('{ broken', encoding='utf-8')
    found, errors = JSONSchemaParser.parse_file(path, tmp_path)
    assert found == [] and len(errors) == 1 and 'JSON Parse Failure' in errors[0]
    path.write_text('{"type":"object","spec":"demo"}', encoding='utf-8')
    found, errors = JSONSchemaParser.parse_file(path, tmp_path)
    assert errors == [] and found[0].schema_id == 'payload.json:payload'
    assert found[0].linked_specs == ['DEMO']


@pytest.mark.parametrize('form,kind', [
    ('class Model(BaseModel):', 'pydantic_model'),
    ('class Model(Schema):', 'pydantic_model'),
    ('class Model(pkg.BaseModel):', 'pydantic_model'),
    ('class Model(pkg.Schema):', 'pydantic_model'),
    ('@dataclass\nclass Model:', 'dataclass'),
    ('@pkg.dataclass\nclass Model:', 'dataclass'),
    ('@dataclass()\nclass Model:', 'dataclass'),
    ('@pkg.dataclass(frozen=True)\nclass Model:', 'dataclass'),
    ('@pydantic_dataclass\nclass Model:', 'dataclass'),
    ('@pkg.pydantic_dataclass()\nclass Model:', 'dataclass'),
])
def test_python_supported_forms_no_execution(tmp_path, form, kind):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-04]"""
    effect = tmp_path / 'executed'
    source = f"from pathlib import Path\nPath({str(effect)!r}).write_text('bad')\n" + form + '\n    """Local model. @spec spec-demo [AC-01, AC-02]"""\n    x: int\n    y: str = ""\n\nclass Ordinary:\n    ignored: int\n'
    path = tmp_path / 'models.py'
    path.write_text(source, encoding='utf-8')
    contracts, errors = SchemaParser.parse_python_models(path, tmp_path)
    assert errors == [] and len(contracts) == 1 and not effect.exists()
    c = contracts[0]
    assert (c.schema_id, c.name, c.kind, c.file_path) == ('models.py:Model', 'Model', kind, 'models.py')
    assert c.fields == [SchemaField('x', 'int'), SchemaField('y', 'str', False)]
    assert c.linked_specs == ['SPEC-DEMO:AC-01', 'SPEC-DEMO:AC-02']
    assert c.start_line == (4 if form.startswith('@') else 3)
    assert c.end_line == c.start_line + 3
    assert c.raw_definition == 'Local model. @spec spec-demo [AC-01, AC-02]'
    path.write_text(source.replace('x: int', 'x: float = 0'), encoding='utf-8')
    changed, errors = SchemaParser.parse_python_models(path, tmp_path)
    assert errors == [] and changed[0].fields[0] == SchemaField('x', 'float', False)
    visitor = ASTPydanticVisitor('sub\\models.py')
    other = ASTPydanticVisitor('other.py')
    visitor.visit(ast.parse('class Plain:\n    x: int\n'))
    assert visitor.rel_path == 'sub/models.py' and visitor.contracts == []
    assert other.contracts is not visitor.contracts
    assert visitor._extract_spec_links(None) == []
    assert visitor._extract_spec_links('@spec demo\n@spec demo') == ['DEMO']


@pytest.mark.parametrize('suffix,parser', [('.sql', SQLSchemaParser.parse_file), ('.json', JSONSchemaParser.parse_file), ('.py', SchemaParser.parse_python_models)])
def test_errors_escape_encoding_and_read(tmp_path, monkeypatch, suffix, parser):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-02, AC-03, AC-04]"""
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / ('outside' + suffix)
    outside.write_text('unused', encoding='utf-8')
    found, errors = parser(outside, root)
    assert found == [] and len(errors) == 1 and 'Security Violation' in errors[0]
    inside = root / ('bad' + suffix)
    inside.write_bytes(b'\xff')
    found, errors = parser(inside, root)
    assert found == [] and len(errors) == 1 and 'UTF-8 Decoding Error' in errors[0]
    def denied(*args, **kwargs):
        raise OSError('synthetic read denial')
    monkeypatch.setattr(type(inside), 'read_text', denied)
    found, errors = parser(inside, root)
    assert found == [] and len(errors) == 1 and 'synthetic read denial' in errors[0]


def test_discovery_exclusions_aggregation(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-02, AC-03, AC-04]"""
    source = tmp_path / 'src'
    source.mkdir()
    files = {'a.sql': 'CREATE TABLE a (id INT);', 'b.ddl': 'CREATE VIEW b AS SELECT 1;', 'c.json': '{"type":"object"}', 'd.py': 'class D(BaseModel):\n    x: int\n', 'e.json': '{ broken', 'f.py': 'class :', 'g.sql': b'\xff', 'ignored.txt': 'broken'}
    for name, text in files.items():
        p = source / name
        p.write_bytes(text if isinstance(text, bytes) else text.encode('utf-8'))
    excluded = source / 'skip'
    excluded.mkdir()
    (excluded / 'bad.json').write_text('{ broken', encoding='utf-8')
    (excluded / 'hidden.sql').write_text('CREATE TABLE hidden (x INT);', encoding='utf-8')
    found, errors = SchemaParser.discover_schemas(['src', '../escape', 'missing'], ['skip'], tmp_path)
    assert [c.schema_id for c in found] == ['src/a.sql:a', 'src/b.ddl:b', 'src/c.json:c', 'src/d.py:D']
    assert len(errors) == 4
    assert all(any(token in e for e in errors) for token in ['JSON Parse Failure', 'AST SyntaxError', 'UTF-8 Decoding Error', 'Security Violation'])
    direct, errors = SchemaParser.discover_schemas(['src/a.sql'], [], tmp_path)
    assert direct == found[:1] and errors == []


def test_record_fields_and_independent_defaults():
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-02, AC-03, AC-04]"""
    field = SchemaField('id', 'int')
    assert asdict(field) == {'name': 'id', 'type_name': 'int', 'required': True, 'description': ''}
    c = SchemaContract('a:X', 'X', 'dataclass', 'a')
    other = SchemaContract('b:Y', 'Y', 'json_schema', 'b')
    assert asdict(c) == {'schema_id': 'a:X', 'name': 'X', 'kind': 'dataclass', 'file_path': 'a', 'start_line': 1, 'end_line': 1, 'fields': [], 'linked_specs': [], 'raw_definition': ''}
    c.fields.append(field)
    c.linked_specs.append('DEMO')
    assert other.fields == [] and other.linked_specs == []
