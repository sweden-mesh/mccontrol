"""The menu bar on row 0: F10 or Alt+letter opens a pull-down, arrows move, Enter runs."""

import curses

from .screen import KEY_ENTER


class MenuItem:
    def __init__(self, label, action, key_label=""):
        self.label, self.action, self.key_label = label, action, key_label


class Menu:
    def __init__(self, title, items, hotkey=None):
        self.title, self.items = title, list(items)
        self.hotkey = (hotkey or title[:1]).lower()


class MenuBar:
    def __init__(self, menus):
        self.menus = list(menus)
        self.open = None          # index of the open menu, or None
        self.item = 0

    def draw(self, screen):
        w, _h = screen.size
        c = screen.colors
        screen.put(0, 0, " " * w, c.bar)
        x = 2
        self.positions = []
        for i, m in enumerate(self.menus):
            attr = c.cursor | curses.A_REVERSE if self.open == i else c.bar
            screen.put(0, x, f" {m.title} ", attr)
            self.positions.append(x)
            x += len(m.title) + 3
        if self.open is not None:
            self._draw_open(screen)

    def _draw_open(self, screen):
        m = self.menus[self.open]
        c = screen.colors
        width = max(len(it.label) + len(it.key_label) + 4 for it in m.items) + 2
        x = self.positions[self.open]
        y = 1
        screen.shadow(y, x, width, len(m.items) + 2)
        screen.fill(y, x, width, len(m.items) + 2, c.dialog)
        screen.frame(y, x, width, len(m.items) + 2, c.dialog, box=None)
        for j, it in enumerate(m.items):
            attr = c.cursor | curses.A_REVERSE if j == self.item else c.dialog
            line = f" {it.label}".ljust(width - 2 - len(it.key_label) - 1) + it.key_label + " "
            screen.put(y + 1 + j, x + 1, line[: width - 2], attr)

    def wants(self, key):
        """Does this key open the bar? F10, or Alt+letter of a menu title."""
        if key == curses.KEY_F10:
            return True
        return key < 0 and any(m.hotkey == chr(-key).lower() for m in self.menus)

    def run(self, screen, key, redraw_under):
        """Open on `key`, interact until Enter or Esc; returns the chosen action or None."""
        if key == curses.KEY_F10:
            self.open = 0
        else:
            self.open = next(i for i, m in enumerate(self.menus) if m.hotkey == chr(-key).lower())
        self.item = 0
        try:
            while True:
                redraw_under()
                self.draw(screen)
                screen.refresh()
                k = screen.key()
                items = self.menus[self.open].items
                if k == 27 or k == curses.KEY_F10:
                    return None
                if k == curses.KEY_LEFT:
                    self.open, self.item = (self.open - 1) % len(self.menus), 0
                elif k == curses.KEY_RIGHT:
                    self.open, self.item = (self.open + 1) % len(self.menus), 0
                elif k == curses.KEY_DOWN:
                    self.item = (self.item + 1) % len(items)
                elif k == curses.KEY_UP:
                    self.item = (self.item - 1) % len(items)
                elif k in KEY_ENTER:
                    return items[self.item].action
                elif k < 0 and any(m.hotkey == chr(-k).lower() for m in self.menus):
                    self.open = next(i for i, m in enumerate(self.menus) if m.hotkey == chr(-k).lower())
                    self.item = 0
                elif 32 <= k < 127:
                    for j, it in enumerate(items):           # a letter picks the item starting with it
                        if it.label.lower().startswith(chr(k).lower()):
                            self.item = j
                            return it.action
        finally:
            self.open = None
