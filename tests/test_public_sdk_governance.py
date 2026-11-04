"""Offline public SDK falsifiers; no qualification or admission claims."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def isolated(source):
    program = "import sys\nsys.path.insert(0, " + repr(str(ROOT / "src")) + ")\n" + source
    compile(program, "<public-sdk-subprocess>", "exec")
    result = subprocess.run(
        [sys.executable, "-I", "-c", program],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_version_parity():
    # Project metadata is read from this exact checkout, not an unrelated install.
    observed = isolated(
        "import contextlib, io, json, tomllib\n"
        "import specgraph\n"
        "from specgraph.cli import build_parser\n"
        "from pathlib import Path\n"
        "out = io.StringIO()\n"
        "with contextlib.redirect_stdout(out):\n"
        " try: build_parser().parse_args(['--version'])\n"
        " except SystemExit as exc: status = exc.code\n"
        "metadata = tomllib.loads(Path('pyproject.toml').read_text(encoding='utf-8'))\n"
        "print(json.dumps([status, out.getvalue().strip().split()[-1], specgraph.__version__, metadata['project']['version']]))\n"
    )
    assert observed[0] == 0
    assert observed[1] == observed[2] == observed[3], observed


def test_lazy_exports_cache_and_unknown_attribute():
    observed = isolated(
        "import json, types\n"
        "import specgraph\n"
        "assert not any('specgraph.' + module in sys.modules for module, _ in specgraph._EXPORTS.values())\n"
        "assert specgraph.__all__ == sorted(specgraph._EXPORTS)\n"
        "assert dir(specgraph) == sorted(set(vars(specgraph)) | set(specgraph._EXPORTS))\n"
        "calls = []\n"
        "for name, (module, attribute) in specgraph._EXPORTS.items():\n"
        " value = object()\n"
        " def load(request, package):\n"
        "  calls.append((request, package))\n"
        "  return types.SimpleNamespace(**{attribute: value})\n"
        " specgraph.import_module = load\n"
        " assert getattr(specgraph, name) is value\n"
        " assert vars(specgraph)[name] is value\n"
        " assert getattr(specgraph, name) is value\n"
        " assert calls[-1] == ('.' + module, 'specgraph')\n"
        "assert len(calls) == len(specgraph._EXPORTS)\n"
        "try: getattr(specgraph, 'not_a_public_export')\n"
        "except AttributeError as exc: assert 'not_a_public_export' in str(exc)\n"
        "else: raise AssertionError('missing AttributeError')\n"
        "assert len(calls) == len(specgraph._EXPORTS)\n"
        "print(json.dumps(True))\n"
    )
    assert observed is True



def test_real_export_in_isolated_process():
    assert isolated('''import json
import specgraph
assert 'specgraph.adr_parser' not in sys.modules
from specgraph.adr_parser import AdrNode
assert specgraph.AdrNode is AdrNode
assert vars(specgraph)['AdrNode'] is AdrNode
assert specgraph.AdrNode is AdrNode
assert 'specgraph.graph' not in sys.modules
try:
    specgraph.not_a_public_export
except AttributeError:
    pass
else:
    raise AssertionError('unknown export accepted')
assert dir(specgraph) == sorted(set(vars(specgraph)) | set(specgraph._EXPORTS))
print(json.dumps(True))
''') is True


def test_parser_defaults_formats_and_filters(tmp_path):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-06]"""
    import pytest
    from specgraph.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(['init'])
    assert (args.root, args.format, args.name) == ('.', 'table', 'my-project')
    assert list(tmp_path.iterdir()) == []
    args = parser.parse_args(['trace'])
    assert args.kind == 'all' and args.format is None
    import argparse
    trace_parser = next(action for action in parser._actions
                        if isinstance(action, argparse._SubParsersAction)).choices['trace']
    kind_help = next(action.help for action in trace_parser._actions if action.dest == 'kind')
    assert 'table/Markdown' in kind_help
    assert 'JSON/YAML preserve the complete matrix' in kind_help
    for fmt in ('json', 'yaml', 'md', 'table'):
        for kind in ('all', 'specs', 'adrs', 'schemas', 'apis', 'mcp', 'code', 'tests'):
            args = parser.parse_args(['--root', str(tmp_path), 'trace', '--format', fmt, '--kind', kind])
            assert (args.root, args.format, args.kind) == (str(tmp_path), fmt, kind)
    for argv in ([], ['trace', '--format', 'xml'], ['trace', '--kind', 'unknown']):
        with pytest.raises(SystemExit) as error:
            parser.parse_args(argv)
        assert error.value.code == 2
    assert parser.parse_args(['drift-check']).strict is True
    assert parser.parse_args(['drift-check', '--no-strict']).strict is False
    assert parser.parse_args(['search', 'needle']).limit == 20
    assert parser.parse_args(['search', 'needle']).live is False


