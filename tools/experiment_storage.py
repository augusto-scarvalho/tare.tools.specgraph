"""Budget, consolidate and retire explicitly registered experiment lots.

No age-based deletion, no filesystem quota claim, no deletion of unregistered
directories. Every retirement verifies the complete source against a local
content-addressed archive. Cloud-only eviction requires a verified round trip.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import time
import uuid

GIB = 1024 ** 3
SCHEMA = 'specgraph/experiment-storage/1'


class StorageError(ValueError):
    pass


def wire(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('xb') as f:
            f.write(wire(value)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def plain_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink() or getattr(part, 'is_junction', lambda: False)():
            raise StorageError('linked directory is not an owned storage path: ' + str(part))
    return path


def name_ok(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
        raise StorageError('lot ID must be 1-64 lowercase letters, digits, underscores or hyphens')


def relative(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')) or '\\' in name or ':' in name:
        raise StorageError('unsafe archive path: ' + name)
    return p


def tree(root, relocated_from=None):
    """Inventory bytes and metadata without following symlinks or junctions."""
    root = plain_path(root)
    if not root.is_dir():
        raise StorageError('source directory is missing')
    entries = {}
    def walk(directory):
        for p in sorted(directory.iterdir()):
            name = p.relative_to(root).as_posix(); relative(name)
            s = p.lstat()
            row = {'mode': stat.S_IMODE(s.st_mode), 'mtime_ns': s.st_mtime_ns}
            if getattr(p, 'is_junction', lambda: False)():
                raise StorageError('junctions require explicit migration: ' + name)
            if stat.S_ISLNK(s.st_mode):
                target = p.resolve()
                if relocated_from is not None and target.is_relative_to(relocated_from):
                    target = root / target.relative_to(relocated_from)
                if not target.is_relative_to(root):
                    raise StorageError('external symlink requires explicit migration: ' + name)
                row.update(kind='link', target=os.readlink(p), portable_target=os.path.relpath(target, p.parent).replace(os.sep, '/'))
            elif stat.S_ISDIR(s.st_mode):
                row['kind'] = 'dir'; entries[name] = row; walk(p); continue
            elif stat.S_ISREG(s.st_mode):
                if p.name == '.git' or name.endswith('/.git/objects/info/alternates'):
                    raise StorageError('linked Git history requires separate consolidation: ' + name)
                row.update(kind='file', bytes=s.st_size, sha256=sha_file(p))
                after = p.stat()
                if (s.st_size, s.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise StorageError('file changed during inventory: ' + name)
            else:
                raise StorageError('special file cannot be consolidated: ' + name)
            entries[name] = row
    walk(root)
    return entries


def footprint(root):
    total = 0
    for base, dirs, files in os.walk(root):
        for name in dirs[:]:
            p = Path(base) / name
            if p.is_symlink() or getattr(p, 'is_junction', lambda: False)():
                dirs.remove(name)
        for name in files:
            p = Path(base) / name
            try:
                if not p.is_symlink():
                    total += p.stat().st_size
            except FileNotFoundError:
                pass  # Writer may rename a transient file; the next check observes it.
    return total


def quiescent(root):
    """Check local processes in addition to the controller's persistent state."""
    root = str(Path(root).resolve())
    def inside(value):
        value = os.path.normcase(value.removesuffix(' (deleted)'))
        prefix = os.path.normcase(root)
        return value == prefix or value.startswith(prefix + os.sep)
    if os.name == 'nt':
        try:
            import psutil
        except ImportError as exc:
            raise StorageError('Windows retirement requires psutil for process checks') from exc
        for proc in psutil.process_iter(['pid', 'cwd', 'exe']):
            if proc.pid == os.getpid():
                continue
            if any(inside(proc.info.get(k) or '') for k in ('cwd', 'exe')):
                raise StorageError('directory is in use by PID ' + str(proc.pid))
            # open_files() over every Windows process can stall on system handles.
            # Managed descendants are contained by WindowsJob; this additional
            # check detects external cwd/exe users without an unbounded handle scan.
    elif Path('/proc').is_dir():
        for proc in Path('/proc').iterdir():
            if not proc.name.isdecimal() or int(proc.name) == os.getpid():
                continue
            links = [proc / 'cwd', proc / 'exe']
            try:
                links.extend((proc / 'fd').iterdir())
            except (PermissionError, FileNotFoundError):
                pass
            for link in links:
                try:
                    if inside(os.readlink(link)):
                        raise StorageError('directory is in use by PID ' + proc.name)
                except (PermissionError, FileNotFoundError, OSError):
                    pass


