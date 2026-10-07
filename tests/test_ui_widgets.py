import curses
import os
import pty
import re
import select
import sys
import time

from mccontrol.ui import widgets


class _FakePanel:
    active = True

    def __init__(self, height=5):
        self._h = height

    @property
    def inner(self):
        return (1, 1, 30, self._h)


def _view(n=12, height=5):
    v = widgets.ListView(_FakePanel(height))
    v.set_rows([f"row {i}" for i in range(n)])
    return v


def test_the_cursor_moves_and_the_view_scrolls_to_keep_it_visible():
    v = _view()
    for _ in range(7):
        assert v.handle(curses.KEY_DOWN)
    assert v.cursor == 7 and v.top == 3
    assert v.handle(curses.KEY_HOME) and v.cursor == 0 and v.top == 0
    assert v.handle(curses.KEY_END) and v.cursor == 11 and v.top == 7
    assert v.handle(curses.KEY_PPAGE) and v.cursor == 6
    assert v.handle(curses.KEY_NPAGE) and v.cursor == 11


def test_ins_tags_and_moves_down_space_too_and_star_inverts():
    v = _view(4)
    assert v.handle(curses.KEY_IC) and v.tags == {0} and v.cursor == 1
    assert v.handle(32) and v.tags == {0, 1} and v.cursor == 2
    assert v.handle(32) and v.tags == {0, 1, 2} and v.cursor == 3
    assert v.handle(32) and v.tags == {0, 1, 2, 3} and v.cursor == 3      # last row: tag, stay
    assert v.handle(32) and v.tags == {0, 1, 2} and v.cursor == 3          # Space again untags
    assert v.handle(ord("*")) and v.tags == {3}


def test_selected_is_the_tags_or_else_the_cursor():
    v = _view(3)
    assert v.selected == [0]
    v.handle(curses.KEY_DOWN)
    assert v.selected == [1]
    v.handle(curses.KEY_IC)
    assert v.selected == [1]            # tagged row 1, cursor moved to 2
    assert widgets.ListView(_FakePanel()).selected == []


def test_new_rows_keep_tags_unless_told_otherwise_and_clamp_the_cursor():
    v = _view(5)
    v.handle(curses.KEY_IC)
    v.handle(curses.KEY_END)
    v.set_rows(["a", "b"])
    assert v.tags == {0} and v.cursor == 1
    v.set_rows(["a"], keep=False)
    assert v.tags == set() and v.cursor == 0


def test_an_unknown_key_is_not_consumed():
    assert not _view().handle(ord("q"))


def _pty_run(script, cols=80, rows=24, seconds=2.0):
    """Run `script` (python source) in a pty and return the text it drew, escape codes removed."""
    import fcntl
    import signal
    import struct
    import termios

    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.execvp(sys.executable, [sys.executable, "-c", script])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                out += os.read(fd, 65536)
            except OSError:
                break
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]", b" ", out).decode("utf-8", "replace")


def test_every_widget_draws_on_a_terminal():
    script = """
import sys; sys.path.insert(0, %r)
import curses
from mccontrol.ui import Screen, Panel, ListView, KeyBar, Message
def main(win):
    s = Screen(win)
    left = Panel(1, 0, 40, 20, "Devices"); left.active = True; left.footer = "2 of 3 tagged"
    right = Panel(1, 40, 40, 20, "History")
    left.draw(s); right.draw(s)
    v = ListView(left); v.set_rows(["LOUTEST   4AC61A7F JKG", "UGO       AB6B9285 JKG", "FAR  FD700369 GOT"])
    v.tags = {0, 1}; v.draw(s)
    KeyBar(["Help", "Send", "Hist", "Edit", "Disco", "Regns", "All", "Del", "Menu", "Quit"]).draw(s)
    m = Message(); m.say("3 of 3 answered"); m.draw(s)
    s.refresh(); win.getch()
curses.wrapper(main)
""" % os.getcwd()
    text = _pty_run(script)
    for needle in ("Devices", "History", "2 of 3 tagged", "LOUTEST", "FD700369", "3 of 3 answered"):
        assert needle in text, needle
    # attribute changes become spaces once the escape codes are stripped, so the number and
    # its label are matched with room between them
    assert re.search(r"1\s*Help", text) and re.search(r"10\s*Quit", text)
