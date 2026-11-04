import json
from pathlib import Path

import pytest

from specgraph.test_selection import capture, compare, recommend_from_receipt, recommend_tests
from specgraph.evidence import classify_test_relation


def _project(tmp_path):
    for folder in ('src/specgraph', 'tests', 'tools/tests'):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / 'src/specgraph/__init__.py').write_text('')
    (tmp_path / 'src/specgraph/calc.py').write_text('def add(a, b):\n    return a + b\n')
    (tmp_path / 'tests/test_calc.py').write_text(
        'from specgraph.calc import add\n'
        'def test_add():\n    assert add(1, 2) == 3\n')
    (tmp_path / 'tools/tests/test_other.py').write_text(
        'from specgraph.calc import add\n'
        'VALUE = add(1, 2)\n'
        'def test_imported():\n    assert VALUE == 3\n')
    return tmp_path


def test_capture_hooks_real_lines_and_compare_smaller_inventory_and_missed_killer(tmp_path, monkeypatch):
    import tempfile
    scratch = tmp_path.parent / 'x5-temp'
    scratch.mkdir(exist_ok=True)
    # Temp paths may contain aliases (8.3 names on Windows service accounts).
    monkeypatch.setattr(tempfile, 'tempdir', str(scratch / '..' / 'x5-temp'))
    root = _project(tmp_path)
    receipt = capture(root, root / 'trace.json')
    assert receipt['suite_returncode'] == 0
    assert len(receipt['tests']) == 2
    assert set(receipt['executed_blocks']) == set(receipt['tests'])
    assert any('src/specgraph/calc.py:2' in blocks for blocks in receipt['executed_blocks'].values())
    assert receipt['fingerprints']['src/specgraph/calc.py']
    assert receipt['attestation'] == 'NONE_LOCAL_OBSERVATION_ONLY'
    mutation = {'name': 'wrong-add', 'path': 'src/specgraph/calc.py', 'find': 'return a + b', 'replace': 'return a - b'}
    report = compare(root, receipt, [mutation])
    result = report['mutations'][0]
    assert len(result['candidates']) == 1  # import-time use has no per-test trace
    assert len(result['full_failures']) == 2
    assert len(result['selected_failures']) == 1
    assert result['omitted_relevant'] == ['tools/tests/test_other.py::test_imported']
    assert report['qualification'] == 'SUSPENDED_MISSED_RELEVANT_TEST'


def test_fallback_hashes_config_environment_and_mandatory(tmp_path, monkeypatch):
    root = _project(tmp_path)
    receipt = capture(root, root / 'trace.json')
    result = recommend_from_receipt(['src/specgraph/calc.py:2'], receipt, root=root,
                                    mandatory=['tools/tests/test_other.py::test_imported'])
    assert result['mode'] == 'FULL_SUITE'
    assert result['candidates'] == sorted(receipt['tests'])
    assert result['command'][-2:] == ['tests', 'tools/tests']
    assert not result['unproven_blocks']
    (root / 'src/specgraph/calc.py').write_text('def add(a, b):\n    return a - b\n')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\n')
    result = recommend_from_receipt(['src/specgraph/calc.py:2'], receipt, root=root)
    assert any('unlisted changed fingerprints' in x for x in result['fallback_reasons'])
    monkeypatch.setenv('PYTEST_ADDOPTS', '-q')
    assert 'environment changed' in recommend_from_receipt(['src/specgraph/calc.py:2'], receipt, root=root)['fallback_reasons']
    receipt['complete'] = True
    receipt['dependency_closure'] = 'EXTERNALLY_VERIFIED'
    assert recommend_from_receipt(['src/specgraph/calc.py:2'], receipt, root=root)['mode'] == 'FULL_SUITE'


def test_static_reference_never_authorizes_selective_execution():
    result = recommend_tests(['a.py'], {'complete': True, 'tests': {'a.py': ['test_a']},
                                        'all_tests': ['test_other']}, mandatory=['test_required'])
    assert result['mode'] == 'FULL_SUITE'
    assert set(result['tests']) == {'test_a', 'test_other', 'test_required'}
    assert result['command'][-1] == 'test_required'
    assert classify_test_relation('TEST_REFERENCES_SYMBOL') == 'STATIC_REFERENCE'


def test_recommendation_without_pytest_still_provides_full_suite_fallback(tmp_path, monkeypatch):
    import specgraph.test_selection as selection
    def missing(name):
        raise selection.PackageNotFoundError(name)
    monkeypatch.setattr(selection, 'version', missing)
    result = selection.recommend_from_receipt(['src/app.py'], {}, root=tmp_path)
    assert result['mode'] == 'FULL_SUITE'
    assert 'environment changed' in result['fallback_reasons']

