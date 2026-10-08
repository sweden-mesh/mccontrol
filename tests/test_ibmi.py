"""IBMi, the 5250 green screen: a walk per screen under a pseudo-terminal, against an
in-process API server over the scripted broker (the `api` fixture), the rule that it
imports nothing of the package but the client, and its pure formatting helpers."""
import ast
import json
import os
import pathlib
import pty
import re
import select
import sys
import time

import pytest

from mccontrol import transport
from mccontrol.ui.IBMi import fmt

PACKAGE = pathlib.Path(__file__).parents[1] / "mccontrol" / "ui" / "IBMi"
F = {n: f"\x1b[{c}~".encode() for n, c in zip(range(5, 13), (15, 17, 18, 19, 20, 21, 23, 24))}
F.update({1: b"\x1bOP", 2: b"\x1bOQ", 3: b"\x1bOR", 4: b"\x1bOS"})
DOWN, UP, TAB = b"\x1bOB", b"\x1bOA", b"\t"
pytestmark = pytest.mark.filterwarnings("ignore:This process .* is multi-threaded:DeprecationWarning")


def walk(api, keys, seconds=2.0, cols=80, rows=24, start=1.0):
    """Start IBMi in a pty on a client of the fixture's server, send `keys` as (bytes,
    delay) pairs, and return the screen text with the escape sequences stripped."""
    import fcntl, signal, struct, termios
    _server, url, _store = api
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.environ.update({"TERM": "xterm", "COLUMNS": str(cols), "LINES": str(rows)})
        os.execvp(sys.executable, [sys.executable, "-c",
                  "import sys; sys.path.insert(0, %r); from mccontrol.apiclient import Client; "
                  "from mccontrol.ui.IBMi import run; sys.exit(run(Client(%r)))" % (os.getcwd(), url)])
    out = b""

    def pump(secs):
        nonlocal out
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.05)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return
    pump(start)
    for k, delay in keys:
        os.write(fd, k)
        pump(delay)
    os.write(fd, b"\x0c")                   # Ctrl+L: the last frame in full
    pump(seconds)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]|\x1b[=>]", b" ", out).decode("utf-8", "replace")


BACKEND = {"store", "actions", "transport", "beacon", "envelope", "firmware", "denylist", "commands", "directory",
           "helptext", "api", "app", "cli", "token", "nodestats", "ui"}


