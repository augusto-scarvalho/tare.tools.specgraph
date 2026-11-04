import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import experiment_storage as storage


@pytest.fixture
def store(tmp_path):
    s = storage.Store(tmp_path / 'storage')
    s.initialize(64 * 1024 * 1024, 0)
    return s


def finished(store, identity='trial', content=b'candidate with uncommitted edits'):
    row = store.create(identity, 4 * 1024 * 1024)
    p = store.path(row)
    (p / 'candidate.py').write_bytes(content)
    (p / 'empty-dir').mkdir()
    d = store.load(); d['lots'][identity]['state'] = 'FINISHED'; store.save(d)
    return p


def test_reservations_are_shared_and_not_preallocated(store):
    before = storage.footprint(store.root)
    store.create('a', 40 * 1024 * 1024)
    with pytest.raises(storage.StorageError, match='capacity'):
        store.create('b', 40 * 1024 * 1024)
    assert storage.footprint(store.root) - before < 4096


def test_free_space_accounts_for_unwritten_reservations(store, monkeypatch):
    store.create('a', 20 * 1024 * 1024)
    monkeypatch.setattr(storage.shutil, 'disk_usage', lambda _: shutil._ntuple_diskusage(100, 70, 30 * 1024 * 1024))
    with pytest.raises(storage.StorageError, match='free space'):
        store.create('b', 20 * 1024 * 1024)


def test_collection_preserves_unregistered_and_running_work(store):
    unknown = store.root / 'old-checkout'; unknown.mkdir(); (unknown / 'dirty').write_text('unique')
    row = store.create('active', 1024 * 1024)
    d = store.load(); d['lots']['active']['state'] = 'RUNNING'; store.save(d)
    assert store.collect(apply=True)[0]['action'] == 'KEEP'
    assert store.path(row).is_dir() and (unknown / 'dirty').read_text() == 'unique'


def test_consolidation_is_lossless_deduplicated_and_restorable(store, tmp_path):
    p = finished(store)
    (p / 'duplicate.py').write_bytes((p / 'candidate.py').read_bytes())
    original = storage.tree(p)
    row = store.consolidate('trial', 'completed offline replay')
    archive, manifest = store.checked_archive(row)
    with tarfile.open(archive) as t:
        blobs = [n for n in t.getnames() if n.startswith('blobs/')]
    assert len(blobs) < sum(v['kind'] == 'file' for v in original.values())
    assert store.collect('trial')[0]['action'] == 'WOULD_RETIRE'
    assert p.exists()
    assert store.collect('trial', apply=True)[0]['action'] == 'RETIRED'
    dest = tmp_path / 'restored'
    restored = storage.restore_archive(archive, dest)
    after = storage.tree(dest)
    assert {k: {f: v for f, v in r.items() if f != 'mtime_ns'} for k, r in after.items()} == {
        k: {f: v for f, v in r.items() if f != 'mtime_ns'} for k, r in original.items()}
    assert manifest['entries'] == original
    assert set(restored['filesystem_metadata_differences']) == {
        k for k in original if original[k]['mtime_ns'] != after[k]['mtime_ns']}
    assert not p.exists()


def test_new_or_changed_files_after_consolidation_block_deletion(store):
    p = finished(store); store.consolidate('trial', 'done')
    (p / 'new-work.txt').write_text('must survive')
    result = store.collect('trial', apply=True)
    assert result[0]['action'] == 'KEEP' and 'changed' in result[0]['reason']
    assert (p / 'new-work.txt').read_text() == 'must survive'


@pytest.mark.parametrize('damage', ['missing', 'corrupt'])
def test_archive_damage_blocks_deletion(store, damage):
    p = finished(store); row = store.consolidate('trial', 'done')
    archive, _ = store.checked_archive(row)
    if damage == 'missing': archive.unlink()
    else: archive.write_bytes(b'corrupt')
    assert store.collect('trial', apply=True)[0]['action'] == 'KEEP'
    assert p.exists()


