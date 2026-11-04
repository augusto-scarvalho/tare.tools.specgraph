"""X5 local pytest observations and falsifiable, conservative test candidates.

A trace is not a C5 externally verified EXECUTED_COVERAGE claim. Unknown
transitive dependencies prevent selective execution without independent proof.
"""
from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import subprocess
import sys
import tempfile
import time

from .evidence import classify_test_relation, evaluate_evidence
from .x5_scope import build_x5_scope

SCHEMA = 'specgraph-x5-local-observation/2'
CONFIG = {'.toml', '.ini', '.cfg', '.yaml', '.yml', '.lock'}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(root: Path) -> dict[str, str]:
    result = {}
    for p in root.rglob('*'):
        if not p.is_file() or any(part in {'.git', '.pytest_cache', '__pycache__', '.venv', 'venv', '.specgraph'} for part in p.relative_to(root).parts):
            continue
        if p.suffix == '.py' or p.suffix in CONFIG or p.name.startswith('requirements'):
            result[p.relative_to(root).as_posix()] = _sha(p.read_bytes())
    return dict(sorted(result.items()))


def _environment() -> dict:
    try:
        pytest_version = version('pytest')
    except PackageNotFoundError:
        pytest_version = None
    return {'python': sys.version, 'executable': sys.executable, 'platform': platform.platform(),
            'pytest': pytest_version, 'PYTHONPATH': os.environ.get('PYTHONPATH'),
            'PYTEST_ADDOPTS': os.environ.get('PYTEST_ADDOPTS'),
            'PYTEST_PLUGINS': os.environ.get('PYTEST_PLUGINS')}


