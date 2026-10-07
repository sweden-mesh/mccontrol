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


class Colors:
    """The sixteen-colour scheme of the era, as curses attribute bundles. Monochrome is a
    valid terminal: every attribute degrades to normal or reverse video."""

    def __init__(self):
        self.ok = False
        try:
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                pairs = [
                    (1, curses.COLOR_WHITE, curses.COLOR_BLUE),     # desktop, panel body
                    (2, curses.COLOR_BLACK, curses.COLOR_CYAN),     # menu bar, key labels, cursor bar
                    (3, curses.COLOR_YELLOW, curses.COLOR_BLUE),    # tagged rows, active title
                    (4, curses.COLOR_BLACK, curses.COLOR_WHITE),    # dialog body
                    (5, curses.COLOR_WHITE, curses.COLOR_RED),      # errors, cautions
                    (6, curses.COLOR_WHITE, curses.COLOR_GREEN),    # default button, ok
                    (7, curses.COLOR_BLACK, curses.COLOR_WHITE),    # key bar numbers
                    (8, curses.COLOR_YELLOW, curses.COLOR_BLACK),   # message line
                ]
                for n, fg, bg in pairs:
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
        return self._pair(2, curses.A_REVERSE)

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
        return self._pair(7, curses.A_NORMAL)

    @property
    def message(self):
        return self._pair(8, curses.A_BOLD) | curses.A_BOLD

    @property
    def cursor(self):
        return self._pair(2, curses.A_REVERSE)


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
        """Write without ever raising: off-screen text is clipped, the last cell is skipped."""
        w, h = self.size
        if not (0 <= y < h) or x >= w:
            return
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
        self.win.refresh()

    def erase(self):
        self.win.erase()

    # -- keys --------------------------------------------------------------------------

    def key(self):
        """One key. An Esc that a terminal sends as the start of a function-key sequence the
        library did not recognise is drained, so its tail never lands in a field."""
        key = self.win.getch()
        if key == 27:
            self.win.nodelay(True)
            try:
                tail = []
                while True:
                    nxt = self.win.getch()
                    if nxt == -1:
                        break
                    tail.append(nxt)
            finally:
                self.win.nodelay(False)
            # Alt+letter arrives as Esc then the letter: hand it back as a negative code.
            if len(tail) == 1 and 32 <= tail[0] < 127:
                return -tail[0]
            return 27
        return key

    def cursor(self, visible):
        try:
            curses.curs_set(1 if visible else 0)
        except curses.error:
            pass


def is_exit(key):
    return key in EXIT_KEYS


def run(main, store):
    """Open the terminal, run `main(screen, store)`, and say why when it cannot."""
    import sys

    if not sys.stdout.isatty():
        print("mccontrol: the screens need a terminal; use the subcommands instead "
              "(mccontrol --help)", file=sys.stderr)
        return 2
    os.environ.setdefault("ESCDELAY", "150")

    def _main(win):
        screen = Screen(win)
        if screen.too_small():
            w, h = screen.size
            win.addstr(0, 0, f"This needs {Screen.MIN_WIDTH}x{Screen.MIN_HEIGHT}; the terminal is "
                             f"{w}x{h}. Make it larger and start again, or use the command line.")
            win.getch()
            return 2
        return main(screen, store)

    try:
        return curses.wrapper(_main)
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot run the screens: "
              f"{exc}. Use the subcommands instead (mccontrol --help).", file=sys.stderr)
        return 2
