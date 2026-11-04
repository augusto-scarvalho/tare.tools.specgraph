"""Deterministic X5/C5 subject binding; never authorizes selective execution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re

SCHEMA = 'specgraph-x5-scope/1'
_SHA = re.compile(r'[0-9a-f]{64}')


def _text(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ValueError('Expected a bounded nonempty string')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Control characters are not allowed')
    return value


def _path(value):
    value = _text(value).replace(chr(92), '/')
    if value.startswith('/') or ':' in value or '..' in value.split('/'):
        raise ValueError('Expected a repository-relative path without traversal')
    normalized = PurePosixPath(value).as_posix()
    if normalized == '.':
        raise ValueError('Expected a file path')
    return normalized


def _block(value):
    path, separator, line = _text(value).rpartition(':')
    if not separator or not re.fullmatch(r'[0-9]+', line):
        raise ValueError('A positive source line is required')
    number = int(line)
    if number < 1:
        raise ValueError('A positive source line is required')
    return _path(path), number


def _nodeid(value):
    value = _text(value)
    path, separator, name = value.partition('::')
    if not separator or not name or _path(path) != path:
        raise ValueError('Expected a canonical pytest nodeid')
    return value


def _json(value):
    # Reject Python-only JSON coercions, including non-string object keys.
    if value is None or type(value) in (str, int, float, bool):
        return
    if type(value) is list:
        for item in value:
            _json(item)
    elif type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json(item)
    else:
        raise ValueError('Capture must contain JSON values only')


def _build(root, changed, receipt, selected, mandatory):
    from .test_selection import SCHEMA as CAPTURE_SCHEMA, _files

    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Project root must be a directory')
    if not isinstance(receipt, dict) or receipt.get('schema') != CAPTURE_SCHEMA:
        raise ValueError('Invalid capture schema')
    _json(receipt)
    encoded = json.dumps(receipt, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
    if type(receipt.get('suite_returncode')) is not int or receipt['suite_returncode'] != 0:
        raise ValueError('A passing capture is required')
    tests = receipt.get('tests')
    if not isinstance(tests, list) or not tests:
        raise ValueError('Capture inventory is required')
    inventory = [_nodeid(test) for test in tests]
    if len(set(inventory)) != len(inventory):
        raise ValueError('Duplicate capture inventory')
    argv = receipt.get('pytest_args')
    if not isinstance(argv, list) or not argv or len(argv) > 128:
        raise ValueError('Expected bounded pytest argv')
    argv = [_text(arg) for arg in argv]
    before = receipt.get('fingerprints')
    if not isinstance(before, dict) or not before or len(before) > 4096:
        raise ValueError('Capture fingerprints are required')
    for path, digest in before.items():
        if _path(path) != path or not isinstance(digest, str) or not _SHA.fullmatch(digest):
            raise ValueError('Invalid canonical fingerprint')
    edges = receipt.get('executed_blocks')
    if not isinstance(edges, dict) or set(edges) != set(inventory):
        raise ValueError('Capture observations must match inventory')
    for blocks in edges.values():
        if not isinstance(blocks, list):
            raise ValueError('Malformed captured blocks')
        for block in blocks:
            path, _ = _block(block)
            if path not in before:
                raise ValueError('Captured block has no fingerprint')
    if not isinstance(changed, list) or not 1 <= len(changed) <= 256:
        raise ValueError('Expected between 1 and 256 changed blocks')
    changes = sorted(set(_block(block) for block in changed))
    current = _files(root)
    if set(current) != set(before):
        raise ValueError('Added or removed files cannot be bound')
    changed_paths = {path for path, _ in changes}
    if any(current[path] != before[path] and path not in changed_paths for path in before):
        raise ValueError('Unlisted file changed')
    for path in current:
        if not (root / path).resolve(strict=True).is_relative_to(root):
            raise ValueError('Fingerprint escapes project root')
    for path, line in changes:
        if path not in current:
            raise ValueError('Changed file is absent from fingerprints')
        if line > len((root / path).read_bytes().splitlines()):
            raise ValueError('Changed line is outside the current file')
    selections = []
    for values in (selected, mandatory):
        if not isinstance(values, list):
            raise ValueError('Selections must be lists of known nodeids')
        normalized = sorted(set(_nodeid(value) for value in values))
        if not set(normalized) <= set(inventory):
            raise ValueError('Unknown selected or mandatory nodeid')
        selections.append(normalized)
    selected, mandatory = selections
    if not set(mandatory) <= set(selected):
        raise ValueError('Mandatory tests must be selected')
    return {'schema': SCHEMA, 'project': root.as_posix(),
            'capture_sha256': hashlib.sha256(encoded).hexdigest(),
            'target_fingerprints': dict(sorted(current.items())),
            'changed': [{'path': path, 'line': line, 'before_sha256': before[path],
                         'after_sha256': current[path]} for path, line in changes],
            'pytest_args': argv, 'tests': sorted(inventory),
            'selected': selected, 'mandatory': mandatory}


def build_x5_scope(root, changed, receipt, selected, mandatory) -> dict:
    """Bind local capture and current files without granting execution authority.

    Inputs and project bytes are never modified. Invalid inputs or unreadable
    project files fail closed as ValueError, not as execution evidence.
    """
    try:
        return _build(root, changed, receipt, selected, mandatory)
    except (OSError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError) as exc:
        raise ValueError('Invalid X5 scope: ' + str(exc)) from exc