def test_readonly_artifacts_are_preserved_before_retirement(store):
    p = finished(store)
    source = p / 'candidate.py'
    os.chmod(source, stat.S_IREAD)
    row = store.consolidate('trial', 'read-only source preserved')
    archive, manifest = store.checked_archive(row)
    assert manifest['entries']['candidate.py']['sha256'] == storage.sha_file(source)
    assert store.collect('trial', apply=True)[0]['action'] == 'RETIRED'
    assert not p.exists() and archive.exists()


def test_archive_directory_cannot_be_replaced_by_external_link(store, tmp_path):
    p = finished(store); outside = tmp_path / 'outside'; outside.mkdir()
    (store.root / 'evidence-store').rmdir()
    try:
        (store.root / 'evidence-store').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(storage.StorageError, match='linked directory'):
        store.consolidate('trial', 'must stay inside storage root')
    assert p.exists() and not list(outside.iterdir())


def test_external_link_cannot_authorize_source_deletion(store, tmp_path):
    p = finished(store); outside = tmp_path / 'unique.txt'; outside.write_text('keep')
    try:
        (p / 'link').symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    with pytest.raises(storage.StorageError, match='external symlink'):
        store.consolidate('trial', 'done')
    assert outside.read_text() == 'keep' and p.exists()


def test_internal_absolute_link_survives_relocation_and_restore(store, tmp_path):
    p = finished(store)
    try:
        (p / 'linked.py').symlink_to(p / 'candidate.py')
    except OSError:
        pytest.skip('symlinks unavailable')
    row = store.consolidate('trial', 'internal link')
    archive, _ = store.checked_archive(row)
    assert store.collect('trial', apply=True)[0]['action'] == 'RETIRED'
    dest = tmp_path / 'restored'; storage.restore_archive(archive, dest)
    assert (dest / 'linked.py').resolve() == dest / 'candidate.py'
    assert (dest / 'linked.py').read_bytes() == (dest / 'candidate.py').read_bytes()


def test_linked_git_checkout_is_not_treated_as_self_contained(store):
    p = finished(store); (p / '.git').write_text('gitdir: /elsewhere/.git/worktrees/test\n')
    with pytest.raises(storage.StorageError, match='linked Git'):
        store.consolidate('trial', 'done')


def test_legacy_deletion_requires_exact_enrollment_and_full_archive(store, tmp_path):
    old = tmp_path / 'old'; old.mkdir(); (old / 'receipt.json').write_text('{"passed":true}')
    with pytest.raises(storage.StorageError, match='allowlisted'):
        store.create('old', 1024 * 1024, old)
    d = store.load(); d['legacy_allowlist'] = [str(old)]; store.save(d)
    store.create('old', 1024 * 1024, old)
    store.consolidate('old', 'legacy replay already complete')
    assert store.collect('old', apply=True)[0]['action'] == 'RETIRED'
    assert not old.exists()


def test_storage_lock_does_not_guess_a_stale_owner(store):
    with store.locked():
        with pytest.raises(storage.StorageError, match='already active'):
            store.create('racing', 1024)


def test_cloud_eviction_requires_an_independent_matching_download(store, tmp_path):
    finished(store); row = store.consolidate('trial', 'done'); store.collect('trial', apply=True)
    archive, _ = store.checked_archive(row)
    with pytest.raises(storage.StorageError, match='verified Drive'):
        store.evict_archive('trial')
    with pytest.raises(storage.StorageError, match='round-trip'):
        store.verify_remote('trial', archive, 'id', 'https://drive.google.com/file/d/id/view')
    download = tmp_path / 'download.tar.gz'; download.write_bytes(b'bad download')
    with pytest.raises(storage.StorageError, match='round-trip'):
        store.verify_remote('trial', download, 'id', 'https://drive.google.com/file/d/id/view')
    shutil.copyfile(archive, download)
    store.verify_remote('trial', download, 'id', 'https://drive.google.com/file/d/id/view')
    store.evict_archive('trial')
    assert not archive.exists()
    storage.restore_archive(download, tmp_path / 'restored')
    assert (tmp_path / 'restored/candidate.py').is_file()


