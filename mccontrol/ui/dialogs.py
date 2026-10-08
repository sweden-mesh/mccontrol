"""Modal dialogs in the Turbo Vision manner: a framed box with labelled fields and buttons.

Tab and Shift+Tab move between controls, Enter triggers the default button from anywhere
(or, on a field, moves on when there is no default), Esc cancels. A `Field` edits a line,
`Choice` is a field with an Up/Down history, `Button` runs or returns a value.
"""

import curses

from .screen import KEY_BACKSPACE, KEY_ENTER


class Field:
    def __init__(self, label, value="", width=30, secret=False, history=None, hotkey=None):
        self.label, self.value, self.width, self.secret = label, value, width, secret
        self.history = list(history or [])
        self._hist = len(self.history)
        self.hotkey = (hotkey or (label[:1] if label else "")).lower()

    def shown(self):
        return "*" * len(self.value) if self.secret else self.value

    def handle(self, key):
        if key in KEY_BACKSPACE:
            self.value = self.value[:-1]
        elif key == curses.KEY_UP and self.history:
            self._hist = max(0, self._hist - 1)
            self.value = self.history[self._hist]
        elif key == curses.KEY_DOWN and self.history:
            self._hist = min(len(self.history), self._hist + 1)
            self.value = self.history[self._hist] if self._hist < len(self.history) else ""
        elif 32 <= key < 127 and len(self.value) < self.width:
            self.value += chr(key)
        else:
            return False
        return True


class Button:
    def __init__(self, label, result, default=False):
        self.label, self.result, self.default = label, result, default