def static_test_files(root: Path, changed_paths: set[str], *, test_roots=('tests', 'tools/tests'),
                      source_roots=('src', 'tools'), report: dict | None = None) -> dict[str, list[str]]:
    """Test files that reach a changed project module through imports, directly or transitively.

    The import graph comes from Ruff's native analyzer when available (fast enough to run on
    every change); otherwise from Python's ``ast``, cached per file stamp in the process.

    Importing a.b.c also runs a/__init__ and a/b/__init__. String literals naming a project
    module (``-m specgraph.cli`` or code passed to ``-c``) count, so subprocess use is covered;
    so do script paths such as ``tools/x.py`` loaded by path or through ``sys.path``.
    An unparsable file depends on every module.
    """
    import ast
    import re
    from .resolver import python_module_name
    root = root.resolve()

    def files(folder: str) -> list[Path]:
        base = root / folder
        return sorted(p for p in base.rglob('*.py') if '__pycache__' not in p.parts) if base.is_dir() else []

    project = {python_module_name(p.relative_to(root).as_posix(), list(source_roots)): p for folder in source_roots
               for p in files(folder)}
    project.pop('', None)

    def reached(names: set[str]) -> set[str]:
        return {'.'.join(n.split('.')[:i]) for n in names for i in range(1, n.count('.') + 2)} & set(project)

    def names_in(tree, module: str, package_file: bool) -> set[str]:
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ''
                if node.level:
                    package = module.split('.') if package_file else module.split('.')[:-1]
                    base = '.'.join(filter(None, [*package[:len(package) - node.level + 1], base]))
                names.add(base)
                for alias in node.names:
                    name = f'{base}.{alias.name}'
                    # A package attribute may be loaded lazily from any submodule.
                    names.update([name] if name in project or base not in project else
                                 [m for m in project if m.startswith(base + '.')])
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                tokens = re.findall(r'[A-Za-z_][\w.]*', node.value)  # e.g. -m specgraph.cli, tools/x.py
                names.update(tokens + [t[:-3] for t in tokens if t.endswith('.py')])
                if 'import ' in node.value:  # code passed to python -c
                    try:
                        names |= names_in(ast.parse(node.value), module, package_file)
                    except SyntaxError:
                        pass
        return names

    def dependencies(path: Path, module: str) -> set[str]:
        try:
            stat = path.stat()
            key = (str(path), stat.st_mtime_ns, stat.st_size)
            if key not in _AST_CACHE:
                _AST_CACHE[key] = ast.parse(path.read_text(encoding='utf-8'))
            tree = _AST_CACHE[key]
        except (SyntaxError, UnicodeDecodeError, OSError):
            return set(project)
        return reached(names_in(tree, module, path.name == '__init__.py'))

    rel_of = {m: p.relative_to(root).as_posix() for m, p in project.items()}
    project_files = set(rel_of.values())
    graph = _ruff_import_graph(root, [f for f in (*source_roots, *test_roots) if (root / f).is_dir()])
    if report is not None:
        report['engine'] = 'ruff' if graph is not None else 'python-ast'

    def file_dependencies(path: Path, module: str) -> set[str]:
        rel = path.relative_to(root).as_posix()
        if graph is None:
            return {rel_of[m] for m in dependencies(path, module)}
        direct = graph.get(rel, set()) & project_files
        needed = set(direct)
        for dep in direct:  # importing a/b/c.py also runs a/__init__.py and a/b/__init__.py
            parts = dep.split('/')[:-1]
            needed |= {'/'.join(parts[:i]) + '/__init__.py' for i in range(1, len(parts) + 1)} & project_files
            # Ruff lists a package itself only for package-level names; a lazy __getattr__ may load any submodule.
            if dep.endswith('/__init__.py') and _lazy_package(root / dep):
                needed |= {f for f in project_files if f.startswith(dep[:-len('__init__.py')])}
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            return set(project_files)
        # Scripts named by path (spec_from_file_location, subprocess) and code passed as a
        # string to python -c are invisible to import analysis; a text scan covers both.
        for token in re.findall(r'[\w./\\-]+\.py\b', text):
            token = token.replace('\\', '/').lstrip('./')
            needed |= {f for f in project_files if f == token or f.endswith('/' + token)}
        names = re.findall(r'\bimport\s+([A-Za-z_][\w.]*)', text)
        for base, imported in re.findall(r'\bfrom\s+([A-Za-z_][\w.]*)\s+import\s+\(?([\w\s,]+)', text):
            members = [n.split()[0] for n in imported.split(',') if n.split()]
            # "from pkg import mod" names a module; other names come from pkg itself.
            names += [f'{base}.{m}' if f'{base}.{m}' in rel_of else base for m in members] or [base]
        for name in names:
            parts = name.split('.')
            module = next(('.'.join(parts[:i]) for i in range(len(parts), 0, -1) if '.'.join(parts[:i]) in rel_of), None)
            if module:
                target = rel_of[module]
                needed.add(target)
                if module == name and target.endswith('/__init__.py') and _lazy_package(root / target):
                    needed |= {f for f in project_files if f.startswith(target[:-len('__init__.py')])}
        return needed

    importers: dict[str, set[str]] = {}
    for module, path in project.items():
        for dependency in file_dependencies(path, module):
            importers.setdefault(dependency, set()).add(rel_of[module])
    # Per changed file, every project file that transitively imports it.
    closures = {}
    for changed in project_files & set(changed_paths):
        closure, frontier = {changed}, [changed]
        while frontier:
            for importer in importers.get(frontier.pop(), ()):
                if importer not in closure:
                    closure.add(importer)
                    frontier.append(importer)
        closures[changed] = closure
    result = {}
    for folder in test_roots:
        for path in files(folder):
            needed = file_dependencies(path, '')
            hit = sorted(changed for changed, closure in closures.items() if needed & closure)
            if hit:
                result[path.relative_to(root).as_posix()] = hit
    return result


def _ruff_import_graph(root: Path, folders: list[str]) -> dict[str, set[str]] | None:
    """File -> files it imports, from Ruff's native (Rust) analyzer; None when Ruff is unavailable."""
    if not folders:
        return None
    executable = shutil.which('ruff')
    command = [executable] if executable else [sys.executable, '-m', 'ruff']
    try:
        proc = subprocess.run([*command, 'analyze', 'graph', '--detect-string-imports', '--min-dots', '1', *folders],
                              cwd=root, capture_output=True, text=True, timeout=300)
        raw = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    posix = lambda value: value.replace('\\', '/')  # noqa: E731 - Windows output uses backslashes
    return {posix(k): {posix(v) for v in values} for k, values in raw.items()}


def core_binary() -> str | None:
    """The native specgraph-core binary: $SPECGRAPH_CORE, PATH, or this checkout's release build."""
    configured = os.environ.get('SPECGRAPH_CORE')
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which('specgraph-core')
    if found:
        return found
    built = Path(__file__).resolve().parents[2] / 'rust/specgraph-core/target/release' / (
        'specgraph-core.exe' if os.name == 'nt' else 'specgraph-core')
    return str(built) if built.is_file() else None


