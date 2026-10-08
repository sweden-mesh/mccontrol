"""The drawing that needs no terminal: glyphs, sparklines, bars, big digits, the title,
the palettes and the tile grid. Pure functions, so they are tested without curses.
"""

import curses
import time

# -- glyphs ------------------------------------------------------------------------------

GLYPHS = {"spark": "▁▂▃▄▅▆▇█", "full": "█", "empty": "░", "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯",
          "h": "─", "v": "│", "scan": "┄", "tag": "◆", "focus": "◉", "dot": "●", "ok": "✓", "bad": "✗",
          "up": "▲", "down": "▼", "run": "▶"}
ASCII = {"spark": " .:-=+*#", "full": "#", "empty": ".", "tl": "+", "tr": "+", "bl": "+", "br": "+",
         "h": "-", "v": "|", "scan": "-", "tag": "*", "focus": "@", "dot": "o", "ok": "+", "bad": "x",
         "up": "^", "down": "v", "run": ">"}
BLOCKS_ASCII = str.maketrans({"█": "#", "▀": "'", "▄": ",", "▪": "."})


def glyphs(encoding):
    """The box and block characters where the terminal's encoding can carry them, ASCII
    where it cannot."""
    return GLYPHS if "utf" in (encoding or "").lower().replace("-", "") else ASCII


# -- charts ------------------------------------------------------------------------------

