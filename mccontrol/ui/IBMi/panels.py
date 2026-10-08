"""The three shapes every 5250 screen takes: a list with an Opt column, a form of prompted
fields, and a display of lines. Each draws on a `Terminal` and hands back the key that ended
it; F1 calls the terminal's `on_help` with the screen's context and carries on."""

import curses

from . import fmt
from .terminal import BACKSPACE, ENTER, EXIT, fkey


NAVIGATION = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_BTAB)


def _help(term, context):
    if getattr(term, "on_help", None):
        term.on_help(context)


class OptList:
    """Head lines, "Type options, press Enter." and the legend, a column header, then rows,
    each with a two-position Opt field; and, with `prompt`, the command line under them.

    Rows are `(text, attr, payload)`; a row whose payload is None (a group heading) takes no
    option. Digits type into the cursor row's Opt; any other character goes to the command
    line, and Tab moves between the two. `run()` returns `(key, picks, command)`: the key
    that ended it, the `(opt, payload)` typed, in row order, and the command line."""

    def __init__(self, term, title, keys, legend=(), header="", head=(), prompt="Parameters or command", context="main",
                 ident=None):
        self.term, self.title, self.keys = term, title, keys
        self.legend, self.header, self.head = list(legend), header, list(head)
        self.prompt, self.context = prompt, context
        self.rows, self.opts = [], {}
        self.cursor = self.top = 0
        self.command, self.focus = "", "list"
        self.note = ""                  # right of the instruction line: a count, the sort
        self.ident = ident or (lambda payload: payload)     # what keeps the cursor on a row across a refresh

    def set_rows(self, rows, opts=None):
        keep = None if self.payload() is None else self.ident(self.payload())
        self.rows = list(rows)
        self.opts = dict(opts or {})
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        for i, (_t, _a, p) in enumerate(self.rows):
            if keep is not None and p is not None and self.ident(p) == keep:
                self.cursor = i
        if self.rows and self.rows[self.cursor][2] is None:
            self.cursor = next((i for i, r in enumerate(self.rows) if r[2] is not None), self.cursor)

    def payload(self):
        if self.rows and self.cursor < len(self.rows):
            return self.rows[self.cursor][2]
        return None

    def _layout(self):
        w, h = self.term.size
        y = 2 + len(self.head)
        if self.legend:
            y += 1 + len(self.legend)
        first = y + 1                    # the header, then the rows
        last = h - (7 if self.prompt else 5)
        return first, max(first, last)

    def draw(self):
        t = self.term
        w, h = t.size
        t.frame(self.title, self.keys)
        y = 2
        for line in self.head:
            t.put(y, 1, line)
            y += 1
        if self.legend:
            t.put(y, 1, "Type options, press Enter.")
            y += 1
            for line in self.legend:
                t.put(y, 3, line, "legend")
                y += 1
        if self.note:
            t.put(2 + len(self.head), w - len(self.note) - 2, self.note, "bright")
        t.put(y, 1, ("Opt  " if self.legend else "") + self.header, "bright")
        first, last = self._layout()
        size = last - first + 1
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + size:
            self.top = self.cursor - size + 1
        x = 6 if self.legend else 1
        if not self.rows:
            t.put(first, x, "(No entries)")
        for i, (text, attr, payload) in enumerate(self.rows[self.top:self.top + size]):
            n = self.top + i
            row = first + i
            here = n == self.cursor and self.focus == "list"
            if self.legend and payload is not None:
                t.put(row, 1, self.opts.get(n, "").ljust(2), "cursor" if here else "field")
            t.put(row, x, text[: w - x - 1], "cursor" if here and not self.legend else attr)
        more = "More..." if self.top + size < len(self.rows) else "Bottom"
        t.put(last + 1, w - len(more) - 2, more)
        if self.prompt:
            t.put(h - 6, 1, self.prompt)
            t.put(h - 5, 1, "===>", "bright")
            t.put(h - 5, 6, self.command[-(w - 8):].ljust(w - 8), "field")
        if self.focus == "cmd":
            self.term.win.move(h - 5, 6 + min(len(self.command), w - 9))
        elif self.legend and self.rows:
            self.term.win.move(first + self.cursor - self.top, 1)
        return size

    def run(self):
        while True:
            size = self.draw()
            try:
                curses.curs_set(1 if self.focus == "cmd" or self.legend else 0)
            except curses.error:
                pass
            key = self.term.key()
            if key == fkey(1):
                _help(self.term, self.context)
                continue
            if key in ENTER or key == 27 or key < 0 or (key >= 256 and key not in NAVIGATION and key not in BACKSPACE):
                picks = [(self.opts[i].strip(), self.rows[i][2]) for i in sorted(self.opts)
                         if i < len(self.rows) and self.opts[i].strip()]
                command = self.command.strip()
                if key in ENTER:
                    self.opts, self.command, self.focus = {}, "", "list"
                return key, picks, command
            if self.focus == "cmd":
                if key in BACKSPACE:
                    if self.command:
                        self.command = self.command[:-1]
                    else:
                        self.focus = "list"
                elif key in (curses.KEY_UP, curses.KEY_DOWN, 9, curses.KEY_BTAB):
                    self.focus = "list"
                elif 32 <= key < 127:
                    self.command += chr(key)
                continue
            if key == curses.KEY_DOWN and self.cursor < len(self.rows) - 1:
                self.cursor += 1
            elif key == curses.KEY_UP and self.cursor > 0:
                self.cursor -= 1
            elif key == curses.KEY_NPAGE:
                self.cursor = min(len(self.rows) - 1, self.cursor + size) if self.rows else 0
                self.top = min(self.top + size, max(0, len(self.rows) - 1))
            elif key == curses.KEY_PPAGE:
                self.cursor, self.top = max(0, self.cursor - size), max(0, self.top - size)
            elif key in (9, curses.KEY_BTAB) and self.prompt:
                self.focus = "cmd"
            elif self.legend and 48 <= key <= 57:
                if self.payload() is not None:
                    self.opts[self.cursor] = fmt.opt_typed(self.opts.get(self.cursor, ""), chr(key))
            elif key in BACKSPACE or key == 32:
                self.opts.pop(self.cursor, None)
            elif 32 < key < 127 and self.prompt:
                self.focus = "cmd"
                self.command += chr(key)


