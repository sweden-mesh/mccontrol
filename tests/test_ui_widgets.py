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


# ---- dialogs ---------------------------------------------------------------------

from mccontrol.ui import dialogs


def test_a_field_edits_and_recalls_history():
    f = dialogs.Field("Command", history=["region", "get autoreply"], width=10)
    for ch in "ver":
        assert f.handle(ord(ch))
    assert f.value == "ver"
    assert f.handle(curses.KEY_BACKSPACE) and f.value == "ve"
    assert f.handle(curses.KEY_UP) and f.value == "get autoreply"
    assert f.handle(curses.KEY_UP) and f.value == "region"
    assert f.handle(curses.KEY_UP) and f.value == "region"              # at the oldest
    assert f.handle(curses.KEY_DOWN) and f.value == "get autoreply"
    assert f.handle(curses.KEY_DOWN) and f.value == ""                  # past the newest: blank
    assert not f.handle(curses.KEY_F1)
    secret = dialogs.Field("Password", "abc", secret=True)
    assert secret.shown() == "***"
    assert dialogs.Field("Command", width=2).handle(ord("x")) and not dialogs.Field("Command", "xx", width=2).handle(ord("y"))


def test_a_dialog_runs_on_a_terminal_enter_takes_the_default_and_esc_cancels():
    script = """
import sys; sys.path.insert(0, %r)
import curses, json
from mccontrol.ui import Screen, Dialog, Field, Button, confirm
def main(win):
    s = Screen(win)
    d = Dialog("Send a Command", fields=[Field("Command", width=30), Field("Note", width=20)],
               buttons=[Button("Send", "send", default=True), Button("Cancel", None)])
    r1 = d.run(s)
    r2 = confirm(s, "Confirm", ["Command: region denyf *"], "stops forwarding every unscoped message")
    r3 = confirm(s, "Confirm", ["again"], "x")
    print("RESULT", json.dumps([r1, d.fields[0].value, d.fields[1].value, r2, r3]), flush=True)
curses.wrapper(main)
""" % os.getcwd()
    import fcntl, signal, struct, termios
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.execvp(sys.executable, [sys.executable, "-c", script])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    def send(keys, wait=0.4):
        time.sleep(wait); os.write(fd, keys)
    send(b"region")           # typed into the focused Command field
    send(b"\t")               # Tab to Note
    send(b"hi")
    send(b"\r")               # Enter on a field: the default button, Send
    send(b"\x1b")             # confirm 1: Esc -> False
    send(b"\x1bOD")           # confirm 2: Left (xterm keypad mode) moves focus from Cancel to Send anyway
    send(b"\r")               #            Enter -> True
    out = b""; end = time.time() + 4
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try: out += os.read(fd, 65536)
            except OSError: break
    os.waitpid(pid, 0)
    text = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b" ", out).decode("utf-8", "replace")
    m = re.search(r"RESULT (\[.*?\])", text)
    assert m, text[-400:]
    import json
    assert json.loads(m.group(1)) == ["send", "region", "hi", False, True]
    assert "Send a Command" in text and "stops forwarding" in text


# ---- menu bar --------------------------------------------------------------------

def test_the_menu_bar_opens_on_f10_or_alt_letter_and_runs_an_item():
    script = """
import os, sys; sys.path.insert(0, %r); os.environ["ESCDELAY"] = "150"
import curses, json
from mccontrol.ui import Screen, MenuBar, Menu, MenuItem
def main(win):
    s = Screen(win)
    bar = MenuBar([Menu("Devices", [MenuItem("Discover", "disco", "F5"), MenuItem("Forget", "forget", "F8")]),
                   Menu("Send", [MenuItem("To device", "send", "F2"), MenuItem("To all in region", "all", "F7")])])
    got = []
    while len(got) < 3:
        s.erase(); bar.draw(s); s.refresh()
        k = s.key()
        if bar.wants(k):
            got.append(bar.run(s, k, lambda: s.erase()))
    print("RESULT", json.dumps(got), flush=True)
curses.wrapper(main)
""" % os.getcwd()
    import fcntl, struct, termios, signal
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm"
        os.execvp(sys.executable, [sys.executable, "-c", script])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    def send(keys, wait=0.4):
        time.sleep(wait); os.write(fd, keys)
    send(b"\x1b[21~")         # F10: Devices open
    send(b"\x1bOB")           # Down -> Forget
    send(b"\r")               # run it
    send(b"\x1bs")            # Alt+s: Send open
    send(b"\x1bOC")           # Right -> wraps to Devices
    send(b"\r")               # Discover
    send(b"\x1bs"); send(b"t")   # Alt+s then the letter t -> "To device"
    out = b""; end = time.time() + 4
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try: out += os.read(fd, 65536)
            except OSError: break
    os.waitpid(pid, 0)
    text = re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]", b" ", out).decode("utf-8", "replace")
    m = re.search(r"RESULT (\[.*?\])", text)
    assert m, text[-300:]
    import json
    assert json.loads(m.group(1)) == ["forget", "disco", "send"]
    assert "Discover" in text and "F5" in text