def test_init_explicit_creation_and_preservation(tmp_path, capsys):
    import yaml
    from specgraph.cli import build_parser, handle_init
    build_parser().parse_args(['init'])
    assert not list(tmp_path.iterdir())
    assert handle_init(tmp_path, 'bounded-fixture') == 0
    config = tmp_path / 'specgraph.yaml'
    sample = tmp_path / 'specs/SPEC-001_core_architecture.md'
    assert yaml.safe_load(config.read_text(encoding='utf-8'))['project_name'] == 'bounded-fixture'
    assert 'id: SPEC-001' in sample.read_text(encoding='utf-8')
    original = (config.read_bytes(), sample.read_bytes())
    assert handle_init(tmp_path, 'different') == 0
    assert (config.read_bytes(), sample.read_bytes()) == original
    config.write_bytes(b'# operator config\n')
    sample.write_bytes(b'# operator sample\n')
    assert handle_init(tmp_path, 'different') == 0
    assert config.read_bytes() == b'# operator config\n'
    assert sample.read_bytes() == b'# operator sample\n'
    config.unlink()
    assert handle_init(tmp_path, 'restored') == 0
    assert sample.read_bytes() == b'# operator sample\n'
    sample.unlink()
    kept = config.read_bytes()
    assert handle_init(tmp_path, 'ignored') == 0
    assert config.read_bytes() == kept and sample.exists()


def test_dump_trace_formats_and_existing_kind_tables(capsys):
    """@verifies SPEC-SPECGRAPH-GOVERNANCE-001 [AC-06]"""
    from types import SimpleNamespace
    import yaml
    from specgraph.cli import dump_trace
    data = {
        'generation_id': 'a' * 64,
        'summary': dict.fromkeys(('total_adrs', 'total_specs', 'total_schemas', 'total_apis',
            'total_mcp_tools', 'total_governed_symbols', 'total_tests', 'total_causal_edges',
            'governed_code_coverage_pct', 'spec_verification_coverage_pct'), 1),
        'adrs': [dict(id='ADR-fixture', title='Decision', status='ACCEPTED', superseded_by=None,
                      governed_specs=['SPEC-fixture'], file_path='docs/adr.md')],
        'specs': [dict(id='SPEC-fixture', title='Spécification', status='ACCEPTED', implements=['REQ'])],
        'schemas': [dict(name='Schema-fixture', kind='json', linked_specs=['SPEC-fixture'], file_path='schema.json', fields=[])],
        'apis': [dict(endpoint_id='API-fixture', method='GET', path='/fixture', linked_specs=[], file_path='api.py', start_line=1)],
        'mcp_tools': [dict(tool_id='MCP-fixture', name='tool', parameters=['arg'], linked_specs=[], file_path='tool.py', start_line=2)],
        'symbols': [dict(symbol_id='CODE-fixture', name='fixture_symbol', kind='function', file_path='src/fixture.py', start_line=3)],
        'tests': [dict(test_id='TEST-fixture', name='test_fixture', file_path='tests/test_fixture.py', start_line=4)],
        'edges': [dict(type=kind, **{'from': name, 'to': 'SPEC-fixture'}) for kind, name in
                  [('CODE_IMPLEMENTS_SPEC', 'src:x'), ('SCHEMA_IMPLEMENTS_SPEC', 'schema:y'),
                   ('API_IMPLEMENTS_SPEC', 'api:z'), ('TEST_VERIFIES_SPEC', 'tests:t')]],
    }
    matrix = SimpleNamespace(config=SimpleNamespace(project_name='fixture'), to_dict=lambda: data)
    for fmt, load in [('json', json.loads), ('yaml', yaml.safe_load)]:
        for kind in ('all', 'specs', 'adrs', 'schemas', 'apis', 'mcp', 'code', 'tests'):
            dump_trace(matrix, fmt, kind)
            assert load(capsys.readouterr().out) == data
    sections = ['## Architecture Decision', '## Specification Traceability', '## Data Schemas', '## APIs & MCP', '## Code Symbols', '## Tests']
    expected = {'all': sections, 'adrs': sections[:1], 'specs': sections[1:2],
                'schemas': sections[2:3], 'apis': sections[3:4], 'mcp': sections[3:4],
                'code': sections[4:5], 'tests': sections[5:6]}
    for fmt in ('md', 'table'):
        for kind, headings in expected.items():
            dump_trace(matrix, fmt, kind)
            out = capsys.readouterr().out
            assert '# SpecGraph Causal Traceability Matrix (fixture)' in out
            for selected, marker in [('apis', 'API-fixture'), ('mcp', 'MCP-fixture'),
                                     ('code', 'CODE-fixture'), ('tests', 'TEST-fixture')]:
                assert (marker in out) == (kind in ('all', selected))
            for heading in sections:
                assert (heading in out) == (heading in headings)
            if kind in ('all', 'specs'):
                assert '`x`' in out and '`t`' in out and '`y`' in out and '`z`' in out


