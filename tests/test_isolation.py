"""An interface is a client of the API and nothing else: every module under mccontrol/ui
imports the standard library, mccontrol.apiclient and its own package, never the backend."""
import ast
import pathlib
import re
import sys

UI = pathlib.Path(__file__).parents[1] / "mccontrol" / "ui"
BACKEND = ("store", "actions", "transport", "beacon", "envelope", "firmware", "denylist", "commands", "directory",
           "helptext", "api")


def imports(path):
    """The absolute module names a file imports (for `from X import a`, X.a where X is a package of ours)."""
    package = ["mccontrol"] + list(path.relative_to(UI.parent).parent.parts)
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = package[: len(package) - node.level + 1] if node.level else []
            module = ".".join(base + (node.module.split(".") if node.module else []))
            if module == "mccontrol":
                yield from (f"mccontrol.{a.name}" for a in node.names)
            else:
                yield module


def violations(path):
    found = []
    for name in imports(path):
        if name.split(".")[0] == "mccontrol":
            if name != "mccontrol.apiclient" and not name.startswith("mccontrol.ui."):
                found.append(name)
        elif name.split(".")[0] not in sys.stdlib_module_names:
            found.append(name)
    text = path.read_text(encoding="utf-8")
    found += re.findall(r"mccontrol\.(?:%s)\b" % "|".join(BACKEND), text)       # an import by string
    return found


def test_no_interface_imports_the_backend():
    files = sorted(UI.rglob("*.py"))
    assert any(p.parent.name == "MeshcoreCommander" for p in files)
    bad = {str(p.relative_to(UI)): v for p in files if (v := violations(p))}
    assert bad == {}


def test_the_check_sees_a_relative_and_an_absolute_import_of_the_backend(tmp_path, monkeypatch):
    probe = UI / "MeshcoreCommander" / "app.py"
    text = probe.read_text(encoding="utf-8")
    for line in ("from ... import store\n", "from ...api import serve\n", "import mccontrol.directory\n",
                 "from mccontrol import actions\n", "import paho\n"):
        fake = tmp_path / "ui" / "MeshcoreCommander" / "app.py"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_text(line + text, encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "UI", tmp_path / "ui")
        assert violations(fake), line
