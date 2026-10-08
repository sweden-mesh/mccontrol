"""Waiting on a curses program in a pty for as long as a key takes, not for a fixed time.

A program that has dealt with a key goes back to reading its terminal. Linux shows that in
/proc: the main thread blocked in read() on fd 0, and its count of voluntary context switches
moved on since the key went in (so a read the key has already woken is not mistaken for the
next one). Where /proc cannot say -- another kernel or architecture -- `settle` waits its
whole limit, which is the fixed delay a walk used before.
"""
import os
import platform
import select
import time

READ = {"x86_64": "0", "aarch64": "63", "riscv64": "63"}.get(platform.machine())
TICK = 0.05


def reading_the_terminal(pid):
    """True when the main thread of `pid` is blocked in read() on fd 0; None where /proc
    does not say."""
    try:
        with open(f"/proc/{pid}/syscall") as f:
            fields = f.read().split()
    except OSError:
        return None
    if READ is None or len(fields) < 2:
        return None
    return fields[0] == READ and int(fields[1], 16) == 0


def switches(pid):
    """The main thread's voluntary context switches so far, or -1."""
    try:
        with open(f"/proc/{pid}/task/{pid}/status") as f:
            return next(int(line.split()[1]) for line in f if line.startswith("voluntary_ctxt_switches"))
    except (OSError, StopIteration, ValueError):
        return -1


def settle(fd, pid, limit, sink, since=None):
    """Read `fd` into `sink(bytes)` until `pid` has woken since `since` (its `switches` when
    the key went in; None at start-up), is reading its terminal again and has written
    nothing for two ticks -- or for `limit` seconds. False when the pty closed."""
    end, quiet = time.time() + limit, 0
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], TICK)
        if r:
            try:
                sink(os.read(fd, 65536))
            except OSError:
                return False
            quiet = 0
        elif reading_the_terminal(pid) and (since is None or switches(pid) > since):
            quiet += 1
            if quiet >= 2:
                return True
        else:
            quiet = 0
    return True


def key(fd, pid, data, limit, sink):
    """Type `data` and settle, `limit` seconds at most."""
    since = switches(pid)
    os.write(fd, data)
    return settle(fd, pid, limit, sink, since)