# ---- the Turbo Pascal scheme ----------------------------------------------------------

def test_the_scheme_is_black_and_red_at_the_edges_and_blue_in_the_middle():
    from mccontrol.ui.screen import Colors
    pairs = {n: (fg, bg) for n, fg, bg in Colors.PAIRS}
    assert pairs[2] == (curses.COLOR_WHITE, curses.COLOR_BLACK)     # menu bar, key bar
    assert pairs[7] == (curses.COLOR_RED, curses.COLOR_BLACK)       # hot letters, key numbers
    assert pairs[10] == (curses.COLOR_BLACK, curses.COLOR_GREEN)    # open menu
    assert pairs[1][1] == curses.COLOR_BLUE and pairs[4][1] == curses.COLOR_WHITE


def test_monochrome_degrades_to_bold_and_reverse(monkeypatch):
    from mccontrol.ui.screen import Colors
    monkeypatch.setattr(curses, "has_colors", lambda: False)
    c = Colors()
    assert not c.ok
    assert c.hot == curses.A_BOLD and c.key_number == curses.A_BOLD
    assert c.bar == curses.A_NORMAL
    assert c.menu_cursor == curses.A_REVERSE and c.cursor == curses.A_REVERSE


def test_the_menu_bar_paints_the_hot_letter_red_and_the_key_bar_its_numbers():
    """Raw escape codes kept: the hot letter of each title and the key numbers are drawn
    with pair 7 (SGR 31 red foreground on xterm), the labels with pair 2 (white, 37)."""
    script = """
import sys; sys.path.insert(0, %r)
import curses
from mccontrol.ui import Screen, KeyBar
from mccontrol.ui.menu import MenuBar, Menu, MenuItem
def main(win):
    s = Screen(win)
    MenuBar([Menu("Devices", [MenuItem("Forget", "f", "F8")], hotkey="v")]).draw(s)
    KeyBar(["Help", "Send"]).draw(s)
    s.refresh(); win.getch()
curses.wrapper(main)
""" % os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw = _pty_run_raw(script)
    red = rb"\x1b\[31m(\x1b\[[0-9;]*m)*"
    assert re.search(red + rb"v", raw), raw                      # the hot letter of Devices
    assert re.search(red + rb" 1", raw), raw                     # key number 1
    assert re.search(rb"\x1b\[37m(\x1b\[[0-9;]*m)*Help", raw), raw   # the label, white


def _pty_run_raw(script, cols=80, rows=24, seconds=2.0):
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
    return out


# ---- the list box -------------------------------------------------------------------

def test_the_list_box_filters_and_the_cursor_skips_headers():
    from mccontrol.ui.dialogs import ListBox
    box = ListBox("x", [("reads", [("ver", 1), ("get name", 2)]), ("node", [("reboot", 3)])])
    assert [r[0] for r in box.rows] == ["head", "item", "item", "head", "item"]
    assert box.rows[box.cursor][1] == "ver"               # settled past the first header
    box.move(+1); assert box.rows[box.cursor][1] == "get name"
    box.move(+1); assert box.rows[box.cursor][1] == "reboot"   # skipped the "node" header
    box.move(-1); assert box.rows[box.cursor][1] == "get name"
    box.filter = "re"; box._rebuild()
    assert [r[1] for r in box.rows] == ["node", "reboot"]       # only reboot matches; its header stays
    box.filter = "zzz"; box._rebuild()
    assert box.rows == []


def test_the_choice_box_previews_and_the_multi_box_joins():
    from mccontrol.ui.dialogs import ChoiceBox
    one = ChoiceBox("set autoreply", ["on", "off"])
    assert one.preview() == "set autoreply on"
    one.cursor = 1
    assert one.preview() == "set autoreply off"
    many = ChoiceBox("x", ["a", "b", "c"], multi=True)
    many.chosen = {"c", "a"}
    assert many.preview() == "x a c"                     # in the choices' order, not the picking order
    none = ChoiceBox("trigger test", ["", "F", "P"])
    assert none.preview() == "trigger test"
