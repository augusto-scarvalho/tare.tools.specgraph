"""Source-exact edits inside one function, including its decorators and helpers."""
import ast
import copy


def target_node(tree, symbol):
    node = tree
    for part in symbol.split('.'):
        candidates = [n for n in node.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part]
        if len(candidates) != 1:
            raise ValueError('target declaration must be unique')
        node = candidates[0]
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        raise ValueError('target must be a function or method')
    return node


def span(node):
    return [min([node.lineno] + [n.lineno for n in node.decorator_list]), node.end_lineno]


def validate_candidate(source, candidate, task):
    before, after = ast.parse(source), ast.parse(candidate)
    old, new = target_node(before, task['symbol']), target_node(after, task['symbol'])
    if [old.lineno, old.end_lineno] != task['target_range']:
        raise ValueError('original target range changed')
    # Compare every AST field except the target body, including decorators,
    # signature and all surrounding declarations and executable statements.
    left, right = copy.deepcopy(before), copy.deepcopy(after)
    target_node(left, task['symbol']).body = [ast.Pass()]
    target_node(right, task['symbol']).body = [ast.Pass()]
    if ast.dump(left) != ast.dump(right):
        raise ValueError('edit changes code outside target body or public signature')
    old_lines, new_lines = source.decode().splitlines(keepends=True), candidate.decode().splitlines(keepends=True)
    lo, hi = span(old); new_lo, new_hi = span(new)
    if old_lines[:lo-1] != new_lines[:new_lo-1] or old_lines[hi:] != new_lines[new_hi:]:
        raise ValueError('edit changes text outside target declaration')
    return candidate


def checked_replacement(source, task, proposal):
    if not isinstance(proposal, dict) or set(proposal) != {'path', 'old', 'new'} or proposal['path'] != task['path']:
        raise ValueError('exact target path and replacement object required')
    old, new = proposal['old'], proposal['new']
    if not isinstance(old, str) or not isinstance(new, str) or not old or old == new or len(new.encode()) > 16384:
        raise ValueError('bounded nonempty replacement required')
    text = source.decode()
    if text.count(old) != 1:
        raise ValueError('replacement must match exactly once')
    tree = ast.parse(source); node = target_node(tree, task['symbol'])
    lines = text.splitlines(keepends=True); start, end = span(node)
    lo, hi = len(''.join(lines[:start-1])), len(''.join(lines[:end]))
    pos = text.index(old)
    if pos < lo or pos + len(old) > hi:
        raise ValueError('replacement escapes target declaration')
    return validate_candidate(source, text.replace(old, new, 1).encode(), task)
