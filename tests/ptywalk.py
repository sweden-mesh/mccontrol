"""Waiting on a curses program in a pty for as long as a key takes, not for a fixed time.

A program that has dealt with a key goes back to waiting for the next. Linux shows that in
/proc: the main thread blocked in read() on fd 0, or in poll() on fd 0 alone (curses waiting
with a timeout, unless it is one of curses' own short waits for the rest of an Esc sequence
or a mouse click), and its count of voluntary context switches moved on since the key went in
(so a wait the key has already woken is not mistaken for the next one). Where /proc cannot
say -- another kernel or architecture -- `settle` waits its whole limit, which is the fixed
delay a walk used before.
"""
import os
import platform
import select
import sys
import time

# (read, poll, ppoll) by architecture; aarch64 and riscv64 have no poll
CALLS = {"x86_64": ("0", "7", "271"), "aarch64": ("63", None, "73"), "riscv64": ("63", None, "73")}.get(platform.machine())
TICK = 0.05
SHORT = 0.25          # seconds: curses waits less than this for the rest of a key (ESCDELAY, mouseinterval)


def peek(pid, address, size):
    """`size` bytes at `address` in `pid` as a signed integer, or None."""
    try:
        with open(f"/proc/{pid}/mem", "rb") as mem:
            mem.seek(address)
            return int.from_bytes(mem.read(size), sys.byteorder, signed=True)
    except (OSError, ValueError, OverflowError):
        return None


def reading_the_terminal(pid):
    """True when the main thread of `pid` is blocked waiting on fd 0 and nothing else;
    None where /proc does not say."""
    try:
        with open(f"/proc/{pid}/syscall") as f:
            fields = f.read().split()
    except OSError:
        return None
    if CALLS is None or len(fields) < 3:
        return None
    read, poll, ppoll = CALLS
    if fields[0] == read:
        return int(fields[1], 16) == 0
    if fields[0] not in (poll, ppoll) or len(fields) < 4:
        return False
    if int(fields[2], 16) != 1 or peek(pid, int(fields[1], 16), 4) != 0:     # one pollfd, on fd 0
        return False
    if fields[0] == poll:                                                     # timeout in ms, -1 none
        timeout = int.from_bytes(int(fields[3], 16).to_bytes(8, "little")[:4], "little", signed=True) / 1000
    else:                                                                     # a struct timespec, NULL none
        at = int(fields[3], 16)
        timeout = -1 if at == 0 else (peek(pid, at, 8) or 0) + (peek(pid, at + 8, 8) or 0) / 1e9
    return timeout < 0 or timeout >= SHORT


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