def remove_owned_tree(root):
    def readonly_retry(function, name, error):
        p = Path(name)
        if not isinstance(error[1], PermissionError) or p.is_symlink() or not p.is_file():
            raise error[1]
        s = p.stat()
        if s.st_mode & stat.S_IWRITE:
            raise error[1]
        os.chmod(p, s.st_mode | stat.S_IWRITE)
        function(name)
    shutil.rmtree(root, onerror=readonly_retry)


def verify_archive(path):
    """Read every stored blob and validate the manifest, without extraction."""
    with tarfile.open(path, 'r:gz') as archive:
        members = archive.getmembers()
        names = [m.name for m in members]
        if len(names) != len(set(names)) or any(not m.isfile() for m in members):
            raise StorageError('archive has duplicate or non-file members')
        try:
            raw = archive.extractfile('manifest.json').read()
            manifest = json.loads(raw)
        except (KeyError, ValueError, AttributeError) as exc:
            raise StorageError('archive manifest missing or malformed') from exc
        if manifest.get('schema') != SCHEMA:
            raise StorageError('archive schema mismatch')
        expected = {}
        for name, row in manifest['entries'].items():
            relative(name)
            for parent in PurePosixPath(name).parents:
                if str(parent) != '.' and manifest['entries'].get(str(parent), {}).get('kind') != 'dir':
                    raise StorageError('archive path has a non-directory parent')
            if row['kind'] == 'file':
                digest = row['sha256']
                if not re.fullmatch('[0-9a-f]{64}', digest) or type(row['bytes']) is not int or row['bytes'] < 0:
                    raise StorageError('invalid blob identity')
                if digest in expected and expected[digest] != row['bytes']:
                    raise StorageError('inconsistent blob length')
                expected[digest] = row['bytes']
            elif row['kind'] not in ('dir', 'link'):
                raise StorageError('invalid entry kind')
        if set(names) != {'manifest.json', *('blobs/' + h for h in expected)}:
            raise StorageError('archive has missing or extra blobs')
        for digest, size in expected.items():
            member = archive.getmember('blobs/' + digest)
            if member.size != size:
                raise StorageError('blob length mismatch')
            h = hashlib.sha256()
            with archive.extractfile(member) as f:
                for block in iter(lambda: f.read(1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != digest:
                raise StorageError('blob hash mismatch')
    return manifest


def restore_archive(path, destination):
    manifest = verify_archive(path)
    destination = plain_path(destination)
    if destination.exists():
        raise StorageError('restore destination must not exist')
    # Validate portable links before creating anything.
    for name, row in manifest['entries'].items():
        if row['kind'] == 'link':
            target = row['portable_target']
            if Path(target).is_absolute() or not (destination / name).parent.joinpath(target).resolve().is_relative_to(destination):
                raise StorageError('restored link would escape destination')
    destination.mkdir(parents=True)
    with tarfile.open(path, 'r:gz') as archive:
        for name, row in sorted(manifest['entries'].items()):
            p = destination.joinpath(*relative(name).parts)
            if row['kind'] == 'dir':
                p.mkdir(parents=True, exist_ok=True)
            elif row['kind'] == 'file':
                p.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile('blobs/' + row['sha256']) as src, p.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
                os.chmod(p, row['mode']); os.utime(p, ns=(row['mtime_ns'], row['mtime_ns']))
        for name, row in manifest['entries'].items():
            if row['kind'] == 'link':
                p = destination / name
                os.symlink(row['portable_target'], p, target_is_directory=(p.parent / row['portable_target']).is_dir())
        for name, row in sorted(manifest['entries'].items(), reverse=True):
            if row['kind'] == 'dir':
                os.chmod(destination / name, row['mode'])
                os.utime(destination / name, ns=(row['mtime_ns'], row['mtime_ns']))
    metadata_changes = []
    for name, row in manifest['entries'].items():
        if row['kind'] != 'link':
            actual = (destination / name).stat()
            if actual.st_mtime_ns != row['mtime_ns'] or stat.S_IMODE(actual.st_mode) != row['mode']:
                metadata_changes.append(name)
    return {'restored': str(destination), 'entries': len(manifest['entries']),
            'filesystem_metadata_differences': metadata_changes,
            'metadata_policy': 'Original mode and nanosecond timestamp remain in manifest; destination filesystem may round them'}


class Store:
    def __init__(self, root):
        self.root = plain_path(root)
        self.control = self.root / 'experiment-storage'
        self.registry = self.control / 'registry.json'

    @contextmanager
    def locked(self):
        plain_path(self.control)
        self.control.mkdir(parents=True, exist_ok=True)
        lock = self.control / 'lock'
        try:
            lock.mkdir()
        except FileExistsError as exc:
            raise StorageError('storage operation already active; inspect lock owner before recovery') from exc
        try:
            atomic_json(lock / 'owner.json', {'pid': os.getpid(), 'platform': os.name, 'created': time.time()})
            yield
        finally:
            (lock / 'owner.json').unlink(missing_ok=True); lock.rmdir()

    def load(self):
        d = json.loads(self.registry.read_text())
        if d.get('schema') != SCHEMA:
            raise StorageError('unknown registry schema')
        return d

    def save(self, d):
        atomic_json(self.registry, d)

    def initialize(self, capacity=8 * GIB, minimum=10 * GIB):
        if type(capacity) is not int or capacity <= 0 or type(minimum) is not int or minimum < 0:
            raise StorageError('invalid disk policy')
        with self.locked():
            if self.registry.exists():
                raise StorageError('storage already initialized; edit the explicit policy to change limits')
            plain_path(self.root / 'experiments').mkdir(exist_ok=True)
            plain_path(self.root / 'evidence-store').mkdir(exist_ok=True)
            self.save({'schema': SCHEMA, 'capacity_bytes': capacity, 'minimum_free_bytes': minimum,
                       'legacy_allowlist': [], 'lots': {}})
        return self.status()

    def path(self, row):
        if row.get('legacy_source'):
            d = self.load()
            if row['legacy_source'] not in d['legacy_allowlist']:
                raise StorageError('legacy source no longer explicitly authorized')
            p = plain_path(row['legacy_source'])
            if p == self.root or self.root.is_relative_to(p) or p == Path(p.anchor):
                raise StorageError('invalid legacy scope')
            return p
        name_ok(row['id'])
        p = plain_path(self.root / 'experiments' / row['id'])
        if p.exists() and json.loads((p / '.experiment.json').read_text()) != {'id': row['id'], 'token': row['token']}:
            raise StorageError('lot ownership marker mismatch')
        return p

    def usage(self, d):
        sizes = {k: footprint(self.path(r)) for k, r in d['lots'].items() if r['state'] != 'RETIRED'}
        archives = footprint(self.root / 'evidence-store')
        committed = sum(max(d['lots'][k]['reservation_bytes'], n) for k, n in sizes.items()) + archives
        pending = sum(max(0, d['lots'][k]['reservation_bytes'] - n) for k, n in sizes.items())
        return sizes, archives, committed, pending

    def check_budget(self, d, extra=0):
        sizes, archives, committed, pending = self.usage(d)
        free = shutil.disk_usage(self.root).free
        if committed + extra > d['capacity_bytes']:
            raise StorageError('experiment capacity exhausted')
        if free - pending - extra < d['minimum_free_bytes']:
            raise StorageError('minimum free space would be violated')
        return sizes

    def status(self):
        d = self.load(); sizes, archives, committed, pending = self.usage(d)
        return {'root': str(self.root), 'capacity_bytes': d['capacity_bytes'], 'minimum_free_bytes': d['minimum_free_bytes'],
                'free_bytes': shutil.disk_usage(self.root).free, 'reserved_and_resident_bytes': committed,
                'archive_bytes': archives, 'pending_growth_bytes': pending,
                'lots': [{**r, 'resident_bytes': sizes.get(k, 0)} for k, r in d['lots'].items()],
                'unregistered_directories': 'Excluded from automatic deletion; still counted by filesystem free space',
                'quota': 'Admission reservation and 1-second run monitoring, not an OS hard quota'}

    def create(self, identity, budget, legacy_source=None):
        name_ok(identity)
        if type(budget) is not int or budget <= 0:
            raise StorageError('positive lot reservation required')
        with self.locked():
            d = self.load()
            if identity in d['lots']:
                raise StorageError('lot ID already registered')
            self.check_budget(d, budget)
            row = {'id': identity, 'token': uuid.uuid4().hex, 'reservation_bytes': budget, 'state': 'OPEN',
                   'created_at': time.time(), 'executions': []}
            if legacy_source:
                source = str(plain_path(legacy_source))
                if source not in d['legacy_allowlist']:
                    raise StorageError('legacy source must be explicitly allowlisted before enrollment')
                row.update(legacy_source=source, state='FINISHED')
                p = self.path(row); quiescent(p)
                if footprint(p) > budget:
                    raise StorageError('legacy data exceeds reservation')
            else:
                p = self.root / 'experiments' / identity
                if p.exists():
                    raise StorageError('lot directory already exists')
                p.mkdir()
                atomic_json(p / '.experiment.json', {'id': identity, 'token': row['token']})
            d['lots'][identity] = row; self.save(d)
        return row

    def execute(self, identity, argv, timeout):
        from pilot_process import run_process
        if not argv or (timeout is not None and
                        (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0)):
            raise StorageError('command and positive timeout or None required')
        with self.locked():
            d = self.load(); row = d['lots'][identity]
            if row['state'] not in ('OPEN', 'FINISHED') or row.get('legacy_source'):
                raise StorageError('lot cannot be executed in this state')
            self.check_budget(d); p = self.path(row)
            row['state'] = 'RUNNING'; self.save(d)
        env = os.environ.copy()
        for k, rel in {'TARE_SPECGRAPH_STATE_DIR': 'state/specgraph', 'TMPDIR': 'tmp', 'TEMP': 'tmp', 'TMP': 'tmp',
                       'XDG_CACHE_HOME': 'cache', 'UV_CACHE_DIR': 'cache/uv', 'PIP_CACHE_DIR': 'cache/pip',
                       'CARGO_TARGET_DIR': 'build'}.items():
            target = p / rel; target.mkdir(parents=True, exist_ok=True); env[k] = str(target)
        env.update(TARE_EXPERIMENT_RUN_DIR=str(p), PYTHONDONTWRITEBYTECODE='1')
        argv = [a.replace('{run}', str(p)) for a in argv]
        def guard():
            current = self.load()
            sizes = self.check_budget(current)
            if sizes[identity] > row['reservation_bytes']:
                raise StorageError('lot exceeded its disk reservation')
        try:
            result = run_process(argv, cwd=p, dest=p / 'executions' / str(len(row['executions']) + 1),
                                 timeout=timeout, env=env, resource_guard=guard, supervise_tree=True)
            # Final check catches a writer that exits before the next polling tick.
            try:
                guard()
            except StorageError as exc:
                result['resource_error'] = str(exc)
        except BaseException:
            # A crashed or interrupted controller never silently marks the lot quiescent.
            raise
        with self.locked():
            d = self.load(); row = d['lots'][identity]
            row['executions'].append(result)
            row['state'] = 'FINISHED' if result['cleanup_verified'] else 'NEEDS_REVIEW'
            self.save(d)
        return result

    def consolidate(self, identity, reason):
        if not reason.strip():
            raise StorageError('consolidation reason required')
        with self.locked():
            d = self.load(); row = d['lots'][identity]
            if row['state'] != 'FINISHED':
                raise StorageError('only a finished lot can be consolidated')
            p = self.path(row); quiescent(p); entries = tree(p)
            total = sum(r.get('bytes', 0) for r in entries.values())
            self.check_budget(d, total + len(wire(entries)) * 2 + 1024 * 1024)
            manifest = {'schema': SCHEMA, 'id': identity, 'reason': reason, 'source': str(p), 'entries': entries}
            manifest_hash = hashlib.sha256(wire(manifest)).hexdigest()
            dest = plain_path(self.root / 'evidence-store' / (manifest_hash + '.tar.gz'))
            tmp = dest.with_suffix('.partial')
            if tmp.exists():
                raise StorageError('unfinished archive requires inspection')
            try:
                with tarfile.open(tmp, 'w:gz', compresslevel=6) as archive:
                    body = wire(manifest); info = tarfile.TarInfo('manifest.json'); info.size = len(body)
                    archive.addfile(info, io.BytesIO(body)); done = set()
                    for name, item in entries.items():
                        if item['kind'] != 'file' or item['sha256'] in done:
                            continue
                        info = tarfile.TarInfo('blobs/' + item['sha256']); info.size = item['bytes']
                        with (p / name).open('rb') as f:
                            archive.addfile(info, f)
                        done.add(item['sha256'])
                if verify_archive(tmp) != manifest or tree(p) != entries:
                    raise StorageError('source changed during consolidation')
                quiescent(p)
                os.replace(tmp, dest)
            finally:
                tmp.unlink(missing_ok=True)
            row.update(state='CONSOLIDATED', archive={'file': dest.name, 'sha256': sha_file(dest),
                       'bytes': dest.stat().st_size, 'manifest_sha256': manifest_hash}, reason=reason)
            self.save(d)
        return row

    def checked_archive(self, row):
        name = row['archive']['file']
        if not re.fullmatch('[0-9a-f]{64}\\.tar\\.gz', name):
            raise StorageError('invalid archive name')
        path = plain_path(self.root / 'evidence-store' / name)
        if not path.is_file() or sha_file(path) != row['archive']['sha256']:
            raise StorageError('consolidated archive is missing or changed')
        manifest = verify_archive(path)
        if hashlib.sha256(wire(manifest)).hexdigest() != row['archive']['manifest_sha256']:
            raise StorageError('manifest identity mismatch')
        return path, manifest

    def collect(self, identity=None, apply=False):
        """Delete only fully consolidated unchanged lots, never by age or name."""
        result = []
        with self.locked():
            d = self.load()
            ids = [identity] if identity else list(d['lots'])
            for key in ids:
                row = d['lots'][key]
                if row['state'] != 'CONSOLIDATED':
                    result.append({'id': key, 'action': 'KEEP', 'reason': row['state']}); continue
                try:
                    _, manifest = self.checked_archive(row)
                    p = self.path(row); quiescent(p)
                    if tree(p) != manifest['entries']:
                        raise StorageError('local contents changed after consolidation')
                    if apply:
                        free_before = shutil.disk_usage(p.parent).free
                        # Atomic rename keeps a replaced source path out of this deletion.
                        tomb = p.with_name(p.name + '.retiring-' + row['token'])
                        if tomb.exists():
                            raise StorageError('unfinished retirement requires inspection')
                        os.rename(p, tomb)
                        try:
                            if tree(tomb, relocated_from=p) != manifest['entries']:
                                raise StorageError('source changed during retirement')
                            quiescent(tomb)
                        except BaseException:
                            os.rename(tomb, p); raise
                        row.update(state='RETIRING', retirement_path=str(tomb)); self.save(d)
                        remove_owned_tree(tomb)
                        row.update(state='RETIRED', retired_at=time.time(),
                                   filesystem_free_delta=shutil.disk_usage(p.parent).free - free_before)
                        self.save(d)
                    result.append({'id': key, 'action': 'RETIRED' if apply else 'WOULD_RETIRE'})
                except (StorageError, OSError) as exc:
                    result.append({'id': key, 'action': 'KEEP', 'reason': str(exc)})
        return result

    def verify_remote(self, identity, downloaded, file_id, url):
        if not file_id or not url.startswith('https://drive.google.com/'):
            raise StorageError('observed Drive ID and URL required')
        with self.locked():
            d = self.load(); row = d['lots'][identity]
            local, _ = self.checked_archive(row)
            downloaded = plain_path(downloaded)
            if downloaded == local or sha_file(downloaded) != row['archive']['sha256']:
                raise StorageError('Drive round-trip content does not match local archive')
            verify_archive(downloaded)
            row['drive'] = {'id': file_id, 'url': url, 'sha256': row['archive']['sha256'],
                            'verified_at': time.time(), 'method': 'download-and-sha256-plus-archive-validation'}
            self.save(d)
        return row['drive']

    def evict_archive(self, identity):
        with self.locked():
            d = self.load(); row = d['lots'][identity]
            if row['state'] != 'RETIRED' or row.get('drive', {}).get('sha256') != row.get('archive', {}).get('sha256'):
                raise StorageError('retired lot and verified Drive round trip required before eviction')
            path, _ = self.checked_archive(row); path.unlink()
            row['archive']['evicted_at'] = time.time(); self.save(d)
        return {'id': identity, 'local_archive_evicted': True, 'drive': row['drive']}


def timeout_value(value):
    if value.lower() in ('none', 'unlimited'):
        return None
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError()
        return seconds
    except ValueError:
        raise argparse.ArgumentTypeError('use positive seconds or unlimited') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('init'); p.add_argument('--capacity-gib', type=float, default=8); p.add_argument('--minimum-free-gib', type=float, default=10)
    sub.add_parser('status')
    p = sub.add_parser('create'); p.add_argument('id'); p.add_argument('--reserve-gib', type=float, required=True); p.add_argument('--legacy-source', type=Path)
    p = sub.add_parser('exec'); p.add_argument('id'); p.add_argument('--timeout', type=timeout_value, default=None,
        help='Optional wall-clock ceiling in seconds; default unlimited'); p.add_argument('command', nargs=argparse.REMAINDER)
    p = sub.add_parser('consolidate'); p.add_argument('id'); p.add_argument('--reason', required=True); p.add_argument('--keep-workspace', action='store_true')
    p = sub.add_parser('gc'); p.add_argument('--id'); p.add_argument('--apply', action='store_true')
    p = sub.add_parser('verify-remote'); p.add_argument('id'); p.add_argument('--downloaded', type=Path, required=True); p.add_argument('--file-id', required=True); p.add_argument('--url', required=True)
    p = sub.add_parser('evict'); p.add_argument('id')
    p = sub.add_parser('restore'); p.add_argument('--archive', type=Path, required=True); p.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(); store = Store(args.root)
    try:
        if args.action == 'init':
            result = store.initialize(int(args.capacity_gib * GIB), int(args.minimum_free_gib * GIB))
        elif args.action == 'status': result = store.status()
        elif args.action == 'create':
            store.collect(apply=True)
            result = store.create(args.id, int(args.reserve_gib * GIB), args.legacy_source)
        elif args.action == 'exec':
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            result = store.execute(args.id, command, args.timeout)
        elif args.action == 'consolidate':
            result = store.consolidate(args.id, args.reason)
            if not args.keep_workspace: result = {'lot': result, 'gc': store.collect(args.id, apply=True)}
        elif args.action == 'gc': result = store.collect(args.id, args.apply)
        elif args.action == 'verify-remote': result = store.verify_remote(args.id, args.downloaded, args.file_id, args.url)
        elif args.action == 'evict': result = store.evict_archive(args.id)
        else: result = restore_archive(args.archive, args.destination)
        print(json.dumps(result, indent=2))
        if args.action == 'exec' and (result.get('exit_code') != 0 or result.get('resource_error') or not result.get('cleanup_verified')):
            return 1
    except (StorageError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'status': 'ERROR', 'error': str(exc)})); return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