def test_archive_traversal_is_rejected_before_restore(tmp_path):
    path = tmp_path / 'bad.tar.gz'
    manifest = {'schema': storage.SCHEMA, 'entries': {'../outside': {'kind': 'dir'}}}
    raw = storage.wire(manifest)
    with tarfile.open(path, 'w:gz') as t:
        info = tarfile.TarInfo('manifest.json'); info.size = len(raw); t.addfile(info, io.BytesIO(raw))
    with pytest.raises(storage.StorageError, match='unsafe archive path'):
        storage.restore_archive(path, tmp_path / 'restore')
    assert not (tmp_path / 'restore').exists()


def test_active_process_prevents_consolidation(store):
    p = finished(store)
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], cwd=p)
    try:
        with pytest.raises(storage.StorageError, match='in use'):
            store.consolidate('trial', 'done')
    finally:
        child.kill(); child.wait()


def test_execution_routes_state_and_checks_final_fast_write(store):
    row = store.create('state', 1024 * 1024)
    command = [sys.executable, '-c',
               'import os,pathlib; p=pathlib.Path(os.environ["TARE_SPECGRAPH_STATE_DIR"]); '
               '(p/"cache").write_bytes(b"x"*2000000)']
    result = store.execute('state', command, 20)
    assert result['cleanup_verified']
    assert 'reservation' in result['resource_error']
    assert (store.path(row) / 'state/specgraph/cache').stat().st_size == 2000000


def test_guard_stops_long_running_disk_growth_and_cleans_processes(store):
    store.create('growth', 1024 * 1024)
    result = store.execute('growth', [sys.executable, '-c',
        'import pathlib,time; pathlib.Path("large").write_bytes(b"x"*2000000); time.sleep(30)'], 20)
    assert result['cleanup_verified'] and 'resource_error' in result
    assert result['wall_ms'] < 10000


@pytest.mark.parametrize('timeout', [20, None])
def test_timeout_policy_preserves_disk_guard_and_receipt(store, timeout):
    store.create('guarded', 1024 * 1024)
    result = store.execute('guarded', [sys.executable, '-c',
        'import pathlib,time; pathlib.Path("large").write_bytes(b"x"*2000000); time.sleep(30)'], timeout)
    assert result['cleanup_verified'] and 'resource_error' in result
    assert not result['timeout'] and result['timeout_seconds'] == timeout
    assert result['wall_ms'] < 10000
    assert store.load()['lots']['guarded']['executions'][0] == result


@pytest.mark.parametrize('guarded', [False, True])
def test_unlimited_process_waits_for_completion_and_cleans_children(tmp_path, guarded):
    from pilot_process import run_process
    checks = []
    child = 'import time;time.sleep(30)'
    command = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(1.2)'
    result = run_process([sys.executable, '-c', command], cwd=tmp_path,
        dest=tmp_path / 'receipt', timeout=None,
        resource_guard=(lambda: checks.append(True)) if guarded else None)
    assert result['exit_code'] == 0 and result['cleanup_verified']
    assert not result['timeout'] and result['timeout_seconds'] is None
    if guarded:
        assert len(checks) >= 2  # Monitoring continues beyond a polling interval.


@pytest.mark.parametrize('arguments', [[], ['--timeout', 'unlimited'], ['--timeout', 'none'], ['--timeout', '10']])
def test_cli_timeout_is_optional(store, monkeypatch, capsys, arguments):
    store.create('cli', 1024 * 1024)
    monkeypatch.setattr(sys, 'argv', ['experiment_storage', '--root', str(store.root),
        'exec', *arguments, 'cli', '--', sys.executable, '-c', 'print("completed")'])
    assert storage.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt['timeout_seconds'] == (10 if arguments == ['--timeout', '10'] else None)
    assert receipt['cleanup_verified'] and not receipt['timeout']


def test_unlimited_wait_remains_interruptible_and_reaps_process(tmp_path, monkeypatch):
    import pilot_process
    launched = []
    popen = pilot_process.subprocess.Popen
    def capture(*args, **kwargs):
        proc = popen(*args, **kwargs)
        launched.append(proc)
        return proc
    def interrupt():
        raise KeyboardInterrupt()
    monkeypatch.setattr(pilot_process.subprocess, 'Popen', capture)
    with pytest.raises(KeyboardInterrupt):
        pilot_process.run_process([sys.executable, '-c', 'import time;time.sleep(30)'],
            cwd=tmp_path, dest=tmp_path / 'receipt', timeout=None, resource_guard=interrupt)
    assert len(launched) == 1 and launched[0].poll() is not None


