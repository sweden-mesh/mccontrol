"""MeshcoreCommander: a Turbo Vision frame around Norton Commander panels, on curses.

`app.py` is the screen, a client of the API and nothing else; the widget layer under it
knows nothing of devices or brokers. `Screen` owns the terminal; the widgets draw themselves
onto it from state the caller passes in and hand keys back as decisions.
"""

from .screen import Screen, Colors, KEY_ENTER, is_enter, is_exit            # noqa: F401
from .widgets import Panel, ListView, KeyBar                                # noqa: F401
from .dialogs import Dialog, Field, Button, ChoiceBox, ListBox, Progress, confirm, notice, waiting   # noqa: F401
from .menu import MenuBar, Menu, MenuItem                                 # noqa: F401
from .help import HelpWindow                                              # noqa: F401
from .app import run                                                      # noqa: F401

DESCRIPTION = "Norton Commander panels in Turbo Pascal colours: the directory, history and every verb on a function key"
