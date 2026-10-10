"""The 5250 terminal on curses: green on black, a title line, input fields drawn as
underlined runs, the message line above the function-key rows, and keys read one at a time.

Nothing here knows what a device is; the screens in `app.py` draw on it.
"""

import curses
import datetime
import os
import sys

ENTER = (curses.KEY_ENTER, 10, 13)
BACKSPACE = (curses.KEY_BACKSPACE, 127, 8, curses.KEY_DC)
EXIT = (curses.KEY_F3, curses.KEY_F12, 27)
MIN_WIDTH, MIN_HEIGHT = 80, 24


def fmt_small(w, h):
    """The notice a terminal below the minimum gets, in what room there is."""
    import textwrap
    text = f"This needs {MIN_WIDTH}x{MIN_HEIGHT}; the terminal is {w}x{h}. Make it larger to go on."
    return textwrap.wrap(text, max(10, w - 1))[: max(1, h)]


def fkey(n):
    return curses.KEY_F0 + n


# Function keys a terminal description may not cover (PuTTY, a Windows console, tmux under a
# mismatched TERM): the sequence after the Esc, and the key curses would have returned.
SEQUENCES = {}
for _n, _s in enumerate(("OP", "OQ", "OR", "OS", "[15~", "[17~", "[18~", "[19~", "[20~", "[21~", "[23~", "[24~"), 1):
    SEQUENCES[_s] = fkey(_n)
for _n, _s in enumerate(("[11~", "[12~", "[13~", "[14~"), 1):
    SEQUENCES[_s] = fkey(_n)
for _n, _s in enumerate(("[1;2P", "[1;2Q", "[1;2R", "[1;2S", "[15;2~", "[17;2~", "[18;2~", "[19;2~", "[20;2~"), 1):
    SEQUENCES[_s] = fkey(12 + _n)                       # Shift+F1..F9 are F13..F21, as on a 5250
