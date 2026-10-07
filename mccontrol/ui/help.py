"""The help window: a topic page over the work, links the cursor moves between, Enter follows.

Borland's help was a window like any other, with the links highlighted, Tab and the arrows
moving between them, Enter following, Alt+F1 going back through the topics seen, Shift+F1 the
contents, Esc closing. That is what this does. Terminals name the modified function keys
unevenly, so Backspace also goes back and `c` also opens the contents.
"""

import curses

from .screen import KEY_BACKSPACE, KEY_ENTER

SHIFT_F1 = (curses.KEY_F0 + 13,)               # xterm: \E[1;2P
ALT_F1 = (curses.KEY_F0 + 49, -ord("1"))       # xterm: \E[1;3P; some terminals send Esc 1


class HelpWindow:
    def __init__(self, loader, contents="index"):
        self.loader = loader
        self.contents = contents
        self.history = []

    def run(self, screen, topic, redraw_under=None):
        current = self.loader(topic) or self.loader(self.contents)
        if current is None:
            return
        link, top = 0, 0
        while True:
            if redraw_under:
                redraw_under()
            page = self._draw(screen, current, link, top)
            links = current.links
            key = screen.key()
            if key in (27, curses.KEY_F3, curses.KEY_F10):
                return
            if key in (9, curses.KEY_DOWN, curses.KEY_RIGHT):
                if links:
                    link = (link + 1) % len(links)
                    top = self._keep_visible(links[link][0], top, page)
                elif top + page < len(current.lines):
                    top += 1
            elif key in (curses.KEY_BTAB, curses.KEY_UP, curses.KEY_LEFT):
                if links:
                    link = (link - 1) % len(links)
                    top = self._keep_visible(links[link][0], top, page)
                elif top > 0:
                    top -= 1
            elif key == curses.KEY_NPAGE:
                top = min(max(0, len(current.lines) - page), top + page)
            elif key == curses.KEY_PPAGE:
                top = max(0, top - page)
            elif key in KEY_ENTER and links:
                target = self.loader(links[link][3])
                if target:
                    self.history.append(current)
                    current, link, top = target, 0, 0
            elif key in ALT_F1 or key in KEY_BACKSPACE:
                if self.history:
                    current, link, top = self.history.pop(), 0, 0
            elif key in SHIFT_F1 or key in (ord("c"), ord("C")):
                if current.name != self.contents:
                    self.history.append(current)
                    current, link, top = self.loader(self.contents), 0, 0

    @staticmethod
    def _keep_visible(line, top, page):
        if line < top:
            return line
        if line >= top + page:
            return line - page + 1
        return top

    def _draw(self, screen, current, link, top):
        w, h = screen.size
        width, height = min(78, w - 2), min(h - 2, 22)
        y, x = max(1, (h - height) // 2), max(1, (w - width) // 2)
        c = screen.colors
        screen.shadow(y, x, width, height)
        screen.fill(y, x, width, height, c.dialog)
        screen.frame(y, x, width, height, c.dialog, f"Help: {current.title}")
        page = height - 3
        for i in range(page):
            idx = top + i
            if idx >= len(current.lines):
                break
            line = current.lines[idx]
            screen.put(y + 1 + i, x + 2, line[: width - 4], c.dialog)
            for j, (li, start, end, _t) in enumerate(current.links):
                if li == idx and start < width - 4:
                    attr = c.cursor if j == link else (c.dialog | curses.A_BOLD | curses.A_UNDERLINE)
                    screen.put(y + 1 + i, x + 2 + start, line[start:end][: width - 4 - start], attr)
        legend = " Enter=follow  Tab=next link  Backspace/Alt+F1=back  c/Shift+F1=contents  Esc=close "
        screen.put(y + height - 2, x + 2, legend[: width - 4], c.dialog | curses.A_DIM)
        if top + page < len(current.lines):
            screen.put(y + height - 1, x + width - 10, " More... ", c.dialog)
        screen.refresh()
        return page
