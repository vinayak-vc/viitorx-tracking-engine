"""What a host needs from a child process, that Unity's job object gave for free and another engine does not.

  * `--parent-pid N`: the engine stops when process N is gone (the host crashed or was killed), instead of holding the camera and a GPU for ever.
  * `--exit-on-stdin-close`: the engine stops when its standard input reaches end of file (a host that holds a pipe to it and lets go).
  * A status line a host can parse: `VTSTATUS {json}` on its own line of standard output, mixed in with the human log lines. Events:
    `starting`, `ready`, `heartbeat`, `stopping`, `error`. Every line carries the engine version, the pid and a wall-clock time.
"""
import json
import os
import sys
import threading
import time

from . import __version__

STATUS_PREFIX = "VTSTATUS "


def parent_alive(pid):
    """True while process `pid` exists. Windows: a handle opened with SYNCHRONIZE and waited on for 0 ms; elsewhere: signal 0."""
    if pid is None or pid <= 0:
        return True
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE = 0x00100000
        WAIT_TIMEOUT = 0x102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, int(pid))
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class StopSignal:
    """One flag a loop checks. Set by a watcher thread (stdin closed) or by the loop itself (parent gone); says why."""

    def __init__(self):
        self._event = threading.Event()
        self.reason = ""

    def set(self, reason):
        if not self._event.is_set():
            self.reason = reason
            self._event.set()

    def is_set(self):
        return self._event.is_set()


class ParentWatch:
    """Checked from the frame loop at most once a second: cheap enough to call every frame."""

    def __init__(self, pid, stop, alive=parent_alive, clock=time.time, interval=1.0):
        self.pid = pid
        self.stop = stop
        self._alive = alive
        self._clock = clock
        self._interval = interval
        self._next = 0.0

    def poll(self):
        if self.pid is None:
            return
        now = self._clock()
        if now < self._next:
            return
        self._next = now + self._interval
        if not self._alive(self.pid):
            self.stop.set("the parent process %d is gone" % self.pid)


def watch_stdin(stop, stream=None):
    """A daemon thread that sets `stop` when `stream` (default: standard input) reaches end of file. Returns the thread."""
    stream = stream if stream is not None else sys.stdin

    def run():
        try:
            while True:
                if not stream.readline():
                    break
        except Exception:
            pass
        stop.set("standard input was closed")

    t = threading.Thread(target=run, name="stdin-watch", daemon=True)
    t.start()
    return t


class StatusEmitter:
    """Writes `VTSTATUS {json}` lines. `write` is injectable for tests (default: print to standard output, flushed)."""

    def __init__(self, enabled=True, write=None, clock=time.time, pid=None):
        self.enabled = enabled
        self._write = write if write is not None else (lambda line: print(line, flush=True))
        self._clock = clock
        self._pid = pid if pid is not None else os.getpid()

    def emit(self, event, **fields):
        if not self.enabled:
            return None
        record = {"event": event, "engine": __version__, "pid": self._pid, "t": round(self._clock(), 3)}
        record.update(fields)
        line = STATUS_PREFIX + json.dumps(record, separators=(",", ":"), sort_keys=True)
        self._write(line)
        return line


def parse_status_line(line):
    """The record in a `VTSTATUS` line, or None for any other line. For hosts and tests."""
    if not line.startswith(STATUS_PREFIX):
        return None
    try:
        return json.loads(line[len(STATUS_PREFIX):])
    except ValueError:
        return None