def test_missing_empty_receipt_cli_falls_back(tmp_path, capsys):
    from specgraph.cli import main
    root = _project(tmp_path)
    for name, content in (('absent.json', None), ('empty.json', '{}'),
                          ('list.json', '[]'), ('null.json', 'null'),
                          ('broken.json', '{"tests":null,"executed_blocks":null,"fingerprints":null}'),
                          ('invalid-args.json', '{"pytest_args":[null,42]}')):
        if content is not None:
            (root / name).write_text(content)
        assert main(['--root', str(root), 'recommend-from-trace', str(root / name),
                     'src/specgraph/calc.py:2']) == 0
        result = json.loads(capsys.readouterr().out)
        assert result['mode'] == 'FULL_SUITE'
        assert result['command'][-2:] == ['tests', 'tools/tests']
        assert result['fallback_reasons']
        assert 'src/specgraph/calc.py:2' in result['unproven_blocks'] or not result['candidates']


def test_mandatory_outside_capture_scope_is_executed_by_fallback(tmp_path, monkeypatch):
    import os
    import subprocess
    root = _project(tmp_path)
    receipt = capture(root, root / 'trace.json', pytest_args=['tests'])
    mandatory = 'tools/tests/test_other.py::test_imported'
    result = recommend_from_receipt(['src/specgraph/calc.py:2'], receipt, root=root, mandatory=[mandatory])
    assert result['mode'] == 'FULL_SUITE'
    assert result['command'][-1] == mandatory
    assert result['full_suite_command'] == result['command']
    assert mandatory in result['tests']
    assert result['reasons'][mandatory] == ['mandatory']
    env = dict(os.environ, PYTHONPATH=str(root / 'src'))
    execution = subprocess.run(result['command'], cwd=root, env=env, capture_output=True, text=True)
    assert execution.returncode == 0, execution.stdout + execution.stderr
    assert '2 passed' in execution.stdout
    assert mandatory in result['candidate_command']
    mutation = {'name': 'outside-capture-mandatory', 'path': 'src/specgraph/calc.py',
                'find': 'return a + b', 'replace': 'return a - b'}
    report = compare(root, receipt, [mutation], mandatory=[mandatory])
    trial = report['mutations'][0]
    assert trial['full_test_count'] == 2
    assert trial['selected_test_count'] == 2
    assert set(trial['full_failures']) == set(trial['selected_failures'])
    assert mandatory in trial['full_failures']


def test_long_candidate_selection_uses_a_pytest_argument_file(tmp_path):
    import subprocess, sys
    from specgraph.test_selection import pytest_command
    (tmp_path / 'test_many.py').write_text(
        'import pytest\n@pytest.mark.parametrize("n", range(1500))\ndef test_n(n):\n    pass\n', encoding='utf-8')
    args = [f'test_many.py::test_n[{n}]' for n in range(1500)]
    short = pytest_command(args[:3], tmp_path)
    assert short[-3:] == args[:3]
    long = pytest_command(args, tmp_path)
    assert long[-1] == '@' + str(tmp_path / 'pytest-args.txt') and len(long) == 8
    # The real runner accepts the file: every selected test executes.
    run = subprocess.run([sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', long[-1]],
                         cwd=tmp_path, capture_output=True, text=True)
    assert run.returncode == 0 and '1500 passed' in run.stdout, run.stdout[-500:]


@pytest.mark.parametrize('engine', ['ruff', 'python-ast'])
def test_static_import_impact_covers_transitive_subprocess_script_and_lazy_imports(tmp_path, monkeypatch, engine):
    import shutil as _shutil
    from specgraph import test_selection
    from specgraph.test_selection import static_test_files
    if engine == 'ruff' and not _shutil.which('ruff'):
        pytest.skip('Ruff is not installed')
    if engine == 'python-ast':
        monkeypatch.setattr(test_selection, '_ruff_import_graph', lambda root, folders: None)
    files = {
        'src/pkg/__init__.py': 'def __getattr__(name):\n    return 1\n',
        'src/pkg/a.py': 'VALUE = 1\n',
        'src/pkg/b.py': 'from . import a\n',
        'src/pkg/c.py': 'X = 2\n',
        'tools/helper.py': 'from pkg.b import a\n',
        'tests/test_import.py': 'from pkg import b\n',
        'tests/test_cli.py': 'import subprocess, sys\nsubprocess.run([sys.executable, "-m", "pkg.a"])\n',
        'tests/test_code.py': 'CODE = "from pkg.a import VALUE; print(VALUE)"\n',
        'tests/test_script.py': 'SCRIPT = "tools/helper.py"\n',
        'tests/test_lazy.py': 'from pkg import Something\n',
        'tests/test_other.py': 'from pkg.c import X\n',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding='utf-8')
    hit = static_test_files(tmp_path, {'src/pkg/a.py'})
    assert sorted(hit) == ['tests/test_cli.py', 'tests/test_code.py', 'tests/test_import.py',
                           'tests/test_lazy.py', 'tests/test_script.py']
    assert sorted(static_test_files(tmp_path, {'src/pkg/c.py'})) == ['tests/test_lazy.py', 'tests/test_other.py']
    report = {}
    both = static_test_files(tmp_path, {'src/pkg/a.py', 'src/pkg/c.py'}, report=report)
    assert report['engine'] == engine
    assert both['tests/test_lazy.py'] == ['src/pkg/a.py', 'src/pkg/c.py'] and both['tests/test_other.py'] == ['src/pkg/c.py']


def test_symbol_impact_uses_the_native_core_and_falls_back_without_it(tmp_path, monkeypatch):
    from specgraph import test_selection
    if test_selection.core_binary() is None:
        pytest.skip('specgraph-core is not built')
    files = {
        'src/pkg/__init__.py': '',
        'src/pkg/a.py': 'def used():\n    return 1\n\n\ndef unused():\n    return 2\n',
        'tests/test_a.py': 'from pkg.a import used\n\n\ndef test_used():\n    assert used() == 1\n\n\n'
                           'def test_other():\n    assert True\n',
    }
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding='utf-8')
    reached = test_selection.symbol_impact(tmp_path, ['src/pkg/a.py:2'])
    assert reached['tests'] == ['tests/test_a.py::test_used'] and not reached['files'], reached
    assert test_selection.symbol_impact(tmp_path, ['src/pkg/a.py:6'])['tests'] == []
    monkeypatch.setenv('SPECGRAPH_CORE', str(tmp_path / 'missing-binary'))
    assert test_selection.symbol_impact(tmp_path, ['src/pkg/a.py:2']) is None