def form(term, title, fields, head=(), keys=("F1=Help   F3=Exit   F12=Cancel",), context="main", instruction="Type choices, press Enter.",
         at=0):
    """Prompted fields, one per line: `fields` are dicts with label, value, width and
    optionally secret and recall. Tab and Down move on, Up back; Enter returns
    `(Enter, values)`, any function key `(key, values)`, F3, F12 and Esc `(key, None)`.
    The cursor starts on field `at`."""
    values = [str(f.get("value", "")) for f in fields]
    i = at
    while True:
        w, h = term.size
        term.frame(title, keys)
        y = 2
        for line in head:
            term.put(y, 1, line)
            y += 1
        term.put(y, 1, instruction)
        y += 2
        top = y
        for n, f in enumerate(fields):
            term.put(top + n, 3, fmt.dots(f["label"]))
            shown = "*" * len(values[n]) if f.get("secret") else values[n]
            term.put(top + n, 32, shown[: f["width"]].ljust(min(f["width"], w - 34)), "field")
        f = fields[i]
        key, values[i] = term.edit(top + i, 32, min(f["width"], w - 34), values[i], f.get("secret"), f.get("recall"))
        if key == fkey(1):
            _help(term, context)
        elif key in ENTER:
            return key, values
        elif key in EXIT:
            return key, None
        elif key in (9, curses.KEY_DOWN):
            i = (i + 1) % len(fields)
        elif key in (curses.KEY_BTAB, curses.KEY_UP):
            i = (i - 1) % len(fields)
        elif key >= 256 or key < 0:
            return key, values


def show(term, title, lines, keys=("F3=Exit   F12=Cancel",), done=(), context="main"):
    """Lines to read, paged: PageDown/PageUp roll, Enter, F3, F12 and Esc leave, as does any
    key in `done`. Long lines wrap. Returns the key that left."""
    w, _h = term.size
    wrapped = []
    for line in lines:
        wrapped += fmt.wrap(line, w - 3) if len(line) > w - 3 else [line]
    top = 0
    while True:
        w, h = term.size
        size = h - 6
        term.frame(title, keys)
        for i, line in enumerate(wrapped[top:top + size]):
            term.put(2 + i, 1, line)
        more = "More..." if top + size < len(wrapped) else "Bottom"
        term.put(2 + size, w - len(more) - 2, more)
        key = term.key()
        if key == fkey(1):
            _help(term, context)
        elif key in ENTER or key in EXIT or key in done:
            return key
        elif key == curses.KEY_NPAGE and top + size < len(wrapped):
            top += size
        elif key == curses.KEY_PPAGE:
            top = max(0, top - size)


def confirm(term, title, lines, prompt="Press Enter to confirm, F12 to cancel."):
    """A Confirm screen: True on Enter."""
    w, _h = term.size
    term.frame(title, ["F12=Cancel"])
    term.put(2, 1, prompt, "bright")
    y = 4
    for line in lines:
        for part in fmt.wrap(line, w - 6):
            term.put(y, 3, part)
            y += 1
    while True:
        key = term.key()
        if key in ENTER:
            return True
        if key in EXIT:
            return False
