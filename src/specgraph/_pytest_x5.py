"""Pytest hooks for X5: local observations only, never attestation."""
import json
import os
from pathlib import Path
import sys



# Bounded observer implementation. No hook changes pytest's result or exit status.
MAX_BYTES = 1024 * 1024
MAX_NODEIDS = 4096
MAX_PAIRS = 32768
MAX_TEXT = 1024
root = Path(os.environ.get('SG_X5_ROOT', '.')).resolve()
reporting = bool(os.environ.get('SG_X5_OUTPUT') and os.environ.get('SG_X5_ROOT'))
trace_enabled = reporting and os.environ.get('SG_X5_TRACE') == '1'
inventory = []
executed = {}
failed = []
path_cache = {}
durations = {}
counters = {'tests': 0, 'pairs': 0, 'setup': 0, 'call': 0, 'teardown': 0}
truncated = False
reason = None
_budget = 4096


def _stop(code):
    global truncated, reason
    if not truncated:
        truncated, reason = True, code
        inventory.clear()
        executed.clear()
        failed.clear()
        durations.clear()
        path_cache.clear()


def _charge(text, copies=1):
    global _budget
    if len(text.encode('utf-8')) > MAX_TEXT:
        _stop('TEXT_LIMIT')
        return False
    _budget += copies * (len(json.dumps(text, ensure_ascii=True).encode('utf-8')) + 128)
    if _budget > MAX_BYTES:
        _stop('BYTE_LIMIT')
    return not truncated


def pytest_collection_finish(session):
    if not reporting:
        return
    for item in session.items:
        if truncated:
            break
        if len(inventory) >= MAX_NODEIDS:
            _stop('NODEID_LIMIT')
            break
        if _charge(item.nodeid, 4):
            inventory.append(item.nodeid)
            counters['tests'] += 1


def pytest_runtest_logreport(report):
    if not reporting or truncated:
        return
    if report.nodeid not in inventory:
        _stop('UNKNOWN_NODEID')
        return
    if report.failed and report.nodeid not in failed:
        failed.append(report.nodeid)
    if report.when in ('setup', 'call', 'teardown'):
        import math
        seconds = float(report.duration)
        if not math.isfinite(seconds) or seconds < 0:
            _stop('INVALID_DURATION')
            return
        durations.setdefault(report.nodeid, {})[report.when] = seconds
        counters[report.when] += 1


def pytest_runtest_protocol(item, nextitem):
    lines = set()
    previous = sys.gettrace()
    tracing = trace_enabled and not truncated

    def trace(frame, event, arg):
        if truncated:
            # Also detach already-installed local trace callbacks on their next event.
            sys.settrace(previous)
            return None
        if event == 'line':
            filename = frame.f_code.co_filename
            if filename not in path_cache:
                if len(path_cache) >= 1024:
                    path_cache.clear()
                path = Path(filename)
                try:
                    rel = path.resolve().relative_to(root).as_posix() if path.suffix == '.py' else None
                except (ValueError, OSError):
                    rel = None
                if len(filename) <= MAX_TEXT:
                    path_cache[filename] = rel
            else:
                rel = path_cache[filename]
            if rel:
                block = f'{rel}:{frame.f_lineno}'
                if block not in lines:
                    if counters['pairs'] >= MAX_PAIRS:
                        _stop('PAIR_LIMIT')
                    elif _charge(block):
                        lines.add(block)
                        counters['pairs'] += 1
        return None if truncated else trace

    if tracing:
        sys.settrace(trace)
    try:
        yield
    finally:
        if tracing:
            sys.settrace(previous)
        if reporting and not truncated:
            executed[item.nodeid] = sorted(lines)

pytest_runtest_protocol = __import__('pytest').hookimpl(hookwrapper=True)(pytest_runtest_protocol)


def pytest_sessionfinish(session, exitstatus):
    if not reporting:
        return
    package = sys.modules.get('specgraph')
    origin = getattr(package, '__file__', None)
    origin = str(Path(origin).resolve()) if origin else None
    if origin and not _charge(origin):
        origin = None

    def payload():
        return json.dumps({
            'source_origin': origin, 'tests': inventory, 'executed_blocks': executed,
            'failed': sorted(set(failed)), 'exitstatus': int(exitstatus),
            'durations': durations, 'counters': counters,
            'truncated': truncated, 'reason': reason, 'complete': not truncated,
        }, ensure_ascii=True, separators=(',', ':' )).encode('utf-8')

    data = payload()
    if len(data) > MAX_BYTES:
        _stop('BYTE_LIMIT')
        data = payload()
    try:
        Path(os.environ['SG_X5_OUTPUT']).write_bytes(data)
    except OSError:
        # A missing/unwritable receipt is not success evidence. Never mask test failures.
        pass