class Dialog:
    """Lay out and run a dialog. `run(screen)` returns the default/pressed button's result,
    or None on Esc. Field values are read back from the Field objects."""

    def __init__(self, title, fields=(), buttons=(), body=(), width=60, caution=""):
        self.title = title
        self.fields = list(fields)
        self.buttons = list(buttons) or [Button("OK", True, default=True), Button("Cancel", None)]
        self.body = list(body)
        self.caution = caution
        self.width = width
        self.focus = 0                      # index into fields + buttons
        self.message = ""

    @property
    def controls(self):
        return self.fields + self.buttons

    @property
    def height(self):
        return 4 + len(self.body) + (2 if self.caution else 0) + len(self.fields) * 1 + (1 if self.fields else 0) + 2

    def _default(self):
        for b in self.buttons:
            if b.default:
                return b
        return self.buttons[0]

    def draw(self, screen):
        w, h = screen.size
        width, height = min(self.width, w - 4), self.height
        y, x = max(1, (h - height) // 2), max(2, (w - width) // 2)
        c = screen.colors
        screen.shadow(y, x, width, height)
        screen.fill(y, x, width, height, c.dialog)
        screen.frame(y, x, width, height, c.dialog, self.title)
        row = y + 2
        for line in self.body:
            screen.put(row, x + 2, line[: width - 4], c.dialog)
            row += 1
        if self.caution:
            screen.put(row, x + 2, (" " + self.caution)[: width - 4].ljust(width - 4), c.error)
            row += 2
        if self.body and not self.caution:
            row += 1
        label_w = max((len(f.label) for f in self.fields), default=0) + 2
        for i, f in enumerate(self.fields):
            focused = self.focus == i
            screen.put(row, x + 2, f"{f.label}:".ljust(label_w), c.dialog)
            box_w = min(f.width, width - 4 - label_w)
            screen.put(row, x + 2 + label_w, f.shown()[-box_w:].ljust(box_w),
                       c.bar if focused else (c.dialog | curses.A_UNDERLINE))
            if focused:
                screen.cursor(True)
                try:
                    screen.win.move(row, x + 2 + label_w + min(len(f.shown()), box_w - 1))
                except curses.error:
                    pass
            row += 1
        row = y + height - 2
        total = sum(len(b.label) + 6 for b in self.buttons)
        bx = x + max(2, (width - total) // 2)
        for j, b in enumerate(self.buttons):
            focused = self.focus == len(self.fields) + j
            attr = c.bar if focused else (c.default_button if b.default else c.dialog)
            screen.put(row, bx, f"[ {b.label} ]", attr | (curses.A_BOLD if b.default else 0))
            bx += len(b.label) + 6
        if self.message:
            screen.put(y + height - 1, x + 2, f" {self.message[: width - 6]} ", c.error)
        if not (self.fields and self.focus < len(self.fields)):
            screen.cursor(False)

    def run(self, screen, redraw_under=None):
        while True:
            if redraw_under:
                redraw_under()
            self.draw(screen)
            screen.refresh()
            key = screen.key()
            if key == 27:
                screen.cursor(False)
                return None
            if key == 9 or key == curses.KEY_DOWN and self.focus >= len(self.fields):
                self.focus = (self.focus + 1) % len(self.controls)
                continue
            if key == curses.KEY_BTAB or key == curses.KEY_UP and self.focus >= len(self.fields):
                self.focus = (self.focus - 1) % len(self.controls)
                continue
            if key < 0 and -key < 128:                       # Alt+letter jumps to a field
                letter = chr(-key).lower()
                for i, f in enumerate(self.fields):
                    if f.hotkey == letter:
                        self.focus = i
                continue
            if self.focus < len(self.fields):
                field = self.fields[self.focus]
                if key in KEY_ENTER:
                    screen.cursor(False)
                    return self._default().result
                if field.handle(key):
                    continue
                if key in (curses.KEY_LEFT, curses.KEY_RIGHT):
                    continue
            else:
                button = self.buttons[self.focus - len(self.fields)]
                if key in KEY_ENTER or key == 32:
                    screen.cursor(False)
                    return button.result
                if key == curses.KEY_LEFT:
                    self.focus = max(len(self.fields), self.focus - 1)
                elif key == curses.KEY_RIGHT:
                    self.focus = min(len(self.controls) - 1, self.focus + 1)
            self.message = ""


def confirm(screen, title, body, caution, yes="Send anyway", no="Cancel", redraw_under=None):
    """A yes/no dialog with the caution sentence in red; Esc and `no` both return False."""
    d = Dialog(title, body=body, caution=caution,
               buttons=[Button(yes, True), Button(no, False, default=True)])
    d.focus = len(d.fields) + 1
    return bool(d.run(screen, redraw_under))


def notice(screen, title, lines, redraw_under=None):
    d = Dialog(title, body=lines, buttons=[Button("OK", True, default=True)], width=max(40, min(76, max((len(l) for l in lines), default=20) + 6)))
    d.focus = 0
    return d.run(screen, redraw_under)


class Progress:
    """A wait, the early-90s way: a framed box over the work with what is happening, a
    second line with the running count, and a bar that fills over the wait. Esc stops the
    wait early and keeps what has come.

        p = Progress(screen, "Discovering", "Asking every node in JKG with one signed read", redraw)
        stop = p.tick("Found 2 new, 1 old", 0.4)     # draws; True when Esc was pressed
        p.close()
    """

    WIDTH = 60

    def __init__(self, screen, title, line, redraw_under=None):
        self.screen, self.title, self.line, self.redraw_under = screen, title, line, redraw_under
        self.status = ""
        self.cancelled = False

    def tick(self, status, fraction, elapsed=None, total=None):
        self.status = status
        if self.redraw_under:
            self.redraw_under()
        self.draw(fraction, elapsed, total)
        self.screen.refresh()
        self.screen.win.nodelay(True)
        try:
            while True:
                k = self.screen.win.getch()
                if k == -1:
                    break
                if k == 27:
                    self.cancelled = True
        finally:
            self.screen.win.nodelay(False)
        return self.cancelled

    def draw(self, fraction, elapsed=None, total=None):
        w, h = self.screen.size
        width, height = min(self.WIDTH, w - 4), 7
        y, x = max(1, (h - height) // 2), max(2, (w - width) // 2)
        c = self.screen.colors
        self.screen.shadow(y, x, width, height)
        self.screen.fill(y, x, width, height, c.dialog)
        self.screen.frame(y, x, width, height, c.dialog, self.title)
        inner = width - 4
        self.screen.put(y + 1, x + 2, self.line[:inner], c.dialog)
        self.screen.put(y + 2, x + 2, self.status[:inner], c.dialog | curses.A_BOLD)
        bar = inner - 8
        filled = int(round(bar * max(0.0, min(1.0, fraction))))
        clock = f"{int(elapsed):>2}/{int(total)}s" if total else ""
        self.screen.put(y + 4, x + 2, "[" + "█" * filled + "░" * (bar - filled) + "] " + clock, c.dialog)
        self.screen.put(y + 5, x + 2, "Esc stops waiting and keeps what has come"[:inner], c.dialog | curses.A_DIM)

    def close(self):
        if self.redraw_under:
            self.redraw_under()
            self.screen.refresh()


def waiting(screen, title, line, redraw_under, describe):
    """A `progress` callback for the transport, drawn as a Progress box. `describe(nodes)`
    turns the nodes answered so far into the status line. Returns `(callback, box)`."""
    box = Progress(screen, title, line, redraw_under)

    def progress(elapsed, total, nodes):
        return box.tick(describe(nodes), elapsed / total if total else 1.0, elapsed, total)
    return progress, box