def test_missed_failures_are_confirmed_by_an_isolated_rerun(tmp_path):
    from specgraph.test_selection import _confirm
    (tmp_path / 'src').mkdir()
    (tmp_path / 'tests').mkdir()
    (tmp_path / 'tests' / 'test_x.py').write_text(
        'def test_broken():\n    assert False\n\n\ndef test_passes_alone():\n    assert True\n', encoding='utf-8')
    confirmed, unstable = _confirm(tmp_path, {'tests/test_x.py::test_broken', 'tests/test_x.py::test_passes_alone'})
    assert confirmed == ['tests/test_x.py::test_broken']
    assert unstable == ['tests/test_x.py::test_passes_alone']
    assert _confirm(tmp_path, set()) == ([], [])


@pytest.mark.parametrize('failure', ['missing', 'timeout', 'nonzero', 'json', 'shape'])
def test_ruff_failures_use_conservative_ast(tmp_path, monkeypatch, failure):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    import subprocess
    from types import SimpleNamespace
    from specgraph import test_selection as selection
    files = {'src/pkg/__init__.py': '', 'src/pkg/a.py': 'VALUE = 1\n',
             'tests/test_direct.py': 'import pkg.a\n',
             'tests/test_broken.py': 'def broken(\n'}
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    def run(*args, **kwargs):
        if failure == 'missing':
            raise OSError('offline missing Ruff')
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args[0], 300)
        return SimpleNamespace(returncode=1 if failure == 'nonzero' else 0,
                               stdout='[]' if failure == 'shape' else 'not JSON')
    monkeypatch.setattr(selection.subprocess, 'run', run)
    report = {}
    assert selection.static_test_files(tmp_path, {'src/pkg/a.py'}, report=report) == {
        'tests/test_broken.py': ['src/pkg/a.py'],
        'tests/test_direct.py': ['src/pkg/a.py']}
    assert report == {'engine': 'python-ast'}


def test_missing_core_and_unreadable_lazy_package_fail_conservatively(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    from specgraph import test_selection as selection
    monkeypatch.setenv('SPECGRAPH_CORE', str(tmp_path / 'absent-core'))
    assert selection.core_binary() is None
    assert selection.symbol_impact(tmp_path, ['src/a.py:1']) is None
    assert selection._lazy_package(tmp_path / 'absent-init.py') is True
    init = tmp_path / '__init__.py'
    init.write_bytes(b'\xff')
    assert selection._lazy_package(init) is True
    init.write_text('VALUE = 1\n', encoding='utf-8')
    assert selection._lazy_package(init) is False


def test_data_change_is_an_unproven_gap_without_authority(tmp_path, monkeypatch):
    """@verifies SPEC-SPECGRAPH-X5 [AC-03]"""
    from specgraph import test_selection as selection
    (tmp_path / 'data.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(selection, 'symbol_impact', lambda *a: None)
    monkeypatch.setattr(selection, '_ruff_import_graph', lambda *a: None)
    receipt = {'schema': selection.SCHEMA, 'tests': ['tests/test_a.py::test_a'],
               'executed_blocks': {'tests/test_a.py::test_a': []},
               'fingerprints': {}, 'environment': selection._environment(),
               'suite_returncode': 0, 'complete': True,
               'dependency_closure': 'EXTERNALLY_VERIFIED', 'attestation': 'VERIFIED'}
    result = selection.recommend_from_receipt(['data.json'], receipt, root=tmp_path)
    assert result['mode'] == 'FULL_SUITE'
    assert result['unproven_blocks'] == ['data.json']
    assert 'no independent C5 attestation' in result['fallback_reasons']
    assert 'IMPACT_GAP' in result['applicability']['reason_codes']
    assert result['applicability']['execution_authorized'] is False
    assert result['applicability']['supports_success'] is False
