"""Exercise an actual archived-wheel to current-wheel upgrade in a new venv.

Both wheels may be development snapshots with the same package version. Their
SHA-256 identities, not a fictitious release history, define the experiment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SEED = '''import json,hashlib
from pathlib import Path
from specgraph.indexer import index_repository
from specgraph.cache import CacheManifest,IncrementalCacheEngine
from specgraph.resolve import resolve_repository,runtime_directory,RECOVERY_INDEX
root=Path.cwd()
(root/'src').mkdir()
(root/'src/main.py').write_text('def original(): return 1\\n',encoding='utf-8')
(root/'src/note.txt').write_text('UpgradeNeedle evidence\\n',encoding='utf-8')
legacy=IncrementalCacheEngine.save_manifest(CacheManifest(project_name='archived-wheel-upgrade'),root)
indexed=index_repository(root)
resolved=resolve_repository(root,'UpgradeNeedle',structural_probe=lambda *_:('MISS',[]))
assert resolved.source=='traditional'
recovery=runtime_directory(root)/RECOVERY_INDEX
assert json.loads(recovery.read_text())['version']==1
receipt={'content_hash':indexed['content_hash'],'revision_seq':indexed['revision_seq'],
         'recovery_path':str(recovery),'recovery_sha256':hashlib.sha256(recovery.read_bytes()).hexdigest(),
         'legacy_path':str(legacy),'legacy_sha256':hashlib.sha256(legacy.read_bytes()).hexdigest()}
(root/'seed-receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
print(json.dumps(receipt))
'''

VERIFY = '''import json,hashlib,importlib.metadata
from pathlib import Path
from specgraph.indexer import index_repository
from specgraph.query import project_query
from specgraph.resolve import resolve_repository,runtime_directory,RECOVERY_INDEX
from specgraph.storage import RepositoryIndex
root=Path.cwd();seed=json.loads((root/'seed-receipt.json').read_text())
with RepositoryIndex(root) as reader:
 assert reader.metadata()['content_hash']==seed['content_hash']
 assert reader.search('original')
current=index_repository(root)
first=resolve_repository(root,'UpgradeNeedle')
assert first.source=='traditional'
recovery=runtime_directory(root)/RECOVERY_INDEX
assert json.loads(recovery.read_text())['version']==2
assert str(recovery)!=seed['recovery_path']
assert resolve_repository(root,'UpgradeNeedle').source=='learned'
assert hashlib.sha256(Path(seed['recovery_path']).read_bytes()).hexdigest()==seed['recovery_sha256']
assert hashlib.sha256(Path(seed['legacy_path']).read_bytes()).hexdigest()==seed['legacy_sha256']
(root/'src/main.py').write_text('def replacement(): return 2\\n',encoding='utf-8')
changed=index_repository(root)
assert changed['revision_seq']>current['revision_seq']
assert project_query(root,'replacement')['results']
assert not project_query(root,'original')['results']
assert resolve_repository(root,'UpgradeNeedle').source=='traditional'
dist=importlib.metadata.distribution('tare-tools-specgraph')
print(json.dumps({'status':'PASS','initial_revision':seed['revision_seq'],
 'after_upgrade_revision':current['revision_seq'],'after_mutation_revision':changed['revision_seq'],
 'direct_url':json.loads(dist.read_text('direct_url.json')),
 'checks':['read previous SQLite publication before rewriting','explicit publication with new implementation',
 'previous recoveryv1 artifact preserved','new recoveryv2 source-validated and reusable',
 'legacy JSONcache byte-preserved','mutation republishes and invalidates v2','renamed symbol visible; old symbol absent']}))
'''


def qualify(old_wheel, new_wheel, output):
    output.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8="1")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        env.pop(key, None)
    def run(name, args, cwd):
        done = subprocess.run([str(a) for a in args], cwd=cwd, env=env, text=True,
                              encoding="utf-8", capture_output=True, timeout=240)
        (output / (name + ".stdout")).write_text(done.stdout, encoding="utf-8")
        (output / (name + ".stderr")).write_text(done.stderr, encoding="utf-8")
        if done.returncode:
            raise RuntimeError(f"{name}: {done.stderr[-2000:]}")
        return done.stdout
    with tempfile.TemporaryDirectory(prefix="specgraph-real-wheel-upgrade-") as directory:
        tmp = Path(directory); venv = tmp / "installed"
        if os.name == "nt":
            run("venv", [sys.executable, "-m", "venv", venv], tmp)
            python = venv / "Scripts/python.exe"
            installer = [python, "-m", "pip", "install", "--only-binary=:all:"]
        else:
            run("venv", ["uv", "venv", venv, "--python", sys.executable], tmp)
            python = venv / "bin/python"
            installer = ["uv", "pip", "install", "--python", python, "--only-binary", ":all:"]
        run("install-previous", installer + [str(old_wheel)], tmp)
        fixture = tmp / "fixture"; fixture.mkdir()
        env["TARE_SPECGRAPH_STATE_DIR"] = str(tmp / "state")
        seed = json.loads(run("seed-previous", [python, "-I", "-c", SEED], fixture))
        run("upgrade", installer + ["--force-reinstall", "--no-deps", str(new_wheel)], tmp)
        result = json.loads(run("verify-current", [python, "-I", "-c", VERIFY], fixture))
        result.update({"schema": "tare.tools/specgraph-c8-real-wheel-upgrade/1", "platform": sys.platform,
                       "old_wheel_sha256": hashlib.sha256(old_wheel.read_bytes()).hexdigest(),
                       "new_wheel_sha256": hashlib.sha256(new_wheel.read_bytes()).hexdigest(), "seed": seed,
                       "authority": "actual archived working-snapshot wheel upgrade; not historical releases",
                       "isolated_interpreter": True, "editable": False,
                       "host_reload_claimed": False})
        (output / "receipt.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-wheel", required=True, type=Path)
    parser.add_argument("--new-wheel", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = qualify(args.old_wheel.resolve(), args.new_wheel.resolve(), args.output.resolve())
    print(json.dumps({key: result[key] for key in ("status", "platform", "old_wheel_sha256", "new_wheel_sha256")}))