def sparkline(values, width, g=GLYPHS):
    """`values` (None for a gap) as one row of block characters, `width` wide at most: more
    values than cells are averaged into buckets, fewer are stretched over it. Scaled from
    the smallest to the largest value present; a flat series sits in the middle."""
    marks = g["spark"]
    width = max(0, width)
    if not values or not width:
        return ""
    if len(values) > width:
        buckets = []
        for i in range(width):
            part = [v for v in values[i * len(values) // width:(i + 1) * len(values) // width] if v is not None]
            buckets.append(sum(part) / len(part) if part else None)
        values = buckets
    else:
        values = [v for v in values for _ in range(width // len(values))]
    present = [v for v in values if v is not None]
    if not present:
        return " " * len(values)
    lo, hi = min(present), max(present)
    out = []
    for v in values:
        if v is None:
            out.append(" ")
        elif hi == lo:
            out.append(marks[len(marks) // 2 - 1])
        else:
            out.append(marks[min(len(marks) - 1, int((v - lo) / (hi - lo) * len(marks)))])
    return "".join(out)


def bar(fraction, width, g=GLYPHS):
    """A progress bar of `width` cells, filled to `fraction` (clamped to 0..1)."""
    width = max(0, width)
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return g["full"] * filled + g["empty"] * (width - filled)


# -- big digits and the title ------------------------------------------------------------

DIGITS = {
    "0": ["█▀█", "█ █", "█▄█"], "1": ["▀█ ", " █ ", "▄█▄"], "2": ["▀▀█", "█▀▀", "█▄▄"],
    "3": ["▀▀█", " ▀█", "▄▄█"], "4": ["█ █", "▀▀█", "  █"], "5": ["█▀▀", "▀▀█", "▄▄█"],
    "6": ["█▀▀", "█▀█", "█▄█"], "7": ["▀▀█", "  █", "  █"], "8": ["█▀█", "█▀█", "█▄█"],
    "9": ["█▀█", "▀▀█", "▄▄█"], ":": [" ", "▀", "▀"], " ": ["  ", "  ", "  "], "-": ["   ", "▀▀▀", "   "],
}

# Squared, wide capitals in the manner of Eurostile Extended: two rows of half blocks.
LETTERS = {
    "M": ["█▀▀█▀▀█", "█  █  █"], "O": ["█▀▀▀█", "█▄▄▄█"], "N": ["█▀▀▀█", "█   █"],
    "L": ["█    ", "█▄▄▄▄"], "I": ["█", "█"], "T": ["▀▀█▀▀", "  █  "], "H": ["█   █", "█▀▀▀█"],
    " ": ["  ", "  "],
}


def bigtext(text, g=GLYPHS, font=DIGITS):
    """`text` in a block font: a list of rows, one space between characters."""
    rows = len(next(iter(font.values())))
    out = [" ".join(font.get(c, font[" "])[r] for c in text) for r in range(rows)]
    return out if g is GLYPHS else [row.translate(BLOCKS_ASCII) for row in out]


def title(g=GLYPHS):
    return bigtext("MONOLITH", g, LETTERS)


def duration(secs):
    """Seconds as `HH:MM:SS`, or `Nd HH:MM` past a day."""
    secs = max(0, int(secs))
    d, rest = divmod(secs, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return f"{d}d {h:02}:{m:02}" if d else f"{h:02}:{m:02}:{s:02}"


def clock(now=None):
    return time.strftime("%H:%M", time.localtime(now))


# -- palettes ----------------------------------------------------------------------------

# Phosphor on near-black with one accent. Each entry: (foreground, dim foreground, accent,
# background), as 256-colour numbers and as the eight basic colours.
PALETTES = {
    "amber": {256: (214, 136, 81, 234), 8: (3, 3, 6, 0)},
    "green": {256: (46, 28, 214, 233), 8: (2, 2, 3, 0)},
}
ROLES = ("base", "dim", "bright", "accent", "cursor", "alert", "frame")
PAIR = {"base": 1, "dim": 2, "bright": 1, "accent": 3, "cursor": 4, "alert": 5, "frame": 2}

# The same roles on a terminal without colour: still five distinguishable looks.
MONO = {"base": curses.A_NORMAL, "dim": curses.A_DIM, "bright": curses.A_BOLD,
        "accent": curses.A_BOLD | curses.A_UNDERLINE, "cursor": curses.A_REVERSE,
        "alert": curses.A_REVERSE | curses.A_BOLD, "frame": curses.A_NORMAL}


def pairs(name, ncolors):
    """`[(pair number, fg, bg)]` for a palette on a terminal with `ncolors` colours."""
    fg, dim, accent, bg = PALETTES[name][256 if ncolors >= 256 else 8]
    return [(1, fg, bg), (2, dim, bg), (3, accent, bg), (4, bg, fg), (5, bg, accent)]


def roles(color_pair=None):
    """The attribute for each role: from colour pairs when `color_pair(n)` is given, else
    the monochrome fallback. Bold makes `bright` out of the base colour."""
    if color_pair is None:
        return dict(MONO)
    out = {role: color_pair(PAIR[role]) for role in ROLES}
    out["bright"] |= curses.A_BOLD
    out["alert"] |= curses.A_BOLD
    return out


# -- the grid ----------------------------------------------------------------------------

MIN_W, MIN_H = 80, 24


def layout(w, h):
    """The tiles for a `w` x `h` terminal, as `{name: (y, x, width, height)}`.

    Every size gets a header, the hint bar and four tiles: devices, device, feed, status.
    Under 120 columns two columns: the directory on the left, status, device and feed
    stacked right. From 120 columns three: directory, then device over feed, then the
    clock over status. From 40 rows the header grows to the block-letter title."""
    if w < MIN_W or h < MIN_H:
        return {}
    head = 3 if h >= 40 and w >= 100 else 1
    top, body = head, h - head - 1
    tiles = {"header": (0, 0, w, head), "hints": (h - 1, 0, w, 1)}
    if w >= 120:
        c1 = w * 2 // 5
        c3 = max(32, w // 4)
        c2 = w - c1 - c3
        dev = body // 2
        clock_h = 7 if body >= 24 else 0
        tiles.update(devices=(top, 0, c1, body), device=(top, c1, c2, dev), feed=(top + dev, c1, c2, body - dev),
                     status=(top + clock_h, c1 + c2, c3, body - clock_h))
        if clock_h:
            tiles["clock"] = (top, c1 + c2, c3, clock_h)
        return tiles
    c1 = w // 2 + 4
    c2 = w - c1
    status = 6
    dev = (body - status) // 2
    tiles.update(devices=(top, 0, c1, body), status=(top, c1, c2, status), device=(top + status, c1, c2, dev),
                 feed=(top + status + dev, c1, c2, body - status - dev))
    return tiles


# -- the palette's filter ----------------------------------------------------------------

def fuzzy(query, text):
    """A score for `query` as a subsequence of `text` (higher is better), None when it is
    not one. Consecutive letters and letters at a word's start score more; an empty query
    matches everything equally."""
    q, t = query.lower().strip(), text.lower()
    if not q:
        return 0
    score, pos, prev = 0, 0, -2
    for ch in q:
        if ch == " ":
            continue
        i = t.find(ch, pos)
        if i < 0:
            return None
        score += 1 + (3 if i == prev + 1 else 0) + (2 if i == 0 or t[i - 1] in " -_:." else 0)
        prev, pos = i, i + 1
    return score - len(t) / 100.0


def ranked(query, items, key=lambda x: x):
    """`items` that match `query`, best first; ties keep their order."""
    scored = [(fuzzy(query, key(item)), i, item) for i, item in enumerate(items)]
    return [item for s, i, item in sorted((s for s in scored if s[0] is not None), key=lambda s: (-s[0], s[1]))]


def wrap(text, width):
    """Plain word wrap; a word longer than the width is cut."""
    width = max(1, width)
    lines = []
    for para in str(text).split("\n"):
        line = ""
        for word in para.split(" "):
            while len(word) > width:
                if line:
                    lines.append(line)
                    line = ""
                lines.append(word[:width])
                word = word[width:]
            if not line:
                line = word
            elif len(line) + 1 + len(word) <= width:
                line += " " + word
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines
