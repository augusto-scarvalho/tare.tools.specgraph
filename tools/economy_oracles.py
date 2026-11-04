"""Pre-registered behavior and boundary checks for three new SDK defects."""
from __future__ import annotations
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

TASKS = ('yaml-root-types', 'test-path-exclusions', 'report-budget-types')
SELECTIONS = {
    TASKS[0]: ('src/specgraph/config.py', 'SpecGraphConfig.load'),
    TASKS[1]: ('src/specgraph/test_parser.py', 'TestParser.discover_tests'),
    TASKS[2]: ('src/specgraph/report.py', 'report_html'),
}


def evaluate(candidate: Path, task: str) -> dict:
    if task not in TASKS:
        raise ValueError('unknown task')
    name = 'specgraph._economy_candidate'
    spec = importlib.util.spec_from_file_location(name, candidate)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    rows = []

    def check(label, predicate, *, kind='preservation'):
        try:
            rows.append({'check': label, 'kind': kind, 'passed': bool(predicate())})
        except Exception as exc:
            rows.append({'check': label, 'kind': kind, 'passed': False, 'error': type(exc).__name__})

    with tempfile.TemporaryDirectory(prefix='sg-economy-') as temp:
        root = Path(temp) / 'repo'; root.mkdir()
        if task == TASKS[0]:
            path = root / 'specgraph.yaml'
            for body in ('[]', 'false', '0', '""', '[src]', 'true', '13', 'text', '[unterminated'):
                path.write_text(body, encoding='utf-8')
                result = module.SpecGraphConfig.load(root)
                check('reject-' + body, lambda: bool(result.config_errors) and result.strict_orphan_check is True,
                      kind='regression' if body in ('[]', 'false', '0', '""') else 'preservation')
                check('input-unchanged-' + body, lambda: path.read_text() == body)
            for body in ('', '# comment only\n', 'null', '~', '{}'):
                path.write_text(body, encoding='utf-8')
                check('default-' + repr(body), lambda: dataclasses.asdict(module.SpecGraphConfig.load(root))
                      == dataclasses.asdict(module.SpecGraphConfig()))
            body = ('project_name: explicit\nindexed_paths: []\ngoverned_paths: [lib]\n'
                    'test_paths: tests/custom\nstrict_orphan_check: false\nexcluded_paths: []\n')
            path.write_text(body)
            result = module.SpecGraphConfig.load(root)
            check('mapping-values-preserved', lambda: not result.config_errors
                  and result.project_name == 'explicit' and result.indexed_paths == []
                  and result.governed_paths == ['lib'] and result.test_paths == ['tests/custom']
                  and result.strict_orphan_check is False and result.excluded_paths == [])
            path.write_text('test_paths: [1]\nstrict_orphan_check: string\n')
            check('field-diagnostics-preserved', lambda: len(module.SpecGraphConfig.load(root).config_errors) == 2)
            path.unlink()
            check('absent-defaults', lambda: dataclasses.asdict(module.SpecGraphConfig.load(root))
                  == dataclasses.asdict(module.SpecGraphConfig()))
            (root / '.specgraph.yaml').write_text('project_name: hidden\n')
            check('alternate-file', lambda: module.SpecGraphConfig.load(root).project_name == 'hidden')
            path.write_text('project_name: primary\n')
            check('primary-precedence', lambda: module.SpecGraphConfig.load(root).project_name == 'primary')
        elif task == TASKS[1]:
            names = ('tests/test_keep.py', 'tests/generated/test_drop.py', 'tests/generated/deep/test_drop.py',
                     'tests/generated_backup/test_keep.py', 'tests/sub/fixtures/test_drop.py',
                     'tests/sub/fixtures_backup/test_keep.py')
            bodies = {}
            for relative in names:
                p = root / relative; p.parent.mkdir(parents=True, exist_ok=True)
                bodies[relative] = 'def test_one():\n    """@verifies SPEC-ONE:AC-01"""\n    pass\n'
                p.write_text(bodies[relative])
            cases = [([], list(names)), (['tests/generated'], [n for n in names if not n.startswith('tests/generated/')]),
                     (['/tests/generated/'], [n for n in names if not n.startswith('tests/generated/')]),
                     (['tests\\generated'], [n for n in names if not n.startswith('tests/generated/')]),
                     (['fixtures'], [n for n in names if '/fixtures/' not in n]),
                     (['tests/generated', 'fixtures'], [n for n in names if not n.startswith('tests/generated/') and '/fixtures/' not in n]),
                     (['tests/generated_backup'], [n for n in names if not n.startswith('tests/generated_backup/')]),
                     (['', '/'], list(names))]
            for exclusions, expected in cases:
                tests, errors = module.TestParser.discover_tests(['tests'], exclusions, root_dir=root)
                check('selection-' + repr(exclusions), lambda: not errors and [t.file_path for t in tests] == sorted(expected),
                      kind='regression' if any('/' in x or '\\' in x for x in exclusions if x not in ('', '/')) else 'preservation')
                check('metadata-' + repr(exclusions), lambda: all(t.test_id == t.file_path + ':test_one'
                      and t.name == 'test_one' and t.verified_specs == ['SPEC-ONE:AC-01']
                      and t.start_line == 1 and t.end_line == 3 for t in tests))
            tests, errors = module.TestParser.discover_tests(['tests/test_keep.py'], ['tests/generated'], root_dir=root)
            check('explicit-file-kept', lambda: not errors and len(tests) == 1 and tests[0].file_path == 'tests/test_keep.py')
            tests, errors = module.TestParser.discover_tests(['tests/generated/test_drop.py'], ['tests/generated'], root_dir=root)
            check('explicit-file-excluded', lambda: tests == [] and errors == [], kind='regression')
            tests, errors = module.TestParser.discover_tests(['../outside'], [], root_dir=root)
            check('outside-root-refused', lambda: tests == [] and bool(errors))
            broken = root / 'tests/generated/test_invalid.py'; broken.write_text('def bad(\n')
            tests, errors = module.TestParser.discover_tests(['tests'], ['tests/generated'], root_dir=root)
            check('excluded-file-not-parsed', lambda: not errors and all(not t.file_path.startswith('tests/generated/') for t in tests), kind='regression')
            check('inputs-preserved', lambda: all((root / n).read_text() == body for n, body in bodies.items()))
        else:
            bounds = {'max_nodes': (1, 1000), 'max_edges': (1, 5000), 'max_hops': (0, 10), 'max_bytes': (8192, 10000000)}
            class IndexReached(Exception):
                pass

            def rejects(kwargs):
                with patch.object(module, 'RepositoryIndex', side_effect=IndexReached):
                    try:
                        module.report_html(root, **kwargs)
                    except ValueError:
                        return True
                    except Exception:
                        return False
                return False

            def accepts(kwargs):
                with patch.object(module, 'RepositoryIndex', side_effect=IndexReached):
                    try:
                        module.report_html(root, **kwargs)
                    except IndexReached:
                        return True
                    except Exception:
                        return False
                return False

            for key, (lo, hi) in bounds.items():
                for value in (True, False, float(lo), lo + .5, str(lo), None, [], {}, float('nan'), float('inf')):
                    check(f'{key}-reject-{value!r}', lambda key=key, value=value: rejects({key: value}), kind='regression')
                for value in (lo - 1, hi + 1):
                    check(f'{key}-range-{value}', lambda key=key, value=value: rejects({key: value}))
                for value in (lo, hi):
                    check(f'{key}-valid-{value}', lambda key=key, value=value: accepts({key: value}))
            from specgraph.indexer import index_repository
            (root / 'src').mkdir()
            source = 'def leaf():\n    return 1\n\ndef caller():\n    return leaf()\n'
            (root / 'src/lib.py').write_bytes(source.encode())
            state = Path(temp) / 'state'
            meta = index_repository(root, state_dir=state)
            output = module.report_html(root, state_dir=state, max_bytes=65536, max_hops=0)
            marker = '<script type="application/json" id="snapshot">'
            data = json.loads(output.split(marker, 1)[1].split('</script>', 1)[0])
            check('export-identity', lambda: Path(data['root']).resolve() == root.resolve() and data['root'] == meta['repository_root'] and data['content_hash'] == meta['content_hash']
                  and data['revision_seq'] == meta['revision_seq'] and data['limits']['max_hops'] == 0)
            check('export-code-and-relations', lambda: {n['id'] for n in data['nodes']} == {'src/lib.py:leaf', 'src/lib.py:caller'}
                  and any(e['from'] == 'src/lib.py:caller' and e['to'] == 'src/lib.py:leaf' for e in data['edges'])
                  and all(n['source_snippet'] and n['sha256'] == hashlib.sha256(source.encode()).hexdigest() for n in data['nodes']))
            check('export-budget-and-source-unchanged', lambda: len(output.encode()) <= 65536 and (root / 'src/lib.py').read_text() == source)
    return {'task': task, 'passed': all(r['passed'] for r in rows), 'checks': rows}


def main(task, candidate):
    report = evaluate(Path(candidate), task)
    print(json.dumps(report, sort_keys=True))
    return 0 if report['passed'] else 1
