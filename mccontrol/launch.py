"""`mccontrol --ui <name>`: start the API in-process, run an interface on it, stop the API;
with `--api <url>`, run it on that running API instead and start none.

An interface is a package under `mccontrol/ui/` with `run(client)` and a one-line
`DESCRIPTION`. A name given is kept in the configuration and is the default next time; with
no name given and none kept, Monolith is started (and nothing is kept). A name that is not
there lists the interfaces instead and exits 2.
"""

import importlib
import pathlib
import sys

from . import apiclient
from .api.server import serve, stop

UI = pathlib.Path(__file__).with_name("ui")
DEFAULT = "Monolith"                    # started when no --ui is given and none is kept


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


def run(store, name=None, api=None):
    """Run the interface `name`, or the last one used, over `store` (None: the default), or
    over the API already running at the base URL `api`, starting none; the exit code."""
    server, url = (None, api) if api else serve(store)
    try:
        client = apiclient.Client(url)
        found = interfaces()
        asked = name or client.config().get("ui") or DEFAULT
        # the name is matched without regard to case (`--ui ibmi`), and the interface's own
        # spelling is what is run and kept
        chosen = next((n for n in found if n.lower() == asked.lower()), None)
        if chosen is None:
            print(listing(found), file=sys.stderr)
            print(f"mccontrol: no interface named {asked!r}", file=sys.stderr)
            return 2
        if name:
            client.set_config(ui=chosen)
        return importlib.import_module(f"mccontrol.ui.{chosen}").run(client)
    finally:
        if server is not None:
            stop(server)