@pytest.mark.parametrize('timeout', [0, -1, float('nan'), float('inf'), True, 'unlimited'])
def test_invalid_api_timeout_does_not_start_lot(store, timeout):
    from pilot_process import run_process
    row = store.create('invalid', 1024 * 1024)
    with pytest.raises(storage.StorageError, match='timeout'):
        store.execute('invalid', [sys.executable, '-c', 'pass'], timeout)
    assert store.load()['lots']['invalid']['state'] == 'OPEN'
    with pytest.raises(ValueError, match='wall budget'):
        run_process([sys.executable, '-c', 'pass'], cwd=store.path(row),
                    dest=store.path(row) / 'invalid', timeout=timeout)


@pytest.mark.skipif(os.name == 'nt', reason='Linux nested session regression')
def test_outer_controller_reaps_nested_sessions_even_after_parent_exits(store):
    row = store.create('nested', 1024 * 1024); p = store.path(row)
    nested = 'import time,pathlib; time.sleep(1.5); pathlib.Path("escaped").write_text("bad")'
    command = 'import subprocess,sys; subprocess.Popen([sys.executable,"-c",' + repr(nested) + '],start_new_session=True)'
    result = store.execute('nested', [sys.executable, '-c', command], 10)
    assert result['cleanup_verified'] and result['containment'] == 'linux-subreaper'
    time.sleep(1.7)
    assert not (p / 'escaped').exists()


def test_lot_id_cannot_escape_managed_root(store):
    with pytest.raises(storage.StorageError):
        store.create('../other', 1024)


def test_pilot_accepts_only_lot_scoped_override_and_children_inherit_it(store, monkeypatch):
    import qualify_context_pilot as pilot
    row = store.create('pilot', 1024 * 1024); root = store.path(row)
    state = root / 'state/specgraph'; state.mkdir(parents=True)
    monkeypatch.setenv('TARE_SPECGRAPH_STATE_DIR', str(state))
    monkeypatch.delenv('TARE_EXPERIMENT_RUN_DIR', raising=False)
    with pytest.raises(ValueError, match='managed'):
        pilot.storage_binding(root / 'pilot')
    monkeypatch.setenv('TARE_EXPERIMENT_RUN_DIR', str(root))
    assert pilot.storage_binding(root / 'pilot')['state_dir'] == str(state)
    assert pilot.environment(root)['TARE_SPECGRAPH_STATE_DIR'] == str(state)
    with pytest.raises(ValueError, match='inside'):
        pilot.storage_binding(store.root / 'outside')
    monkeypatch.setenv('TARE_SPECGRAPH_STATE_DIR', str(store.root / 'global-cache'))
    with pytest.raises(ValueError, match='belong'):
        pilot.storage_binding(root / 'pilot')


def test_changing_cache_binding_invalidates_frozen_pilot(store, monkeypatch):
    import qualify_context_pilot as pilot
    root = store.path(store.create('bound', 1024 * 1024))
    output = root / 'pilot'; output.mkdir()
    state = root / 'state/specgraph'; state.mkdir(parents=True)
    monkeypatch.setenv('TARE_EXPERIMENT_RUN_DIR', str(root))
    monkeypatch.setenv('TARE_SPECGRAPH_STATE_DIR', str(state))
    p = {'files': {}, 'hosts': [], 'storage_binding': pilot.storage_binding(output)}
    (output / 'protocol.json').write_text(json.dumps(p))
    (output / 'protocol-lock.json').write_text(json.dumps({'sha256': pilot.digest(p)}))
    assert pilot.load_protocol(output) == p
    monkeypatch.delenv('TARE_EXPERIMENT_RUN_DIR'); monkeypatch.delenv('TARE_SPECGRAPH_STATE_DIR')
    with pytest.raises(ValueError, match='binding changed'):
        pilot.load_protocol(output)
