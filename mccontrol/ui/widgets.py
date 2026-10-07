"""Panels, lists, the key bar and the message line. Each draws from what it is given."""

import curses

from .screen import KEY_ENTER


class Panel:
    """A framed region with a title; the title is highlighted when the panel is active."""

    def __init__(self, y, x, width, height, title=""):
        self.y, self.x, self.width, self.height, self.title = y, x, width, height, title
        self.active = False
        self.footer = ""

    @property
    def inner(self):
        """(y, x, width, height) of the area inside the frame."""
        return self.y + 1, self.x + 1, self.width - 2, self.height - 2

    def draw(self, screen):
        c = screen.colors
        screen.fill(self.y, self.x, self.width, self.height, c.desktop)
        screen.frame(self.y, self.x, self.width, self.height, c.desktop, self.title,
                     title_attr=(c.tagged if self.active else c.desktop))
        if self.footer:
            label = f" {self.footer} "
            screen.put(self.y + self.height - 1, self.x + 2, label, c.desktop)


class ListView:
    """Rows with a cursor bar, tags, and scrolling, inside a panel.

    `rows` is a list of strings already laid out for the width. Tags are kept by row index
    and survive a redraw; the caller resets them when the rows change meaning.
    """

    def __init__(self, panel, header=0):
        self.panel = panel
        self.header = header            # rows at the top of the panel the list leaves alone
        self.rows = []
        self.cursor = 0
        self.top = 0
        self.tags = set()

    @property
    def area(self):
        y, x, w, h = self.panel.inner
        return y + self.header, x, w, h - self.header

    @property
    def page(self):
        return max(1, self.area[3])

    def set_rows(self, rows, keep=True):
        self.rows = list(rows)
        if not keep:
            self.tags = set()
        self.cursor = min(self.cursor, max(0, len(self.rows) - 1))
        self._scroll()

    def _scroll(self):
        if self.cursor < self.top:
            self.top = self.cursor
        elif self.cursor >= self.top + self.page:
            self.top = self.cursor - self.page + 1

    def draw(self, screen):
        y, x, w, h = self.area
        c = screen.colors
        for i in range(h):
            idx = self.top + i
            if idx >= len(self.rows):
                screen.put(y + i, x, " " * w, c.desktop)
                continue
            tagged = idx in self.tags
            attr = c.cursor if (idx == self.cursor and self.panel.active) else (c.tagged if tagged else c.desktop)
            mark = "▸" if tagged else " "
            screen.put(y + i, x, (mark + self.rows[idx]).ljust(w)[:w], attr)
        if len(self.rows) > h:
            # a one-column scroll bar on the frame's right edge
            track = max(1, h - 1)
            thumb = y + min(track, int(track * self.cursor / max(1, len(self.rows) - 1)))
            for i in range(h):
                screen.put(y + i, x + w, "▓" if y + i == thumb else "░", c.desktop)

    def handle(self, key):
        """Move or tag; returns True when the key was consumed."""
        n = len(self.rows)
        if key == curses.KEY_DOWN and self.cursor < n - 1:
            self.cursor += 1
        elif key == curses.KEY_UP and self.cursor > 0:
            self.cursor -= 1
        elif key == curses.KEY_NPAGE:
            self.cursor = min(n - 1, self.cursor + self.page) if n else 0
        elif key == curses.KEY_PPAGE:
            self.cursor = max(0, self.cursor - self.page)
        elif key == curses.KEY_HOME:
            self.cursor = 0
        elif key == curses.KEY_END:
            self.cursor = max(0, n - 1)
        elif key in (curses.KEY_IC, 32) and n:               # Ins or Space: tag and move down
            self.tags ^= {self.cursor}
            if self.cursor < n - 1:
                self.cursor += 1
        elif key == ord("*") and n:
            self.tags = set(range(n)) - self.tags
        else:
            return False
        self._scroll()
        return True

    @property
    def selected(self):
        """The tagged rows, or the cursor row when nothing is tagged."""
        if self.tags:
            return sorted(self.tags)
        return [self.cursor] if self.rows else []


class KeyBar:
    """Norton Commander's last line: `1Help 2Send ...`, ten labels, relabelled per screen."""

    def __init__(self, labels):
        self.labels = list(labels) + [""] * (10 - len(labels))

    def draw(self, screen):
        w, h = screen.size
        y = h - 1
        screen.put(y, 0, " " * w, screen.colors.key_number)
        cell = max(6, w // 10)
        for i, label in enumerate(self.labels):
            x = i * cell
            screen.put(y, x, f"{i + 1:>2}", screen.colors.key_number)
            screen.put(y, x + 2, label.ljust(cell - 2)[: cell - 2], screen.colors.bar)


class Message:
    """One line above the key bar: the last outcome, cleared by the next key."""

    def __init__(self):
        self.text = ""
        self.error = False

    def say(self, text, error=False):
        self.text, self.error = text, error

    def clear(self):
        self.text = ""

    def draw(self, screen):
        w, h = screen.size
        attr = screen.colors.error if self.error else screen.colors.message
        screen.put(h - 2, 0, " " * w, screen.colors.desktop)
        if self.text:
            screen.put(h - 2, 1, self.text[: w - 2], attr)
