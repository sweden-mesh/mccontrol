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
        self.screen.flush()                       # one frame: the work and the box together
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


class ListBox:
    """A framed list over the work: items under group headers, a filter line that narrows
    as you type, Up/Down/PgUp/PgDn, Enter picks, Esc closes. `items` is
    `[(group, [(label, value), ...]), ...]`; `run` returns the picked value or None.

    Headers are not selectable; the cursor skips them. The filter matches the label, case-
    insensitive, and keeps the headers of the groups that still have a match."""

    def __init__(self, title, items, width=60, height=18, hint="Type to filter  Enter=run  Esc=close"):
        self.title, self.items, self.width, self.height, self.hint = title, items, width, height, hint
        self.filter = ""
        self.cursor = 0
        self.top = 0
        self.note = ""                 # one line above the hint: why the greyed entries are grey
        self.rows = []                 # (kind, text, value): kind is "head", "item" or "grey"
        self._rebuild()

    def _rebuild(self):
        f = self.filter.lower()
        rows = []
        for group, entries in self.items:
            hits = [e for e in entries if f in e[0].lower()]
            if hits:
                rows.append(("head", group, None))
                # an entry may carry a third element: a reason it is greyed out (drawn dim,
                # skipped by the cursor like a header; Enter on it says the reason)
                rows += [("grey" if len(e) > 2 and e[2] else "item", e[0], e[2] if len(e) > 2 and e[2] else e[1])
                         for e in hits]
        self.rows = rows
        self.cursor = min(self.cursor, max(0, len(rows) - 1))
        self._settle(+1)

    def _settle(self, direction):
        """Keep the cursor off headers, moving in `direction`; onto nothing when all headers."""
        n = len(self.rows)
        if not n:
            self.cursor = 0
            return
        steps = 0
        while self.rows[self.cursor][0] in ("head", "grey") and steps < n:
            self.cursor = (self.cursor + direction) % n
            steps += 1
        page = self.height - 6
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + page:
            self.top = self.cursor - page + 1

    def move(self, delta):
        n = len(self.rows)
        if not n:
            return
        self.cursor = max(0, min(n - 1, self.cursor + delta))
        self._settle(1 if delta > 0 else -1)

    def draw(self, screen):
        w, h = screen.size
        width, height = min(self.width, w - 4), min(self.height, h - 2)
        y, x = max(1, (h - height) // 2), max(2, (w - width) // 2)
        c = screen.colors
        screen.shadow(y, x, width, height)
        screen.fill(y, x, width, height, c.dialog)
        screen.frame(y, x, width, height, c.dialog, self.title)
        inner = width - 4
        screen.put(y + 1, x + 2, ("Filter: " + self.filter)[:inner].ljust(inner), c.dialog | curses.A_BOLD)
        page = height - 6
        for i in range(page):
            idx = self.top + i
            if idx >= len(self.rows):
                break
            kind, text, _v = self.rows[idx]
            if kind == "head":
                screen.put(y + 3 + i, x + 2, f"-- {text} --".ljust(inner)[:inner], c.dialog | curses.A_DIM)
            elif kind == "grey":
                screen.put(y + 3 + i, x + 2, ("  " + text).ljust(inner)[:inner], c.dialog | curses.A_DIM)
            else:
                attr = c.cursor if idx == self.cursor else c.dialog
                screen.put(y + 3 + i, x + 2, ("  " + text).ljust(inner)[:inner], attr)
        screen.put(y + height - 2, x + 2, self.hint[:inner], c.dialog | curses.A_DIM)
        if self.note:
            screen.put(y + height - 3, x + 2, self.note[:inner].ljust(inner), c.dialog | curses.A_BOLD)

    def run(self, screen, redraw_under=None):
        while True:
            if redraw_under:
                redraw_under()
            self.draw(screen)
            screen.refresh()
            key = screen.key()
            if key == 27:
                return None
            if key in KEY_ENTER:
                if self.rows and self.rows[self.cursor][0] == "item":
                    return self.rows[self.cursor][2]
                continue
            if key == curses.KEY_DOWN:
                self.move(+1)
            elif key == curses.KEY_UP:
                self.move(-1)
            elif key == curses.KEY_NPAGE:
                self.move(self.height - 6)
            elif key == curses.KEY_PPAGE:
                self.move(-(self.height - 6))
            elif key == curses.KEY_HOME:
                self.cursor = 0
                self._settle(+1)
            elif key == curses.KEY_END:
                self.cursor = max(0, len(self.rows) - 1)
                self._settle(-1)
            elif key in KEY_BACKSPACE:
                self.filter = self.filter[:-1]
                self._rebuild()
            elif 32 <= key < 127:
                self.filter += chr(key)
                self._rebuild()


class ChoiceBox:
    """One of several values as radio buttons, or several as checkboxes (`multi=True`).
    Space toggles, Enter accepts, Esc backs out. `run` returns the value, the list of
    chosen values for multi, or None. The command being built is previewed on the last
    line, so what Enter will send is never a surprise."""

    def __init__(self, command, choices, multi=False, width=50):
        self.command, self.choices, self.multi, self.width = command, list(choices), multi, width
        self.cursor = 0
        self.chosen = set()

    def preview(self):
        if self.multi:
            tail = " ".join(c for c in self.choices if c in self.chosen)
        else:
            tail = self.choices[self.cursor] if self.choices else ""
        return f"{self.command} {tail}".strip()

    def draw(self, screen):
        w, h = screen.size
        height = len(self.choices) + 6
        width = min(self.width, w - 4)
        y, x = max(1, (h - height) // 2), max(2, (w - width) // 2)
        c = screen.colors
        screen.shadow(y, x, width, height)
        screen.fill(y, x, width, height, c.dialog)
        screen.frame(y, x, width, height, c.dialog, self.command)
        inner = width - 4
        for i, choice in enumerate(self.choices):
            if self.multi:
                mark = "[X]" if choice in self.chosen else "[ ]"
            else:
                mark = "(*)" if i == self.cursor else "( )"
            label = choice if choice else "(none)"
            attr = c.cursor if i == self.cursor else c.dialog
            screen.put(y + 1 + i, x + 2, f" {mark} {label}".ljust(inner)[:inner], attr)
        screen.put(y + height - 3, x + 2, ("Sends: " + self.preview())[:inner].ljust(inner), c.dialog | curses.A_BOLD)
        hint = "Space=choose  Enter=run  Esc=back" if self.multi else "Up/Down  Enter=run  Esc=back"
        screen.put(y + height - 2, x + 2, hint[:inner], c.dialog | curses.A_DIM)

    def run(self, screen, redraw_under=None):
        while True:
            if redraw_under:
                redraw_under()
            self.draw(screen)
            screen.refresh()
            key = screen.key()
            if key == 27:
                return None
            if key in KEY_ENTER:
                if self.multi:
                    return [c for c in self.choices if c in self.chosen]
                return self.choices[self.cursor] if self.choices else None
            if key == curses.KEY_DOWN:
                self.cursor = min(len(self.choices) - 1, self.cursor + 1)
            elif key == curses.KEY_UP:
                self.cursor = max(0, self.cursor - 1)
            elif key == 32 and self.choices:
                if self.multi:
                    self.chosen ^= {self.choices[self.cursor]}
                else:
                    return self.choices[self.cursor]
