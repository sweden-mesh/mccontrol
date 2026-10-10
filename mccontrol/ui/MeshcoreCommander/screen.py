"""The terminal: colours, safe drawing, box frames, and keys read one decision at a time."""

import curses
import os

KEY_ENTER = (curses.KEY_ENTER, 10, 13)
KEY_BACKSPACE = (curses.KEY_BACKSPACE, 127, 8)
EXIT_KEYS = (curses.KEY_F3, curses.KEY_F12, 27)        # F3 and F12 as the first interface had them; Esc

# Double-line frame, as Turbo Vision drew every window; falls back to ASCII on a terminal
# that cannot show the box characters.
BOX = {"tl": "╔", "tr": "╗", "bl": "╚", "br": "╝", "h": "═", "v": "║"}
BOX_ASCII = {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"}
SINGLE = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}


def is_enter(key):
    return key in KEY_ENTER


# Function keys the terminal's description did not cover, so a PuTTY, a Windows console or a
# tmux with a mismatched TERM still gets F1..F12 and their Shift forms. The sequence is what
# follows the Esc; the value is what curses would have returned under the right TERM.
SEQUENCES = {}
for _n, _s in enumerate(("OP", "OQ", "OR", "OS", "[15~", "[17~", "[18~", "[19~", "[20~", "[21~", "[23~", "[24~"), 1):
    SEQUENCES[_s] = curses.KEY_F0 + _n
for _n, _s in enumerate(("[11~", "[12~", "[13~", "[14~"), 1):                 # VT220 / PuTTY F1..F4
    SEQUENCES[_s] = curses.KEY_F0 + _n
for _n, _s in enumerate(("[1;2P", "[1;2Q", "[1;2R", "[1;2S", "[15;2~", "[17;2~", "[18;2~", "[19;2~", "[20;2~", "[21;2~"), 1):
    SEQUENCES[_s] = curses.KEY_F0 + 12 + _n                                   # Shift+F1..F10 (xterm)
for _n, _s in enumerate(("[23~", "[24~", "[25~", "[26~", "[28~", "[29~", "[31~", "[32~", "[33~", "[34~"), 1):
    SEQUENCES.setdefault(_s, curses.KEY_F0 + 12 + _n)                         # Shift+F1..F10 (VT220 / PuTTY)
for _n, _s in enumerate(("[1;3P", "[1;3Q", "[1;3R", "[1;3S"), 1):
    SEQUENCES[_s] = curses.KEY_F0 + 48 + _n                                   # Alt+F1..F4 (xterm)
SEQUENCES.update({"[A": curses.KEY_UP, "[B": curses.KEY_DOWN, "[C": curses.KEY_RIGHT, "[D": curses.KEY_LEFT,
                  "OA": curses.KEY_UP, "OB": curses.KEY_DOWN, "OC": curses.KEY_RIGHT, "OD": curses.KEY_LEFT,
                  "[H": curses.KEY_HOME, "[F": curses.KEY_END, "[1~": curses.KEY_HOME, "[4~": curses.KEY_END,
                  "[2~": curses.KEY_IC, "[3~": curses.KEY_DC, "[5~": curses.KEY_PPAGE, "[6~": curses.KEY_NPAGE,
                  "[Z": curses.KEY_BTAB})