def test_main_init_trace_and_drift_forwarding(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    from specgraph import cli, graph
    calls = []
    monkeypatch.setattr(cli, 'handle_init', lambda *args: calls.append(args) or 1)
    assert cli.main(['--root', str(tmp_path), 'init', '--name', 'explicit']) == 1
    assert calls.pop() == (tmp_path.resolve(), 'explicit')
    matrix = SimpleNamespace(drift_check=lambda **kw: calls.append(kw) or {'status': 'PASS'})
    monkeypatch.setattr(graph.CausalTraceabilityMatrix, 'build', lambda **kw: calls.append(kw) or matrix)
    monkeypatch.setattr(cli, 'dump_trace', lambda *args, **kw: calls.append((args, kw)))
    assert cli.main(['--root', str(tmp_path), 'trace', '--format', 'yaml', '--kind', 'schemas']) == 0
    assert calls == [{'root_dir': tmp_path.resolve()}, ((matrix, 'yaml'), {'kind': 'schemas'})]
    calls.clear()
    assert cli.main(['--root', str(tmp_path), 'drift-check', '--format', 'json', '--no-strict']) == 0
    assert calls == [{'root_dir': tmp_path.resolve()}, {'strict_override': False}]
    assert json.loads(capsys.readouterr().out)['status'] == 'PASS'
    matrix.drift_check = lambda **kw: {'status': 'FAIL'}
    assert cli.main(['--root', str(tmp_path), 'drift-check', '--format', 'json']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'FAIL'


def test_main_index_doctor_status_and_errors(tmp_path, monkeypatch, capsys):
    from specgraph import cli, lifecycle, capabilities
    calls = []
    payload = {'status': 'READY'}
    def reconcile(root, **kwargs):
        calls.append((root, kwargs))
        return payload
    monkeypatch.setattr(lifecycle, 'reconcile_repository', reconcile)
    for command in ('index', 'reconcile'):
        for status, expected in [('READY', 0), ('PARTIAL', 1), ('BUSY', 1)]:
            payload['status'] = status
            assert cli.main(['--root', str(tmp_path), command, '--full', '--format', 'json']) == expected
            assert calls.pop() == (tmp_path.resolve(), {'incremental': False, 'reason': command})
            assert json.loads(capsys.readouterr().out) == payload
    def doctor(root, **kwargs):
        calls.append((root, kwargs))
        return {'status': 'NOT_CONFIGURED'}
    monkeypatch.setattr(capabilities, 'project_doctor', doctor)
    assert cli.main(['--root', str(tmp_path), 'doctor', '--format', 'json', '--effectiveness-report', 'small.json']) == 1
    assert calls.pop() == (tmp_path.resolve(), {'effectiveness_report': 'small.json'})
    capsys.readouterr()
    def broken(*args, **kwargs):
        raise ValueError('bounded handler failure')
    monkeypatch.setattr(lifecycle, 'reconcile_repository', broken)
    assert cli.main(['--root', str(tmp_path), 'index', '--format', 'json']) == 1
    assert 'bounded handler failure' in capsys.readouterr().err
