"""Supervised host execution with an optional wall-clock ceiling.

Windows children inherit a kill-on-close Job Object. The initial process is
assigned while suspended, before it can create an uncontained child. POSIX uses
a new process group (deliberate daemonisation is outside this containment).
Output goes to files so a descendant holding a pipe cannot stall communicate().
"""
from __future__ import annotations

import os
import math
from pathlib import Path
import signal
import subprocess
import time


class LinuxSubreaper:
    """A single-command supervisor also owns orphaned nested process groups.

    Only the outer experiment controller uses this; ordinary host invocations
    keep their existing process-group behavior. Existing children are excluded.
    """
    def __init__(self):
        import ctypes
        self.c = ctypes
        self.lib = ctypes.CDLL(None, use_errno=True)
        previous = ctypes.c_int()
        if self.lib.prctl(37, ctypes.byref(previous), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'PR_GET_CHILD_SUBREAPER failed')
        self.previous = previous.value
        self.before = self.descendants()
        if self.lib.prctl(36, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), 'PR_SET_CHILD_SUBREAPER failed')

    def descendants(self):
        rows = {}
        for folder in Path('/proc').iterdir():
            if not folder.name.isdecimal():
                continue
            try:
                fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
                rows[int(folder.name)] = (int(fields[1]), fields[19], fields[0])
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
        found = {}; parents = {os.getpid()}
        while True:
            added = {pid: (start, state) for pid, (parent, start, state) in rows.items()
                     if parent in parents and pid not in found}
            if not added:
                return found
            found.update(added); parents.update(added)

    def owned(self):
        return {pid: state for pid, state in self.descendants().items()
                if self.before.get(pid, (None,))[0] != state[0]}

    def active(self):
        return sum(state not in ('Z', 'X') for _, state in self.owned().values())

    def terminate(self):
        for pid in self.owned():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def reap(self):
        for pid in self.owned():
            try:
                os.waitpid(pid, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass

    def close(self):
        self.reap()
        self.lib.prctl(36, self.previous, 0, 0, 0)


class WindowsJob:
    def __init__(self):
        import ctypes as c
        from ctypes import wintypes as w
        self.c = c
        self.k = c.WinDLL('kernel32', use_last_error=True)
        signatures = {
            'CreateJobObjectW': ([c.c_void_p, w.LPCWSTR], w.HANDLE),
            'SetInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
            'AssignProcessToJobObject': ([w.HANDLE, w.HANDLE], w.BOOL),
            'TerminateJobObject': ([w.HANDLE, w.UINT], w.BOOL),
            'QueryInformationJobObject': ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p], w.BOOL),
            'CloseHandle': ([w.HANDLE], w.BOOL),
        }
        for name, (args, result) in signatures.items():
            fn = getattr(self.k, name); fn.argtypes = args; fn.restype = result

        class Basic(c.Structure):
            _fields_ = [('process_time', c.c_int64), ('job_time', c.c_int64), ('flags', w.DWORD),
                        ('min_ws', c.c_size_t), ('max_ws', c.c_size_t), ('active_limit', w.DWORD),
                        ('affinity', c.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]

        class IO(c.Structure):
            _fields_ = [(name, c.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read', 'write', 'other')]

        class Extended(c.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', c.c_size_t),
                        ('job_memory', c.c_size_t), ('peak_process', c.c_size_t), ('peak_job', c.c_size_t)]

        class Accounting(c.Structure):
            _fields_ = [(name, c.c_int64) for name in ('user', 'kernel', 'period_user', 'period_kernel')]
            _fields_ += [(name, w.DWORD) for name in ('faults', 'total', 'active', 'terminated')]

        self.Accounting = Accounting
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise c.WinError(c.get_last_error())
        limits = Extended(); limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.handle, 9, c.byref(limits), c.sizeof(limits)):
            self.close(); raise c.WinError(c.get_last_error())

    def assign_and_resume(self, proc):
        if not self.k.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise self.c.WinError(self.c.get_last_error())
        resume = self.c.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [self.c.c_void_p]; resume.restype = self.c.c_long
        status = resume(int(proc._handle))
        if status != 0:
            raise OSError(f'NtResumeProcess failed: {status}')

    def active(self):
        info = self.Accounting()
        if not self.k.QueryInformationJobObject(self.handle, 1, self.c.byref(info), self.c.sizeof(info), None):
            raise self.c.WinError(self.c.get_last_error())
        return info.active

    def terminate(self):
        if not self.k.TerminateJobObject(self.handle, 1):
            raise self.c.WinError(self.c.get_last_error())

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle); self.handle = None


def _group_live(pgid):
    # Linux /proc distinguishes harmless unreaped zombies from running children.
    if Path('/proc').is_dir():
        for folder in Path('/proc').iterdir():
            if folder.name.isdecimal():
                try:
                    fields = (folder / 'stat').read_text().rsplit(')', 1)[1].split()
                    if int(fields[2]) == pgid and fields[0] not in ('Z', 'X'):
                        return True
                except (FileNotFoundError, ProcessLookupError):
                    pass
        return False
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def run_process(argv, *, cwd, dest, timeout, env=None, stdin=b'', resource_guard=None, supervise_tree=False):
    """Return only after cleanup; unknown cleanup is a hard stop for the runner."""
    if timeout is not None and (type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('positive wall budget or None required')
    dest = Path(dest); dest.mkdir(parents=True, exist_ok=True)
    (dest / 'stdin.txt').write_bytes(stdin)
    started = time.perf_counter(); job = None; proc = None; family = None
    result = {'exit_code': None, 'timeout': False, 'timeout_seconds': timeout, 'cleanup_verified': False,
              'containment': 'windows-job' if os.name == 'nt' else 'posix-process-group'}
    try:
        if os.name == 'nt':
            job = WindowsJob()
        elif supervise_tree:
            if not Path('/proc').is_dir():
                raise ValueError('tree supervision requires Linux or Windows')
            family = LinuxSubreaper()
            result['containment'] = 'linux-subreaper'
        with (dest / 'stdin.txt').open('rb') as inp, (dest / 'stdout.txt').open('wb') as out, (dest / 'stderr.txt').open('wb') as err:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=inp, stdout=out, stderr=err,
                **({'creationflags': 0x4} if job else {'start_new_session': True}))
            result['pid'] = proc.pid
            if job:
                job.assign_and_resume(proc)
            try:
                if resource_guard is None:
                    result['exit_code'] = proc.wait(timeout=timeout)
                else:
                    deadline = None if timeout is None else time.monotonic() + timeout
                    while True:
                        try:
                            resource_guard()
                        except Exception as exc:
                            result['resource_error'] = f'{type(exc).__name__}: {exc}'
                            break
                        remaining = None if deadline is None else deadline - time.monotonic()
                        if remaining is not None and remaining <= 0:
                            result['timeout'] = True
                            break
                        try:
                            result['exit_code'] = proc.wait(timeout=1 if remaining is None else min(1, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
            except subprocess.TimeoutExpired:
                result['timeout'] = True
            finally:
                # Always clean up, including a host's partial result with exit 0.
                result['descendants_at_exit'] = job.active() if job else family.active() if family else int(_group_live(proc.pid))
                if job:
                    job.terminate()
                elif family:
                    family.terminate()
                else:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                proc.wait(timeout=5)
                until = time.monotonic() + 5
                while time.monotonic() < until:
                    if family:
                        family.terminate(); family.reap()
                    if ((job.active() == 0) if job else (family.active() == 0) if family else not _group_live(proc.pid)):
                        result['cleanup_verified'] = True
                        break
                    time.sleep(.02)
    finally:
        # Also handles assignment/resume failures before proc.wait().
        if proc and proc.poll() is None:
            proc.kill(); proc.wait(timeout=5)
        if job:
            job.close()
        if family:
            family.terminate(); family.close()
    result['wall_ms'] = (time.perf_counter() - started) * 1000
    return result
