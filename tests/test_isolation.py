"""The split, as imports. An interface (every package under mccontrol/ui, found by walking
the tree) imports the standard library, mccontrol.apiclient and its own package, never the
backend or another interface. The backend (everything else under mccontrol but cli.py and
launch.py) imports neither curses nor an interface. The command line and the launcher import
the standard library, the client and the server that starts the backend; the launcher finds
the interfaces by name, and cli.py imports it only to open the screens."""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).parents[1] / "mccontrol"
UI = ROOT / "ui"
FRONT = ("cli.py", "launch.py")
BACKEND_NAMES = ("store", "actions", "transport", "beacon", "envelope", "firmware", "denylist", "commands", "directory",
                 "helptext", "nodestats", "token", "api")


def interfaces():
    return sorted(p for p in UI.iterdir() if (p / "__init__.py").is_file() and not p.name.startswith(("_", ".")))


def backend():
    return sorted(p for p in ROOT.rglob("*.py") if UI not in p.parents
                  and not (p.parent == ROOT and p.name in FRONT))


def package_of(path):
    return ".".join(path.relative_to(ROOT.parent).parent.parts)


def imports(path, package=None, skip=None):
    """The absolute module names a file imports, relative imports resolved against `package`
    (by default the file's own). `from X import a` is X.a when a is a module of ours, else X.
    `skip(tree)` names a node whose imports are not read."""
    parts = (package or package_of(path)).split(".")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    skipped = {id(n) for n in ast.walk(skip(tree))} if skip else set()
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = parts[: len(parts) - node.level + 1] if node.level else []
            module = base + (node.module.split(".") if node.module else [])
            for a in node.names:
                sub = ROOT.parent.joinpath(*module, a.name)
                yield ".".join(module + [a.name]) if sub.with_suffix(".py").is_file() or sub.is_dir() else ".".join(module)


def ours(name):
    return name.split(".")[0] == "mccontrol"


def stdlib(name):
    return name.split(".")[0] in sys.stdlib_module_names


def interface_violations(path, package=None):
    """What an interface module may not import: anything of ours but the client and its own
    package, anything outside the stdlib, and a backend module named in a string."""
    own = ".".join((package or package_of(path)).split(".")[:3])           # mccontrol.ui.<name>
    found = [n for n in imports(path, package)
             if (ours(n) and n != "mccontrol.apiclient" and n != own and not n.startswith(own + "."))
             or (not ours(n) and not stdlib(n))]
    found += re.findall(r"mccontrol\.(?:%s)\b" % "|".join(BACKEND_NAMES + ("ui", "cli", "launch")),
                        path.read_text(encoding="utf-8").replace(f"{own}", ""))
    return found


def backend_violations(path, package=None):
    """What the backend may not import: curses, or an interface."""
    return [n for n in imports(path, package)
            if n.split(".")[0] in ("curses", "_curses") or n == "mccontrol.ui" or n.startswith("mccontrol.ui.")]


def front_violations(path, package=None, skip=None):
    """What cli.py and launch.py may import of ours: the client and the server, no more."""
    return [n for n in imports(path, package, skip)
            if (ours(n) and n not in ("mccontrol.apiclient", "mccontrol.api.server")) or (not ours(n) and not stdlib(n))]


def screens_branch(tree):
    """The `if args.command is None:` block of cli.py: the door to the launcher."""
    return next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "args.command is None")


def test_every_interface_imports_only_the_client_and_its_own_package():
    found = interfaces()
    assert {p.name for p in found} >= {"MeshcoreCommander", "IBMi", "Monolith"}
    files = [f for p in found for f in sorted(p.rglob("*.py"))]
    assert all(any(f.parent == p for f in files) for p in found)
    bad = {str(f.relative_to(UI)): v for f in files if (v := interface_violations(f))}
    assert bad == {}


def test_the_interfaces_package_is_a_docstring():
    body = ast.parse((UI / "__init__.py").read_text(encoding="utf-8")).body
    assert len(body) == 1 and isinstance(body[0], ast.Expr) and isinstance(body[0].value.value, str)


def test_the_backend_imports_neither_curses_nor_an_interface():
    files = backend()
    names = {str(f.relative_to(ROOT)) for f in files}
    assert {f"{n}.py" for n in BACKEND_NAMES if n != "api"} | {"api/server.py", "api/routes.py", "apiclient.py"} <= names
    bad = {str(f.relative_to(ROOT)): v for f in files if (v := backend_violations(f))}
    assert bad == {}


def test_the_command_line_and_the_launcher_import_only_the_client_and_the_server():
    cli = ROOT / "cli.py"
    assert front_violations(cli, skip=screens_branch) == []
    assert set(imports(cli)) - set(imports(cli, skip=screens_branch)) == {"mccontrol.launch"}
    assert front_violations(ROOT / "launch.py") == []


# -- the checks see what they are for ------------------------------------------------

def test_the_interface_check_sees_a_backend_or_another_interface(tmp_path):
    ok = tmp_path / "ok.py"
    ok.write_text("import curses\nfrom mccontrol.apiclient import ApiError\nfrom mccontrol import apiclient\n"
                  "from . import art\nfrom .board import Board\n")
    assert interface_violations(ok, "mccontrol.ui.Monolith") == []
    for line in ("from ... import store\n", "from ...api import serve\n", "import mccontrol.directory\n",
                 "from mccontrol import actions\n", "from mccontrol import apiclient, actions\n", "import paho\n",
                 "from ..IBMi import fmt\n", "from mccontrol.ui.MeshcoreCommander import Screen\n", "from .. import IBMi\n",
                 "import importlib; importlib.import_module('mccontrol.store')\n"):
        bad = tmp_path / "bad.py"
        bad.write_text(line)
        assert interface_violations(bad, "mccontrol.ui.Monolith"), line


def test_the_backend_check_sees_curses_and_an_interface(tmp_path):
    bad = tmp_path / "bad.py"
    for line in ("import curses\n", "from curses import wrapper\n", "from ..ui import Monolith\n", "from ..ui.IBMi import fmt\n",
                 "import mccontrol.ui.MeshcoreCommander\n"):
        bad.write_text(line)
        assert backend_violations(bad, "mccontrol.api"), line
    bad.write_text("from .. import actions, store as _store\nfrom .errors import ApiError\n")
    assert backend_violations(bad, "mccontrol.api") == []


def test_the_front_check_sees_the_backend(tmp_path):
    bad = tmp_path / "bad.py"
    for line in ("from . import store\n", "from .api import routes\n", "from .api.jobs import Jobs\n", "import paho\n",
                 "from .ui import IBMi\n", "from . import launch\n"):
        bad.write_text(line)
        assert front_violations(bad, "mccontrol"), line
    bad.write_text("import sys\nfrom . import apiclient\nfrom .apiclient import Client\nfrom .api.server import serve, stop\n")
    assert front_violations(bad, "mccontrol") == []
