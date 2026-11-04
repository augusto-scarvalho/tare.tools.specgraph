import ast
from pathlib import Path
import pytest
from tools.scoped_edit import checked_replacement, validate_candidate

SOURCE = b'class C:\n    @classmethod\n    def target(cls, x=1):\n        return x\n\n    def other(self):\n        return 2\n'
TASK = {'path': 'unit.py', 'symbol': 'C.target', 'target_range': [3, 4]}


def test_decorator_context_and_local_helper_are_accepted():
    old = '    @classmethod\n    def target(cls, x=1):\n        return x\n'
    new = old.replace('        return x', '        def local(value):\n            return value + 1\n        return local(x)')
    result = checked_replacement(SOURCE, TASK, {'path':'unit.py','old':old,'new':new})
    namespace = {}; exec(result, namespace)
    assert namespace['C'].target() == 2 and namespace['C']().other() == 2


@pytest.mark.parametrize('candidate', [
    SOURCE.replace(b'return 2', b'return 3'),
    SOURCE.replace(b'x=1', b'x=2'),
    SOURCE.replace(b'@classmethod', b'@staticmethod'),
    SOURCE.replace(b'return x', b'return x\n\ndef injected():\n    pass'),
    SOURCE.replace(b'def other', b'# hidden comment\n    def other'),
])
def test_outer_code_signatures_and_decorators_remain_protected(candidate):
    with pytest.raises(ValueError):
        validate_candidate(SOURCE, candidate, TASK)


def test_rejected_historical_proposals_now_pass_without_rewriting_them():
    import json
    root = Path(__file__).parents[1] / 'tests/fixtures/historical/economy'
    task = json.loads((root / 'frozen/prepared/test-path-exclusions/task.json').read_bytes())
    source = (root / 'frozen/prepared/test-path-exclusions/corpus' / task['path']).read_bytes()
    for n in (1, 2):
        proposal = json.loads((root / 'proposals.json').read_bytes())['answers'][str(n)]
        expected = (root / 'review' / f'r{n}-test-path-exclusions-full-proposal.py').read_bytes()
        assert checked_replacement(source, task, proposal | {'path':task['path']}) == expected
