"""`mccontrol --ui <name>`: start the API in-process, run an interface on it, stop the API.

An interface is a package under `mccontrol/ui/` with `run(client)` and a one-line
`DESCRIPTION`. The name used is kept in the configuration and is the default next time; with
no name given and none kept, or a name that is not there, the interfaces are listed instead.
"""

import importlib
import pathlib
import sys

from . import apiclient
from .api import serve, stop

UI = pathlib.Path(__file__).with_name("ui")


def interfaces():
    """`{name: DESCRIPTION}` for every package in `mccontrol/ui/`, by name."""
    found = {}
    for path in sorted(UI.iterdir(), key=lambda p: p.name.lower()):
        if (path / "__init__.py").is_file() and not path.name.startswith(("_", ".")):
            found[path.name] = importlib.import_module(f"mccontrol.ui.{path.name}").DESCRIPTION
    return found


def listing(found):
    width = max((len(n) for n in found), default=0)
    return "\n".join(["interfaces (mccontrol --ui <name>):"] + [f"  {n:{width}}  {d}" for n, d in found.items()])


def run(store, name=None):
    """Run the interface `name`, or the last one used, over `store` (None: the default);
    the exit code."""
    server, url = serve(store)
    try:
        client = apiclient.Client(url)
        found = interfaces()
        chosen = name or client.config().get("ui")
        if chosen not in found:
            print(listing(found), file=sys.stdout if chosen is None else sys.stderr)
            if chosen is not None:
                print(f"mccontrol: no interface named {chosen!r}", file=sys.stderr)
            return 0 if chosen is None else 2
        if name:
            client.set_config(ui=name)
        return importlib.import_module(f"mccontrol.ui.{chosen}").run(client)
    finally:
        stop(server)