def symbol_impact(root: Path, changed: list[str], *, source_roots=('src', 'tools'),
                  test_roots=('tests', 'tools/tests')) -> dict | None:
    """Tests reached from changed lines through the native symbol graph; None without the core."""
    binary = core_binary()
    if binary is None:
        return None
    root = Path(root).resolve()
    command = [binary, 'impact', '--root', str(root), '--cache', str(root / '.specgraph' / 'core-cache')]
    command += [a for r in source_roots if (root / r).is_dir() for a in ('--source-root', r)]
    command += [a for r in test_roots if (root / r).is_dir() for a in ('--test-root', r)]
    for change in changed:
        path, line = _change(change)
        command += ['--changed', f'{path}:{line or 0}']  # no line: module level, the conservative case
    try:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=300)
        value = json.loads(proc.stdout) if proc.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    return {'engine': value.get('engine'), 'tests': value.get('tests', []), 'files': set(value.get('test_files', [])),
            'full_suite': bool(value.get('full_suite')), 'modes': value.get('modes', [])}


def _lazy_package(init: Path) -> bool:
    try:
        return 'def __getattr__' in init.read_text(encoding='utf-8')
    except (OSError, UnicodeDecodeError):
        return True


# Parsed trees by (path, mtime, size): repeated recommendations in one process parse once.
_AST_CACHE: dict = {}


def pytest_command(args: list[str], folder: Path) -> list[str]:
    """Long selections go through an @file: Windows caps a command line at 32,767 characters."""
    cmd = [sys.executable, '-m', 'pytest', '-p', '_pytest_x5', '-p', 'no:cacheprovider']
    if len(subprocess.list2cmdline(cmd + args)) <= 30000:
        return cmd + args
    argfile = folder / 'pytest-args.txt'
    argfile.write_text('\n'.join(args), encoding='utf-8')  # pytest >= 8.2 reads one argument per line
    return cmd + ['@' + str(argfile)]


def _run(root: Path, args: list[str], *, trace: bool = False) -> dict:
    root = root.resolve()
    with tempfile.TemporaryDirectory(prefix='specgraph-x5-run-') as td:
        output = Path(td) / 'pytest.json'
        env = dict(os.environ, SG_X5_ROOT=str(root), SG_X5_OUTPUT=str(output))
        if trace:
            env['SG_X5_TRACE'] = '1'
        else:
            env.pop('SG_X5_TRACE', None)
        # Standalone plugin: never import specgraph from the original worktree.
        shutil.copyfile(Path(__file__).with_name('_pytest_x5.py'), Path(td) / '_pytest_x5.py')
        env['PYTHONPATH'] = str(root / 'src') + os.pathsep + td + os.pathsep + env.get('PYTHONPATH', '')
        cmd = pytest_command(args, Path(td))
        start = time.perf_counter()
        proc = subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True)
        seconds = time.perf_counter() - start
        if not output.exists():
            raise RuntimeError('pytest did not produce an inventory: ' + proc.stdout[-2000:] + proc.stderr[-2000:])
        result = json.loads(output.read_text(encoding='utf-8'))
        if result.get('complete', True) is False or result.get('truncated', False):
            raise RuntimeError('pytest observation incomplete/truncated: ' + str(result.get('reason')))
        if proc.returncode not in (0, 1) or not result['tests']:
            raise RuntimeError('pytest collection/execution failed: ' + proc.stdout[-2000:] + proc.stderr[-2000:])
        origin = result.get('source_origin')
        if origin and not Path(origin).is_relative_to(root / 'src'):
            raise RuntimeError('pytest imported specgraph outside worktree: ' + origin)
        # What a caller would place in model context; Tare's verify tool keeps the first 16 KiB.
        result.update(seconds=seconds, returncode=proc.returncode, command=cmd,
                      output_bytes=len((proc.stdout + proc.stderr).encode('utf-8')))
        if not trace:
            result.pop('executed_blocks')
        return result


