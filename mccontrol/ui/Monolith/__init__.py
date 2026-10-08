"""Monolith: the fleet as a board of live tiles in phosphor on black -- a directory, the
device under the cursor with its reads and a statistics sparkline, a feed of results, the
broker and key, a clock -- with a command palette, the mouse, and jobs drawn as bars.

On the API only: the client is the one module of the package it imports.
"""

import os
import sys

DESCRIPTION = "Monolith: a retro-futuristic board of live tiles, a command palette, phosphor on black"


def run(client):
    """The board until q; 2 when there is no terminal to draw it on."""
    import curses

    from .board import Board

    if not sys.stdout.isatty():
        print("mccontrol: Monolith needs a terminal; use the subcommands instead (mccontrol --help)", file=sys.stderr)
        return 2
    os.environ.setdefault("ESCDELAY", "60")
    try:
        return curses.wrapper(lambda win: Board(win, client).run())
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot draw Monolith: {exc}", file=sys.stderr)
        return 2