class Colors:
    """The Turbo Pascal scheme, as curses attribute bundles: a black menu bar and key bar
    with red hot letters, a blue work area, grey dialogs, green for the open menu's cursor.
    Monochrome is a valid terminal: every attribute degrades to normal, bold or reverse."""

    PAIRS = [
        (1, curses.COLOR_WHITE, curses.COLOR_BLUE),     # desktop, panel body
        (2, curses.COLOR_WHITE, curses.COLOR_BLACK),    # menu bar, key bar labels
        (3, curses.COLOR_YELLOW, curses.COLOR_BLUE),    # tagged rows, active title
        (4, curses.COLOR_BLACK, curses.COLOR_WHITE),    # dialog body, pull-down body
        (5, curses.COLOR_WHITE, curses.COLOR_RED),      # errors, cautions
        (6, curses.COLOR_WHITE, curses.COLOR_GREEN),    # default button, ok
        (7, curses.COLOR_RED, curses.COLOR_BLACK),      # hot letters, key bar numbers
        (8, curses.COLOR_YELLOW, curses.COLOR_BLACK),   # message line
        (9, curses.COLOR_BLACK, curses.COLOR_CYAN),     # list cursor bar
        (10, curses.COLOR_BLACK, curses.COLOR_GREEN),   # open menu title and item
        (11, curses.COLOR_BLACK, curses.COLOR_WHITE),   # disabled text: drawn bold = bright black on grey
    ]

    def __init__(self):
        self.ok = False
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                for n, fg, bg in self.PAIRS:
                    curses.init_pair(n, fg, bg)
                self.ok = True
        except curses.error:
            self.ok = False

    def _pair(self, n, fallback=0):
        return curses.color_pair(n) if self.ok else fallback

    @property
    def desktop(self):
        return self._pair(1)

    @property
    def bar(self):
        return self._pair(2, curses.A_NORMAL)

    @property
    def hot(self):
        return self._pair(7, curses.A_BOLD) | curses.A_BOLD

    @property
    def tagged(self):
        return self._pair(3, curses.A_BOLD) | curses.A_BOLD

    @property
    def dialog(self):
        return self._pair(4, curses.A_REVERSE)

    @property
    def error(self):
        return self._pair(5, curses.A_REVERSE | curses.A_BOLD)

    @property
    def default_button(self):
        return self._pair(6, curses.A_REVERSE | curses.A_BOLD)

    @property
    def key_number(self):
        return self.hot

    @property
    def message(self):
        return self._pair(8, curses.A_BOLD) | curses.A_BOLD

    @property
    def cursor(self):
        return self._pair(9, curses.A_REVERSE)

    @property
    def menu_cursor(self):
        return self._pair(10, curses.A_REVERSE)

    @property
    def disabled(self):
        """Turbo Vision's disabled item: dark grey on the dialog's grey. A_DIM alone is not
        it -- many terminals ignore it, and on a light background it shows nothing."""
        return self._pair(11, curses.A_DIM) | curses.A_BOLD


class Screen:
    MIN_WIDTH, MIN_HEIGHT = 80, 24

    def __init__(self, win):
        self.win = win
        self.colors = Colors()
        self.box = BOX
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        win.keypad(True)
        try:
            win.bkgd(" ", self.colors.desktop)
        except curses.error:
            pass
        # The mouse where the terminal offers one; nothing needs it. A click arrives as
        # KEY_MOUSE from key(), with its position in `self.mouse`.
        self.mouse = None
        try:
            curses.mousemask(curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_RELEASED)
        except (curses.error, AttributeError):
            pass

    # -- geometry ----------------------------------------------------------------------

    @property
    def size(self):
        h, w = self.win.getmaxyx()
        return w, h

    def too_small(self):
        w, h = self.size
        return w < self.MIN_WIDTH or h < self.MIN_HEIGHT

    # -- drawing -----------------------------------------------------------------------

    def put(self, y, x, text, attr=0):
        """Write without ever raising: off-screen text is clipped, the last cell is skipped; a
        line break in the text (a region map is several lines) is a space, never a new row."""
        w, h = self.size
        if not (0 <= y < h) or x >= w:
            return
        text = text.replace("\r", " ").replace("\n", " ")
        if x < 0:
            text, x = text[-x:], 0
        try:
            self.win.addnstr(y, x, text, max(0, w - x - 1), attr)
        except curses.error:
            pass

    def fill(self, y, x, width, height, attr):
        for row in range(y, y + height):
            self.put(row, x, " " * width, attr)

    def frame(self, y, x, width, height, attr, title="", title_attr=None, box=None):
        """A double-line box with a centred title in the top edge."""
        b = box or self.box
        try:
            self.put(y, x, b["tl"] + b["h"] * (width - 2) + b["tr"], attr)
            for row in range(y + 1, y + height - 1):
                self.put(row, x, b["v"], attr)
                self.put(row, x + width - 1, b["v"], attr)
            self.put(y + height - 1, x, b["bl"] + b["h"] * (width - 2) + b["br"], attr)
        except Exception:
            self.box = BOX_ASCII
            return self.frame(y, x, width, height, attr, title, title_attr, BOX_ASCII)
        if title:
            label = f" {title} "
            self.put(y, x + max(1, (width - len(label)) // 2), label, title_attr or attr)

    def shadow(self, y, x, width, height):
        """Turbo Vision's drop shadow: one row below and two columns right, darkened."""
        attr = curses.A_DIM if not self.colors.ok else curses.color_pair(0) | curses.A_DIM
        self.put(y + height, x + 2, " " * width, attr)
        for row in range(y + 1, y + height + 1):
            self.put(row, x + width, "  ", attr)

    def refresh(self):
        """Compose into curses' virtual screen only. The physical terminal is written once
        per frame -- in key(), just before it waits, and in Progress.tick() -- so a dialog
        drawn over the work after the work was redrawn reaches the terminal as one frame,
        never as the work first and the dialog a moment later. That second write was the
        flicker (planning/mccontrol/stable-screen.md in the workspace)."""
        self.win.noutrefresh()

    def flush(self):
        """Write the composed frame to the terminal: what changed since the last one."""
        try:
            curses.doupdate()
        except curses.error:
            pass

    def repaint(self):
        """Redraw every cell on the next refresh, not only what changed."""
        try:
            self.win.clearok(True)
        except curses.error:
            pass

    def erase(self):
        self.win.erase()

    # -- keys --------------------------------------------------------------------------

    def key(self):
        """One key. An Esc that a terminal sends as the start of a function-key sequence the
        library did not recognise is drained, so its tail never lands in a field."""
        self.flush()
        key = self.win.getch()
        if key == curses.KEY_MOUSE:
            try:
                _id, x, y, _z, state = curses.getmouse()
            except curses.error:
                return self.key()
            if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED):
                self.mouse = (y, x)
                return key
            return self.key()                               # a release on its own: nothing
        if key != 27:
            return key
        self.win.nodelay(True)
        try:
            nxt = self.win.getch()
            if nxt == -1:                                   # a plain Esc
                return 27
            if nxt >= 256:                                  # a key of its own pressed right after Esc
                curses.ungetch(nxt)
                return 27
            tail = [nxt]
            if nxt not in (ord("["), ord("O")):             # Alt+letter: Esc then the letter
                return -nxt if 32 <= nxt < 127 else 27
            while True:                                     # an unrecognised sequence: drain it
                nxt = self.win.getch()
                if nxt == -1:
                    break
                tail.append(nxt)
        finally:
            self.win.nodelay(False)
        return SEQUENCES.get(bytes(tail).decode("latin-1"), 27)

    def cursor(self, visible):
        try:
            curses.curs_set(1 if visible else 0)
        except curses.error:
            pass


def is_exit(key):
    return key in EXIT_KEYS


ESCDELAY_MS = 25


def escdelay(env=None):
    """How long curses waits after Esc for the rest of a key's sequence, in ms: 25, ample on a
    local terminal; MCCONTROL_ESCDELAY (whole ms) sets a longer one for a slow ssh link. An
    inherited ESCDELAY (often 1000) is not taken: it is what makes Esc feel caught late."""
    value = (os.environ if env is None else env).get("MCCONTROL_ESCDELAY", "").strip()
    return int(value) if value.isdigit() else ESCDELAY_MS


def run(main, client):
    """Open the terminal, run `main(screen, client)`, and say why when it cannot."""
    import sys

    if not sys.stdout.isatty():
        print("mccontrol: the screens need a terminal; use the subcommands instead "
              "(mccontrol --help)", file=sys.stderr)
        return 2
    os.environ["ESCDELAY"] = str(escdelay())

    def _main(win):
        screen = Screen(win)
        if screen.too_small():
            w, h = screen.size
            win.addstr(0, 0, f"This needs {Screen.MIN_WIDTH}x{Screen.MIN_HEIGHT}; the terminal is "
                             f"{w}x{h}. Make it larger and start again, or use the command line.")
            win.getch()
            return 2
        return main(screen, client)

    try:
        return curses.wrapper(_main)
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot run the screens: "
              f"{exc}. Use the subcommands instead (mccontrol --help).", file=sys.stderr)
        return 2
