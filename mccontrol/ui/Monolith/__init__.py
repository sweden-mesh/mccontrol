"""Monolith: the fleet as a board of live tiles in phosphor on black -- a directory, the
device under the cursor with its reads and a statistics sparkline, a feed of results, the
broker and key, a clock -- with a command palette, the mouse, and jobs drawn as bars.

On the API only: the client is the one module of the package it imports.
"""

import os
import sys

ESCDELAY_MS = 25
DESCRIPTION = "Monolith: a retro-futuristic board of live tiles, a command palette, phosphor on black"


def escdelay(env=None):
    """How long curses waits after Esc for the rest of a key's sequence, in ms. 25 is ample on
    a local terminal, where a sequence arrives in one write; over a slow ssh link a sequence
    split across packets may be read as Esc and the rest, and MCCONTROL_ESCDELAY (a whole
    number of ms) sets another. An inherited ESCDELAY (often 1000) is not taken: it is what
    made Esc feel caught late."""
    value = (os.environ if env is None else env).get("MCCONTROL_ESCDELAY", "").strip()
    return int(value) if value.isdigit() else ESCDELAY_MS


def run(client):
    """The board until q; 2 when there is no terminal to draw it on."""
    import curses

    from .board import Board

    if not sys.stdout.isatty():
        print("mccontrol: Monolith needs a terminal; use the subcommands instead (mccontrol --help)", file=sys.stderr)
        return 2
    os.environ["ESCDELAY"] = str(escdelay())
    try:
        return curses.wrapper(lambda win: Board(win, client).run())
    except curses.error as exc:
        print(f"mccontrol: this terminal ({os.environ.get('TERM', '?')}) cannot draw Monolith: {exc}", file=sys.stderr)
        return 2
