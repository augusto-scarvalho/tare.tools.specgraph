"""Linux backend identity collector; usable locally or over SSH with stdin.

Hash the GGUF once, then reuse the proof only while device/inode/size/mtime/ctime
are identical. The proof and current stat are exposed; this is not a fresh hash
per cell. No credentials, service restarts or sampling changes are involved.
"""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request


def file_hash(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def capture(alias, port, proof_path):
    processes = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdecimal(): continue
        try:
            argv = (proc / 'cmdline').read_bytes().decode().strip('\0').split('\0')
            if '--alias' in argv and argv[argv.index('--alias') + 1] == alias and 'llama-server' in argv[0]:
                processes.append((proc, argv))
        except (FileNotFoundError, PermissionError, UnicodeError):
            pass
    if len(processes) != 1:
        raise ValueError('backend alias must resolve to one live process')
    proc, argv = processes[0]
    if argv[argv.index('--port') + 1] != str(port):
        raise ValueError('backend process/port mismatch')
    model = Path(argv[argv.index('-m') + 1])
    stat = model.stat()
    signature = {key: getattr(stat, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
    proof_path = Path(proof_path)
    proof = json.loads(proof_path.read_text()) if proof_path.is_file() else {}
    fresh = proof.get('stat') != signature or proof.get('path') != str(model)
    if fresh:
        proof = {'path': str(model), 'stat': signature, 'sha256': file_hash(model)}
        if signature != {key: getattr(model.stat(), key) for key in signature}:
            raise ValueError('model changed during hashing')
        proof_path.parent.mkdir(parents=True, exist_ok=True)
        proof_path.write_text(json.dumps(proof, sort_keys=True))
    with urllib.request.urlopen(f'http://127.0.0.1:{port}/props', timeout=10) as response:
        props = json.load(response)
    template = props['chat_template']
    return {'binary_sha256': file_hash(proc / 'exe'), 'model_sha256': proof['sha256'],
            'model_hash_verified': True, 'model_hash_fresh': fresh, 'model_stat': signature,
            'model_hash_proof': proof, 'template_sha256': hashlib.sha256(template.encode()).hexdigest(),
            'argv': argv, 'pid': int(proc.name),
            'process_start_ticks': (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19],
            'effective_parameters': props['default_generation_settings'], 'props': props,
            'scope': 'backend bytes and defaults; effective per-request parameters captured separately'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--alias', default='qwen38')
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--proof', required=True)
    args = parser.parse_args()
    print(json.dumps(capture(args.alias, args.port, args.proof), sort_keys=True))