def capture(root: Path, output: Path, *, pytest_args: list[str] | None = None) -> dict:
    root = root.resolve()
    args = pytest_args if pytest_args is not None else ['tests', 'tools/tests']
    start = time.perf_counter()
    fingerprints = _files(root)
    environment = _environment()
    scan_seconds = time.perf_counter() - start
    run = _run(root, args, trace=True)
    receipt = {'schema': SCHEMA, 'tests': run['tests'], 'executed_blocks': run['executed_blocks'],
               'fingerprints': fingerprints, 'environment': environment,
               'config_sha256': _sha(json.dumps({k:v for k,v in fingerprints.items() if Path(k).suffix in CONFIG}, sort_keys=True).encode()),
               'environment_sha256': _sha(json.dumps(environment, sort_keys=True).encode()),
               'pytest_args': args, 'capture_seconds': scan_seconds + run['seconds'],
               'scan_seconds': scan_seconds, 'suite_seconds': run['seconds'],
               'suite_returncode': run['returncode'], 'failed': run['failed'],
               'truncated': run.get('truncated', False), 'complete': run.get('complete', True),
               'reason': run.get('reason'), 'durations': run.get('durations', {}),
               'counters': run.get('counters', {}),
               'dependency_closure': 'UNKNOWN', 'attestation': 'NONE_LOCAL_OBSERVATION_ONLY',
               'relation': classify_test_relation('EXECUTED_COVERAGE') + '_LOCAL_UNATTESTED'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def _change(change: str) -> tuple[str, int | None]:
    path, sep, line = change.rpartition(':')
    if sep and line.isdecimal():
        return path.replace('\\', '/'), int(line)
    return change.replace('\\', '/'), None


def _applicability(root, changed, receipt, tests, edges, fingerprints, current,
                   valid, stale, config, env_changed, mandatory, policy_tests, unreached):
    """Structural permission to experiment, never execution evidence or authority."""
    def safe(path):
        if (not isinstance(path, str) or not path or len(path.encode('utf-8')) > 1024
                or any(ord(c) < 32 for c in path) or '\\' in path or ':' in path
                or any(p in {'', '.', '..'} for p in path.split('/'))
                or path.startswith('/')):
            return False
        try:
            return (root / path).resolve().relative_to(root).as_posix() == path
        except (OSError, ValueError):
            return False

    paths = {_change(c)[0] for c in changed}
    canonical = all(safe(p) for p in paths | fingerprints.keys() | current.keys())
    canonical = canonical and all(
        safe(t.split('::')[0]) and t.split('::')[0] in fingerprints
        and t.split('::')[0] in current for t in tests)
    for blocks in edges.values():
        for block in blocks:
            path, sep, line = block.rpartition(':')
            canonical = (canonical and bool(sep) and len(line) <= 10
                         and line.isdecimal() and int(line) > 0)
            canonical = canonical and safe(path) and path in fingerprints and path in current
    canonical = canonical and all(
        c == p or (line is not None and line > 0 and c == f'{p}:{line}')
        for c, (p, line) in zip(changed, map(_change, changed)))
    passing = (valid and receipt.get('suite_returncode') == 0
               and not receipt.get('failed') and not receipt.get('truncated')
               and receipt.get('complete', True) is True
               and all(isinstance(v, str) and len(v) == 64
                       and all(c in '0123456789abcdef' for c in v)
                       for v in fingerprints.values()))
    checks = [
        ('INVALID_CAPTURE', not passing),
        ('EMPTY_CHANGES', not paths),
        ('UNSAFE_PATH', not canonical),
        ('ADDED_OR_DELETED_PATH', any(p not in fingerprints or p not in current for p in paths)),
        ('UNLISTED_CHANGE', bool(stale)),
        ('CONFIGURATION_CHANGE', bool(config) or any(
            Path(p).name == 'conftest.py' or Path(p).name.startswith('requirements')
            for p in paths)),
        ('ENVIRONMENT_CHANGE', env_changed),
        ('UNKNOWN_MANDATORY', bool(set(mandatory or []) - set(tests))),
        ('EMPTY_POLICY', not policy_tests),
        ('IMPACT_GAP', bool(unreached)),
    ]
    codes = [code for code, failed in checks if failed]
    return {'status': 'FALLBACK' if codes else 'CANDIDATE',
            'execution_authorized': False, 'supports_success': False,
            'dependency_closure': 'UNKNOWN', 'reason_codes': codes,
            'limitations': ['LOCAL_TRANSITIVE_CLOSURE_UNKNOWN', 'NO_TARGET_EXECUTION_PROOF']}


def recommend_from_receipt(changed: list[str], receipt: dict, *, root: Path = Path('.'),
                           mandatory: list[str] | None = None,
                           c5_evidence=None, c5_current=None, attestation_verifier=None) -> dict:
    # These observations/verifier are supplied only by trusted Python composition.
    root = root.resolve()
    start = time.perf_counter()
    receipt = receipt if isinstance(receipt, dict) else {}
    tests = receipt.get('tests')
    tests = tests if isinstance(tests, list) and all(isinstance(t, str) for t in tests) else []
    edges = receipt.get('executed_blocks')
    edges = edges if isinstance(edges, dict) and all(
        isinstance(k, str) and isinstance(v, list) and all(isinstance(b, str) for b in v)
        for k, v in edges.items()) else {}
    fingerprints = receipt.get('fingerprints')
    fingerprints = fingerprints if isinstance(fingerprints, dict) and all(isinstance(k, str) for k in fingerprints) else {}
    changes = [_change(c) for c in changed]
    changed_paths = {p for p, _ in changes}
    current = _files(root)
    stale = sorted(k for k in current.keys() | fingerprints.keys() if current.get(k) != fingerprints.get(k) and k not in changed_paths)
    config = sorted(p for p in changed_paths if Path(p).suffix in CONFIG or p.startswith('requirements'))
    env_changed = receipt.get('environment') != _environment()
    reasons: dict[str, set[str]] = {}
    unproven = []
    for path, line in changes:
        block = f'{path}:{line}' if line else path
        if path not in fingerprints or path not in current:
            unproven.append(block)
            continue
        hits = [t for t, blocks in edges.items() if t in tests and any(
            b == block if line else b.startswith(path + ':') for b in blocks)]
        if not hits:
            unproven.append(block)
        for test in hits:
            reasons.setdefault(test, set()).add('locally executed ' + block)
    for test in mandatory or []:
        reasons.setdefault(test, set()).add('mandatory')
        if test in tests:
            continue
        else:
            unproven.append('unknown mandatory nodeid: ' + test)
    valid = (receipt.get('schema') == SCHEMA and receipt.get('suite_returncode') == 0
             and isinstance(tests, list) and bool(tests) and set(edges) == set(tests)
             and all(isinstance(edges[t], list) for t in tests) and bool(fingerprints))
    # The plugin cannot observe subprocesses/native imports or assert a transitive closure.
    # An unverified JSON "complete":true is deliberately ignored.
    problems = (['invalid/incomplete local capture'] if not valid else []) + (
        ['unknown transitive dependency closure'] if receipt.get('dependency_closure') != 'EXTERNALLY_VERIFIED' else [])
    # Even a forged string is not an independent C5 verifier: remain conservative.
    problems += ['no independent C5 attestation']
    if stale: problems.append('unlisted changed fingerprints: ' + ', '.join(stale))
    if config: problems.append('configuration changed: ' + ', '.join(config))
    if env_changed: problems.append('environment changed')
    if unproven: problems.append('unproven blocks: ' + ', '.join(unproven))
    candidates = sorted(reasons)
    args = receipt.get('pytest_args')
    if not isinstance(args, list) or not args or not all(isinstance(arg, str) for arg in args):
        args = ['tests', 'tools/tests']
    base = [sys.executable, '-m', 'pytest', *args]
    missing_mandatory = sorted(set(mandatory or []) - set(tests))
    full_command = base + missing_mandatory
    # Measured policy: traced ∪ statically imported tests; a changed file that neither
    # reaches runs everything. Advisory like the candidates; the fallback above still rules.
    core = symbol_impact(root, changed)
    if core is not None:
        # Symbol-level impact from the native core; it asks for everything when it must.
        static_report = {'engine': core['engine']}
        static_tests = sorted(t for t in tests if t.split('::')[0] in core['files']
                              or any(t == c or t.startswith(c + '[') for c in core['tests']))
        unreached = ['specgraph-core requested the full suite'] if core['full_suite'] else []
        if unproven:
            # A nonempty symbol selection alone cannot close unrelated trace gaps.
            static_files = static_test_files(root, changed_paths)
            reached_paths = {path for hits in static_files.values() for path in hits}
            unreached += sorted({b.rpartition(':')[0] or b for b in unproven} - reached_paths)
        policy_name = 'TRACE_OR_SYMBOL_IMPACT'
    else:
        static_report = {}
        static_files = static_test_files(root, changed_paths, report=static_report)
        static_tests = sorted(t for t in tests if t.split('::')[0] in static_files)
        reached_paths = {path for hits in static_files.values() for path in hits}
        unreached = sorted({b.rpartition(':')[0] or b for b in unproven} - reached_paths)
        policy_name = 'TRACE_OR_STATIC_IMPORT'
    policy_tests = sorted(set(candidates) | set(static_tests) | set(mandatory or []))
    authorized = False
    # Trusted composition must supply all C5 inputs before doing scope work.
    if (isinstance(c5_evidence, dict) and isinstance(c5_current, dict)
            and callable(attestation_verifier)):
        try:
            scope = build_x5_scope(root, changed, receipt, policy_tests, mandatory or [])
            scope_sha256 = _sha(json.dumps(scope, sort_keys=True, ensure_ascii=False,
                                          separators=(',', ':'), allow_nan=False).encode('utf-8'))
            project = scope['project']
            target = {f'{project}:{path}': digest
                      for path, digest in scope['target_fingerprints'].items()}
            capture_config = _sha(json.dumps(
                {k: v for k, v in fingerprints.items() if Path(k).suffix in CONFIG},
                sort_keys=True).encode())
            capture_environment = _sha(json.dumps(receipt.get('environment'), sort_keys=True).encode())
            # Never manufacture current observations from the execution claim.
            validity = evaluate_evidence(c5_evidence, c5_current,
                                         attestation_verifier=attestation_verifier)
            dependencies = c5_evidence.get('dependencies', {})
            coverage = c5_evidence.get('coverage', {})
            snapshot = c5_evidence.get('snapshot', {})
            authorized = (
                valid and not stale and not config and not env_changed
                and not unreached and bool(policy_tests)
                and validity.get('status') == 'VALID'
                and validity.get('supports_success') is True
                and validity.get('relation') == 'EXECUTED_COVERAGE'
                and validity.get('attestation', {}).get('status') == 'VERIFIED_EXTERNALLY'
                and dependencies.get('complete') is True
                and 'x5_scope' not in c5_evidence
                and c5_evidence.get('x5_scope_sha256') == scope_sha256
                and snapshot.get('repository') == project
                and c5_current.get('snapshot') == snapshot
                and receipt.get('config_sha256') == capture_config
                and receipt.get('environment_sha256') == capture_environment
                and c5_evidence.get('config_sha256') == capture_config
                and c5_evidence.get('environment_sha256') == capture_environment
                and all(dependencies.get('hashes', {}).get(k) == v for k, v in target.items())
                and {f'{project}:{test}' for test in policy_tests} <= set(coverage.get('tests', []))
                and {f"{project}:{item['path']}:{item['line']}" for item in scope['changed']}
                    <= set(coverage.get('symbols', [])))
        except Exception:
            # Missing, malformed or failing owner observations never grant selection.
            authorized = False
    if authorized:
        problems = []
    elif c5_evidence is not None:
        problems.append('C5 evidence does not qualify for this exact target scope')
    fallback = not authorized
    selected = sorted(set(tests) | set(mandatory or [])) if fallback else policy_tests
    return {'mode': 'FULL_SUITE' if fallback else 'SELECTIVE_SCOPED_C5', 'tests': selected,
            'static_candidates': static_tests, 'unreached_paths': unreached,
            'static_engine': static_report.get('engine'),
            'policy': 'FULL_SUITE' if unreached or not policy_tests else policy_name,
            'policy_tests': policy_tests,
            'applicability': _applicability(
                root, changed, receipt, tests, edges, fingerprints, current, valid,
                stale, config, env_changed, mandatory, policy_tests, unreached),
            'candidates': candidates, 'reasons': {t: sorted(v) for t,v in sorted(reasons.items())},
            'unproven_blocks': sorted(set(unproven)), 'fallback_reasons': problems,
            'command': full_command if fallback else [sys.executable, '-m', 'pytest', *policy_tests],
            'candidate_command': [sys.executable, '-m', 'pytest', *sorted(set(candidates) | set(missing_mandatory))]
            if candidates or missing_mandatory else None,
            'full_suite_command': full_command,
            'qualification': 'NOT_QUALIFIED_LOCAL_TRACE_ONLY' if fallback else 'QUALIFIED_SCOPED_C5',
            'selection_seconds': time.perf_counter() - start}


def _confirm(copy: Path, missed: set[str], runner=None) -> tuple[list[str], list[str]]:
    """Split failures a selection missed: those failing again alone are omissions; the rest are
    unstable (e.g. timing under load in the full suite) and do not count against the selection."""
    if not missed:
        return [], []
    rerun = (runner or _run)(copy, sorted(missed))
    confirmed = sorted(missed & set(rerun['failed']))
    return confirmed, sorted(missed - set(confirmed))


def compare(root: Path, receipt: dict, mutations: list[dict], *, mandatory: list[str] | None = None) -> dict:
    """Run real known mutations in isolated copies; any missed killer suspends selection."""
    root = root.resolve()
    if receipt.get('suite_returncode') != 0 or not mutations:
        raise ValueError('A passing baseline receipt and nonempty mutations are required')
    def seconds(value):
        import math
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError('cost seconds must be finite and nonnegative')
        return value

    capture_seconds = seconds(receipt['capture_seconds'])
    results = []
    for mutation in mutations:
        case_started = time.perf_counter()
        phases = []

        def run(phase, directory, tests):
            started = time.perf_counter()
            result = _run(directory, tests)
            phases.append({'phase': phase, 'tests': list(tests), 'seconds': seconds(result['seconds']),
                           'wall_seconds': seconds(time.perf_counter() - started)})
            return result

        def confirm_runner(directory, tests):
            return run('confirmation', directory, tests)
        path = mutation['path'].replace('\\', '/')
        # Strip relative prefixes before path parsers can collapse UNC separators.
        while path.startswith('./'):
            path = path[2:]
        relative = PurePosixPath(path)
        # Check normalized Windows anchors on every host, before copying anything.
        if relative.is_absolute() or PureWindowsPath(path).anchor or '..' in relative.parts:
            raise ValueError('mutation must be a repository-relative path')
        path = relative.as_posix()
        with tempfile.TemporaryDirectory(prefix='x5-') as td:
            copy = Path(td) / 'repo'
            # Windows long-path prefix for archived evidence; no assumptions about writable ancestors.
            long_root = Path('\\\\?\\' + str(root)) if os.name == 'nt' else root
            long_copy = Path('\\\\?\\' + str(copy)) if os.name == 'nt' else copy
            def excluded(directory, names):
                return {n for n in names if n in {'.git', '.pytest_cache', '__pycache__', '.venv', '.specgraph', 'target'}}
            try:
                shutil.copytree(long_root, long_copy, ignore=excluded)
            except OSError:
                if long_copy.exists():
                    shutil.rmtree(long_copy)
                raise
            try:
                target = copy / path
                before = target.read_text(encoding='utf-8')
                if not mutation['find'] or before.count(mutation['find']) != 1:
                    raise ValueError('mutation find must occur exactly once: ' + path)
                line = before[:before.index(mutation['find'])].count('\n') + 1
                target.write_text(before.replace(mutation['find'], mutation['replace']), encoding='utf-8')
                # Include every mutated line, not just the first one.
                changed = [f'{path}:{n}' for n in range(line, line + max(1, mutation['find'].count('\n') + 1))]
                setup_seconds = seconds(time.perf_counter() - case_started)
                selection_started = time.perf_counter()
                recommendation = recommend_from_receipt(changed, receipt, root=copy, mandatory=mandatory)
                selection_wall_seconds = seconds(time.perf_counter() - selection_started)
                seconds(recommendation['selection_seconds'])
                candidates = recommendation['candidates']
                full = run('full_audit', copy, recommendation['full_suite_command'][3:])
                selected = run('trace_candidate', copy, recommendation['candidate_command'][3:]) if recommendation['candidate_command'] else None
                omitted, unstable = _confirm(copy, set(full['failed']) - set(selected['failed'] if selected else []), runner=confirm_runner)
                # The measured policy runs everything when a changed file is unreached.
                if recommendation['policy'] == 'FULL_SUITE':
                    policy = full
                elif selected and recommendation['policy_tests'] == candidates:
                    policy = selected
                else:
                    policy = run('policy_candidate', copy, recommendation['policy_tests'])
                policy_omitted, policy_unstable = _confirm(copy, set(full['failed']) - set(policy['failed']), runner=confirm_runner)
                results.append({'mutation': mutation.get('name', path), 'changed': changed,
                                'policy': recommendation['policy'], 'policy_test_count': len(policy['tests']),
                                'policy_failures': policy['failed'], 'policy_seconds': policy['seconds'],
                                'policy_omitted': policy_omitted, 'policy_unstable': policy_unstable,
                                'unstable_not_omitted': unstable,
                                'output_bytes': {'full': full['output_bytes'], 'policy': policy['output_bytes'],
                                                 'selected': selected['output_bytes'] if selected else 0},
                                'static_candidate_count': len(recommendation['static_candidates']),
                                'candidates': candidates, 'fallback': recommendation['mode'],
                                'full_test_count': len(full['tests']),
                                'selected_test_count': len(selected['tests']) if selected else 0,
                                'unproven_blocks': recommendation['unproven_blocks'],
                                'full_failures': full['failed'], 'selected_failures': selected['failed'] if selected else [],
                                'omitted_relevant': omitted, 'full_seconds': full['seconds'],
                                'selected_seconds': selected['seconds'] if selected else 0,
                                'selection_seconds': recommendation['selection_seconds']})
            finally:
                cleanup_started = time.perf_counter()
                if long_copy.exists():
                    shutil.rmtree(long_copy)
                cleanup_seconds = seconds(time.perf_counter() - cleanup_started)
        wall_seconds = seconds(time.perf_counter() - case_started)
        command_wall = seconds(sum(p['wall_seconds'] for p in phases))
        results[-1]['observed_cost'] = {
            'phases': phases, 'execution_count': len(phases),
            'phase_counts': {name: sum(p['phase'] == name for p in phases)
                             for name in ('full_audit', 'trace_candidate', 'policy_candidate', 'confirmation')},
            'command_seconds': seconds(sum(p['seconds'] for p in phases)),
            'command_wall_seconds': command_wall,
            'selection_seconds': selection_wall_seconds, 'setup_seconds': setup_seconds,
            'cleanup_seconds': cleanup_seconds,
            'overhead_seconds': max(0.0, wall_seconds - command_wall - selection_wall_seconds
                                    - setup_seconds - cleanup_seconds),
            'wall_seconds': wall_seconds}
    suspended = any(r['omitted_relevant'] for r in results)
    return {'qualification': 'SUSPENDED_MISSED_RELEVANT_TEST' if suspended else 'NOT_QUALIFIED_LOCAL_TRACE_ONLY',
            'mutations': results, 'capture_seconds': capture_seconds,
            'median_full_seconds': __import__('statistics').median(r['full_seconds'] for r in results),
            'median_candidate_ratio': (__import__('statistics').median(
                (r['selection_seconds'] + r['selected_seconds']) / r['full_seconds'] for r in results)
                if all(r['full_seconds'] > 0 for r in results) else None),
            'target_ratio': 0.30, 'correctness_preserved': not suspended and all(r['full_failures'] for r in results),
            'policy_correctness_preserved': (not any(r['policy_omitted'] for r in results)
                                             and all(r['full_failures'] for r in results))}

def recommend_tests(changed: list[str], impact: dict, *, mandatory: list[str] | None = None,
                    config_or_environment_changed: bool = False) -> dict:
    """Static hints: complete:true is caller text, not measured coverage."""
    impact = impact if isinstance(impact, dict) else {}
    refs = impact.get('tests', {})
    reasons = {t: ['mandatory'] for t in mandatory or []}
    for path in changed:
        for t in refs.get(path, []) if isinstance(refs, dict) else []:
            reasons.setdefault(t, []).append('static reference from ' + path)
    all_tests = impact.get('all_tests', [])
    return {'mode': 'FULL_SUITE', 'tests': sorted(set(all_tests) | set(reasons)),
            'reasons': reasons, 'unproven_blocks': sorted(changed),
            'qualification': 'NOT_QUALIFIED_STATIC_REFERENCE',
            'fallback_reasons': ['complete:true is not observed or externally attested',
                                 'execute full suite: python -m pytest tests tools/tests'],
            'command': [sys.executable, '-m', 'pytest', 'tests', 'tools/tests', *sorted(set(mandatory or []))]}