def imports(path):
    """`(level, module, names)` for every import in a file."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield 0, alias.name, []
        elif isinstance(node, ast.ImportFrom):
            yield node.level, node.module or "", [a.name for a in node.names]


def violations(path):
    """The imports in a file an interface may not make: anything of the package but the
    client, a relative import out of its own directory, or a backend module by bare name."""
    out = []
    for level, module, names in imports(path):
        where = f"{path.name}: {'.' * level}{module} {names}"
        if level:
            if level > 1:
                out.append(where)
        elif module.split(".")[0] == "mccontrol":
            if not (module == "mccontrol.apiclient" or (module == "mccontrol" and names == ["apiclient"])):
                out.append(where)
        elif module.split(".")[0] in BACKEND:
            out.append(where)
    return out


def test_ibmi_imports_nothing_of_the_package_but_the_client():
    files = sorted(PACKAGE.glob("*.py"))
    assert len(files) >= 2
    assert [v for path in files for v in violations(path)] == []


def test_the_isolation_check_catches_a_backend_import(tmp_path):
    bad = tmp_path / "bad.py"
    for line in ("from mccontrol import store", "from mccontrol import apiclient, actions", "import mccontrol.actions",
                 "from ... import transport", "from ..screen import Screen", "from mccontrol.api import serve", "import envelope"):
        bad.write_text(line + "\n")
        assert violations(bad), line
    bad.write_text("import curses\nfrom mccontrol.apiclient import ApiError\nfrom mccontrol import apiclient\nfrom . import fmt\n")
    assert violations(bad) == []


def test_the_interface_has_a_description_and_an_entry():
    import mccontrol.ui.IBMi as ibmi
    assert ibmi.DESCRIPTION.startswith("IBMi") and "\n" not in ibmi.DESCRIPTION
    assert callable(ibmi.run)


# -- the pure helpers ---------------------------------------------------------------------

def test_a_result_says_how_far_it_can_be_trusted():
    assert fmt.signature({"ok": True, "code": 0, "verified": True}) == "verified"
    assert fmt.signature({"ok": True, "code": 0, "verified": None}) == "unverified result"
    assert fmt.signature({"ok": False, "code": 0, "verified": False}) == "BAD SIGNATURE"
    assert fmt.signature({"ok": None, "code": None, "verified": None}) == "-"
    row = fmt.outcome_row({"name": "Gisebo", "node": "4AC61A7F", "ok": True, "code": 0, "verified": True, "line": "> * F [signed]"})
    assert row.startswith("Gisebo") and "verified" in row and row.endswith("> * F [signed]")


def test_a_send_is_to_one_region():
    one = [{"name": "Gisebo", "node": "4AC61A7F", "region": "JKG"}]
    two = one + [{"name": "", "node": "65C1D12C", "region": "jkg"}]
    assert fmt.target_line(one) == ("Gisebo  (4AC61A7F, JKG)", None)
    assert fmt.target_line(two) == ("2 tagged devices in JKG", None)
    line, why = fmt.target_line(two + [{"name": "Far", "node": "FD700369", "region": "GOT"}])
    assert line is None and "more than one region" in why
    assert fmt.target_line([])[0] is None


def test_a_greyed_entry_says_why():
    assert fmt.command_note({"admitted": True, "needs": "ar-10", "refusal": None}) == ""
    assert fmt.command_note({"admitted": False, "needs": "ar-11", "refusal": None}) == "needs ar-11"
    assert fmt.command_note({"admitted": True, "refusal": "denied: x"}).startswith("denied:")
    assert fmt.command_label({"text": "set autoreply", "kind": "choice", "choices": ["on", "off"]}) == "set autoreply  <on|off>"
    assert fmt.firmware_note(0, [None]) == ""
    assert fmt.firmware_note(3, [None]) == "3 greyed: need a newer build; this device reports unknown"
    assert fmt.firmware_note(3, ["esp32s3-v1.17.1-ar-10-observer-42f48ce9"]).endswith("reports ar-10")


def test_several_devices_admit_only_what_every_one_admits():
    def listing(admitted, version):
        return {"version": version, "groups": [{"group": "reads", "commands": [{"text": "ver", "admitted": admitted}]}]}
    merged = fmt.merge_commands([listing(True, "ar-11"), listing(False, None)])
    assert merged["groups"][0]["commands"][0]["admitted"] is False and merged["greyed"] == 1
    assert merged["versions"] == ["ar-11", None]
    assert fmt.merge_commands([listing(True, "x"), listing(True, "y")])["greyed"] == 0


def test_the_small_pieces():
    assert fmt.opt_typed("", "1") == "1" and fmt.opt_typed("1", "1") == "11" and fmt.opt_typed("11", "4") == "4"
    rows = fmt.fkeys(["F1=Help", "F3=Exit", "F5=Refresh", "F6=Discover", "F7=Batt/Seen", "F9=Send", "F10=Names",
                      "F11=Sort", "F12=Cancel", "F13-F21=Shortcuts"])
    assert len(rows) == 2 and all(len(r) <= 78 for r in rows) and rows[0].startswith("F1=Help")
    key = "AB" * 32
    assert fmt.pubkey_lines(key, 100) == [f"set mqtt.cmd.pubkey {key}", "set mqtt.cmd on"]
    split = fmt.pubkey_lines(key, 80)
    assert "".join(l.split()[-1] for l in split[:2]) == key and all(len(l) < 78 for l in split)
    assert fmt.build("set autoreply", ["off"]) == "set autoreply off" and fmt.build("ver", []) == "ver"
    assert len({fmt.dots(l).index(":") for l in ("Name", "Region", "Statistics URL", "Or every node in region")}) == 1
    assert fmt.split_verb("  SEND Far ver ") == ("send", "Far ver") and fmt.split_verb("") == ("", "")
    assert fmt.history_out({"code": 0, "reply": ""}) == "OK"
    assert fmt.history_out({"code": 14, "code_name": "not-allowed"}) == "refused not-allowed"
    assert fmt.history_out({"code": None, "note": ""}) == "no answer"


def test_a_found_node_says_what_it_is_known_as_here_even_unnamed():
    e = {"node": "65C1D12C", "region": "JKG", "name": "", "outcome": "> on", "known_as": ""}
    assert fmt.found_row(e).endswith("= (unnamed)")
    assert "= " not in fmt.found_row(dict(e, known_as=None))


def test_a_broker_test_lists_its_steps_then_the_key_and_registration():
    lines = fmt.test_lines({"steps": [{"step": "connect", "ok": True, "detail": "fine"}], "key": "AB",
                            "registered": {"broker": "mqtt.louie.se", "at": "2026-10-08 10:00:00"}})
    assert lines[0].startswith("ok    connect") and "present" in lines[1] and "mqtt.louie.se on 2026-10-08" in lines[2]
