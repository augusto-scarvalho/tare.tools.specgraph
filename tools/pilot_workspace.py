"""Independent executable exports, description-based context and orthogonal grading."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import time


def sha(data):
    return hashlib.sha256(data).hexdigest()


def relative_path(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or '..' in p.parts or '\\' in value or ':' in value or value != p.as_posix():
        raise ValueError(f'unsafe repository path: {value}')
    return p


def excluded(name, prefixes):
    return any(name == prefix.rstrip('/') or name.startswith(prefix.rstrip('/') + '/') for prefix in prefixes)


def inventory(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[name] = {'kind': 'symlink', 'target': str(path.readlink())}
        elif path.is_file():
            result[name] = {'kind': 'file', 'sha256': sha(path.read_bytes())}
    return result


def export_checkout(repo, commit, dest, exclude):
    if len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit):
        raise ValueError('full source commit required')
    for name in exclude:
        relative_path(name.rstrip('/'))
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=False)
    data = subprocess.run(['git', '-C', str(repo), 'archive', '--format=tar', commit], check=True, capture_output=True).stdout
    omitted = []
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            name = member.name.rstrip('/'); relative_path(name)
            if excluded(name, exclude):
                if not member.isdir(): omitted.append(name)
                continue
            path = dest / name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.extractfile(member).read())
                path.chmod(member.mode)
            else:
                raise ValueError(f'export requires explicit handling of links/submodules: {name}')
    return {'commit': commit, 'exported': inventory(dest), 'excluded_files': omitted,
            'excluded_prefixes': exclude, 'git_history_exposed': False}


def validate_trial_paths(output, task_id, names, cells, *, windows=None):
    """Reject long future paths before spending on the first model cell.

    A Windows Python build accepting long paths does not guarantee that the
    native hosts and their copy/edit tools do. Keep the full path within 240
    UTF-16 units, leaving room for a temporary sibling used during replacement.
    """
    if not (os.name == 'nt' if windows is None else windows):
        return
    for cell in cells:
        if cell['task'] != task_id:
            continue
        folder = Path(output) / f"r{cell['repeat']}-{cell['host']}-{task_id}-{cell['arm']}" / 'workspace'
        for name in names:
            path = str(folder / name)
            if len(path.encode('utf-16-le')) // 2 > 240:
                raise ValueError('Windows workspace path exceeds the 240-unit portable limit; '
                                 'choose a shorter output root before inference: ' + path)


def classify_changes(before, after, editable, auxiliary):
    # Prefix allowlists concern newly created artifacts only. Existing tracked
    # files never become exempt because their path happens to resemble a cache.
    for name in editable:
        relative_path(name)
    for name in auxiliary:
        relative_path(name.rstrip('/'))
    changed = [p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
    aux = [p for p in changed if p not in before and excluded(p, auxiliary) and after[p]['kind'] == 'file']
    outside = [p for p in changed if p not in editable and p not in aux]
    invalid = [p for p in changed if p in editable and (p not in after or after[p]['kind'] != 'file')]
    return {'changed': changed, 'auxiliary_files': aux, 'outside_scope': outside,
            'invalid_editable_files': invalid, 'scope_valid': not outside and not invalid}


def context_packet(root, state, description, context_files, *, max_bytes=32768, limit=5, success_criteria=''):
    """Select bounded initial context through the versioned implementation policy."""
    from pilot_selection import select_context
    return select_context(root, state, description, context_files, max_bytes=max_bytes, limit=limit,
                          success_criteria=success_criteria)


def full_packet(root, context_files):
    started = time.perf_counter(); chunks = []; total = 0
    for name in context_files:
        relative_path(name); body = (Path(root) / name).read_bytes(); total += len(body)
        chunks.append(f'File: {name}\nSHA256: {sha(body)}\n```\n'.encode() + body + b'\n```\n')
    packet = b'\n'.join(chunks)
    return packet, {'index_builds': 0, 'index_wall_ms': 0, 'query_wall_ms': 0, 'query_api_calls': 0,
        'context_total_wall_ms': (time.perf_counter() - started) * 1000, 'packet_source_reads': len(context_files),
        'packet_source_bytes': total, 'packet_bytes': len(packet), 'packet_sha256': sha(packet),
        'host_source_reads': None, 'fallback_deliveries': 0}