SEQUENCES.update({"[A": curses.KEY_UP, "[B": curses.KEY_DOWN, "[C": curses.KEY_RIGHT, "[D": curses.KEY_LEFT,
                  "OA": curses.KEY_UP, "OB": curses.KEY_DOWN, "OC": curses.KEY_RIGHT, "OD": curses.KEY_LEFT,
                  "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE, "[3~": curses.KEY_DC, "[Z": curses.KEY_BTAB})


class Terminal:
    """One 80x24-or-larger window in the 5250 manner. `message` is drawn on every frame
    until a screen clears it."""

    def __init__(self, win):
        self.win = win
        self.message = ""
        self.repaint = False            # a Ctrl+L typed during a wait, for after it
        self.attr = {"normal": 0, "bright": curses.A_BOLD, "field": curses.A_UNDERLINE, "error": curses.A_BOLD,
                     "grey": curses.A_DIM, "cursor": curses.A_REVERSE, "legend": 0}
        try:
            if curses.has_colors():
                curses.start_color()
                for n, fg in enumerate((curses.COLOR_GREEN, curses.COLOR_WHITE, curses.COLOR_RED, curses.COLOR_BLUE,
                                        curses.COLOR_CYAN), 1):
                    curses.init_pair(n, fg, curses.COLOR_BLACK)
                p = curses.color_pair
                self.attr = {"normal": p(1), "bright": p(2) | curses.A_BOLD, "field": p(1) | curses.A_UNDERLINE,
                             "error": p(3) | curses.A_BOLD, "grey": p(4) | curses.A_BOLD, "cursor": p(1) | curses.A_REVERSE,
                             "legend": p(5)}
                win.bkgd(" ", p(1))
        except curses.error:
            pass
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        win.keypad(True)

    @property
    def size(self):
        h, w = self.win.getmaxyx()
        return w, h

    def put(self, y, x, text, attr="normal"):
        """Write without raising: clipped at the edge, the last cell skipped; a line break in
        the text (a region map is several lines) is a space, never a jump to the next row."""
        w, h = self.size
        if not (0 <= y < h) or x >= w:
            return
        text = text.replace("\r", " ").replace("\n", " ")
        try:
            self.win.addnstr(y, x, text, max(0, w - x - 1), self.attr.get(attr, attr) if isinstance(attr, str) else attr)
        except curses.error:
            pass

    def frame(self, title, keys=()):
        """A fresh screen: the title line, the message line and the function-key rows."""
        w, h = self.size
        self.win.erase()
        now = datetime.datetime.now().strftime("%d/%m/%y  %H:%M:%S")
        self.put(0, 1, "MCCONTROL")
        self.put(0, max(11, (w - len(title)) // 2), title, "bright")
        self.put(0, w - len(now) - 1, now)
        if self.message:
            self.put(h - 3, 1, self.message[: w - 2], "bright")
        for i, line in enumerate(list(keys)[-2:][::-1]):
            self.put(h - 1 - i, 1, line, "legend")

    def move(self, y, x):
        """The hardware cursor, where a 5250 keeps it: in the field being typed into."""
        try:
            self.win.move(y, x)
        except curses.error:
            pass

    def say(self, text):
        """The message line, at once, without redrawing the rest: a wait's progress."""
        w, h = self.size
        self.put(h - 3, 1, text[: w - 2].ljust(w - 2), "bright")
        self.flush()

    def flush(self):
        try:
            self.win.refresh()
        except curses.error:
            pass

    def key(self):
        """One key, after the frame is on the terminal. Ctrl+L repaints and is not returned;
        a resize is returned as KEY_RESIZE, for the screen to draw itself again at the new
        size; Esc then a digit is Alt+digit, returned negative; an unknown sequence is drained."""
        if self.too_small():            # checked on every read: a shrink mid-session included
            self.wait_for_room()
            return curses.KEY_RESIZE
        while True:
            self.flush()
            key = self.win.getch()
            if key == curses.KEY_RESIZE:
                self.resized()
                return key
            if key == 12:
                self.win.clearok(True)
                continue
            if key != 27:
                return key
            self.win.nodelay(True)
            try:
                nxt = self.win.getch()
                if nxt == -1:
                    return 27
                if nxt >= 256:
                    curses.ungetch(nxt)
                    return 27
                if nxt not in (ord("["), ord("O")):
                    return -nxt if 32 <= nxt < 127 else 27
                tail = [nxt]
                while True:
                    nxt = self.win.getch()
                    if nxt == -1:
                        break
                    tail.append(nxt)
            finally:
                self.win.nodelay(False)
            return SEQUENCES.get(bytes(tail).decode("latin-1"), 27)

    def too_small(self):
        w, h = self.size
        return w < MIN_WIDTH or h < MIN_HEIGHT

    def wait_for_room(self):
        """Below 80x24 no screen is drawn, only what it needs, until the terminal grows back;
        keys typed meanwhile are dropped. The caller then draws its screen again."""
        while self.too_small():
            w, h = self.size
            self.win.erase()
            for i, line in enumerate(fmt_small(w, h)):
                self.put(i, 0, line)
            self.flush()
            if self.win.getch() == curses.KEY_RESIZE:
                self.resized()
        self.win.clearok(True)

    def resized(self):
        """After KEY_RESIZE: take the size from the terminal itself. curses may keep the
        LINES and COLUMNS it put in the environment at start, and then never grow."""
        try:
            cols, rows = os.get_terminal_size(sys.__stdout__.fileno())
            if (rows, cols) != self.win.getmaxyx():
                curses.resizeterm(rows, cols)
        except (OSError, ValueError, AttributeError, curses.error):
            pass
        self.win.clearok(True)

    def poll(self):
        """F3, F12 or Esc if one was typed, else -1: how a wait notices a cancel. Any other
        key typed during a wait is dropped: what follows the wait is another screen, and a
        key meant for this one must never answer that one. A Ctrl+L is kept for after it."""
        found = -1
        for key in self._pending():
            if key in EXIT:
                found = key
        return found

    def discard(self):
        """Drop what was typed ahead, so the next key read is a fresh one: before a Confirm
        screen, and when a wait ends. A Ctrl+L among it repaints."""
        self._pending()
        if self.repaint:
            self.repaint = False
            self.win.clearok(True)

    def _pending(self):
        keys = []
        while True:
            self.win.nodelay(True)
            try:
                key = self.win.getch()
            finally:
                self.win.nodelay(False)
            if key == -1:
                return keys
            if key == 27:
                curses.ungetch(27)
                key = self.key()
            if key == 12:
                self.repaint = True
            elif key == curses.KEY_RESIZE:
                self.resized()
            else:
                keys.append(key)

    def edit(self, y, x, width, value="", secret=False, recall=None):
        """One input field: type, Backspace, Left/Right do nothing clever. Returns `(key,
        value)` with the key that left it: Enter, Tab, Up, Down, a function key, Esc. With
        `recall`, Up and Down step through those earlier values instead of leaving."""
        buf = value
        pos = len(recall or ())
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        try:
            while True:
                shown = ("*" * len(buf) if secret else buf)[-width:]
                self.put(y, x, shown.ljust(width), "field")
                self.move(y, min(x + len(shown), x + width - 1))
                key = self.key()
                if key in BACKSPACE:
                    buf = buf[:-1]
                elif recall and key in (curses.KEY_UP, curses.KEY_DOWN):
                    pos = max(0, pos - 1) if key == curses.KEY_UP else min(len(recall), pos + 1)
                    buf = recall[pos] if pos < len(recall) else ""
                elif 32 <= key < 127:
                    if len(buf) < width or width >= 40:
                        buf += chr(key)
                else:
                    return key, buf
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass


ESCDELAY_MS = 25


def escdelay(env=None):
    """How long curses waits after Esc for the rest of a key's sequence, in ms: 25, ample on a
    local terminal; MCCONTROL_ESCDELAY (whole ms) sets a longer one for a slow ssh link. An
    inherited ESCDELAY (often 1000) is not taken: it is what makes Esc feel caught late."""
    value = (os.environ if env is None else env).get("MCCONTROL_ESCDELAY", "").strip()
    return int(value) if value.isdigit() else ESCDELAY_MS


def start(main):
    """Open the terminal and run `main(term)`; say why when it cannot."""
    if not sys.stdout.isatty():
        print("mccontrol: the screens need a terminal; use the subcommands instead (mccontrol --help)", file=sys.stderr)
        return 2
    os.environ["ESCDELAY"] = str(escdelay())

    def _main(win):
        term = Terminal(win)
        w, h = term.size
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            term.put(0, 0, f"This needs {MIN_WIDTH}x{MIN_HEIGHT}; the terminal is {w}x{h}.")
            term.win.getch()
            return 2
        return main(term)

    try:
        return curses.wrapper(_main)
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot run the screens: {exc}", file=sys.stderr)
        return 2
