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


def walk(api, keys, seconds=1.0, cols=80, rows=24, start=1.0, raw=False):
    """Start IBMi in a pty on a client of the fixture's server, send `keys` as (bytes,
    delay) pairs, and return the screen text with the escape sequences stripped (`raw`:
    as the terminal received it)."""
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
        os.write(fd, b"\x0c")               # Ctrl+L after each step: the stream carries only changed
        pump(0.15)                          # cells, so a repaint puts every frame's text in whole
    pump(seconds)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    if raw:
        return out.decode("utf-8", "replace")
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


# -- the walks: a screen each ---------------------------------------------------------------

def sent(store, node):
    return [l["command"] for l in store.history(node)]


def test_the_main_menu_numbers_its_selections_and_takes_a_command(api):
    text = walk(api, [(b"42\r", 0.6)])
    assert "MCCONTROL Main Menu" in text and "Select one of the following:" in text
    assert "1. Work with devices" in text and "90. Sign off" in text and "===>" in text
    assert "Signing key  present" in text and "3 devices" in text
    assert "Command 42 is not valid here." in text
    assert "F3=Exit" in text and "F12=Cancel" in text


def test_work_with_devices_shows_the_directory_and_1_tags_rows(api):
    server, _url, _store = api
    text = walk(api, [(b"1\r", 0.8), (DOWN, 0.2), (b"1", 0.1), (DOWN, 0.2), (b"1\r", 0.8)])
    assert "Work with Devices" in text and "Type options, press Enter." in text
    assert "1=Tag/untag" in text and "7=Commands" in text and "11=Name and version" in text
    assert re.search(r"Opt\s+Name\s+Node\s+Reg\s+Sent", text)
    assert "Gisebo" in text and "(unnamed)" in text and "FD700369" in text and "GOT" in text
    assert "2 of 3 tagged, by name" in text
    assert server.ctx.tags == {"4AC61A7F", "65C1D12C"}
    assert "F6=Discover" in text and "F11=Sort" in text


def test_f11_sorts_and_the_command_line_tags_inverts_and_sorts(api):
    server, _url, _store = api
    text = walk(api, [(b"1\r", 0.8), (F[11], 0.6), (b"tag GOT\r", 0.6), (b"invert\r", 0.6), (b"sort version\r", 0.6),
                      (b"frobnicate\r", 0.6)])
    assert "Sorted by region." in text and "by version" in text
    assert server.ctx.tags == {"4AC61A7F", "65C1D12C"}           # GOT tagged, then inverted
    assert "Command frobnicate is not valid here." in text


def test_9_sends_to_the_rows_and_the_results_show_each_answer_with_its_signature(api):
    _server, _url, store = api
    text = walk(api, [(b"1\r", 0.8), (DOWN, 0.2), (b"9", 0.1), (DOWN, 0.2), (b"9\r", 0.8), (DOWN, 0.2),
                      (b"region\r", 1.2), (b"5\r", 0.6)])
    assert "Send a Command" in text and "Gisebo, 65C1D12C" in text
    assert "Command Results" in text and "2 tagged devices in JKG" in text and "2 of 2 answered." in text
    assert re.search(r"Gisebo\s+unverified result\s+> \* F", text)
    assert "refused: not-allowed" in text
    assert "Display Result" in text and "Reply:" in text
    assert sent(store, "4AC61A7F") == ["region"] and sent(store, "65C1D12C") == ["region"]


def test_a_signed_answer_is_verified_and_a_forged_one_is_said_to_be(api, monkeypatch):
    real = transport._fake

    def signed(*a):
        answers = real(*a)
        for node, r in (answers or {}).items():
            r.verified = node == "4AC61A7F"
        return answers
    monkeypatch.setattr(transport, "_fake", signed)
    text = walk(api, [(b"send Gisebo region\r", 1.2), (F[3], 0.4), (b"send 65C1D12C ver\r", 1.2)])
    assert re.search(r"Gisebo\s+verified\s+> \* F \[signed\]", text)
    assert re.search(r"65C1D12C\s+BAD SIGNATURE\s+BAD SIGNATURE", text)


def test_a_command_that_takes_something_away_asks_first_and_f12_sends_nothing(api):
    _server, _url, store = api
    text = walk(api, [(b"send Gisebo region denyf *\r", 0.8), (F[12], 0.6), (b"send Gisebo region denyf *\r", 0.8),
                      (b"\r", 1.0)])
    assert "Confirm Command" in text and "stops this repeater forwarding every message" in text
    assert "To:  Gisebo  (4AC61A7F, JKG)" in text
    assert "Nothing was sent." in text
    assert sent(store, "4AC61A7F") == ["region denyf *"]          # once: after the second confirm


def test_a_command_the_node_would_refuse_is_not_sent_and_the_reason_is_said(api):
    _server, _url, store = api
    text = walk(api, [(b"send Far set repeat off\r", 0.8)])
    assert "Not sent: the node would refuse 'set repeat off': denied" in text
    assert sent(store, "FD700369") == []


def test_tags_across_two_regions_are_refused(api):
    text = walk(api, [(b"1\r", 0.8), (b"1", 0.1), (DOWN, 0.2), (b"1\r", 0.6), (F[9], 0.6), (DOWN, 0.2), (b"ver\r", 0.6)])
    assert "span more than one region" in text


def test_3_sends_to_every_node_in_a_region_and_12_shows_the_broadcast(api):
    _server, _url, store = api
    text = walk(api, [(b"3\r", 0.6), (b"\t", 0.2), (b"region put se-jkp *\r", 1.2), (F[3], 0.4), (b"12\r", 0.6)])
    assert "Send a Command to Every Node in a Region" in text
    assert "EVERY node in JKG" in text and "2 of 2 answered." in text
    assert "Display Broadcast History" in text and "region put se-jkp *" in text
    assert [l["command"] for l in store.history(None)] == ["region put se-jkp *"]
    assert sent(store, "FD700369") == []                          # Far is in GOT


def _far_on(store, tag):
    store.update_device("FD700369", ver=f"esp32s3-v1.17.1-{tag}-observer-2c079ed7")


def test_7_lists_the_commands_and_greys_what_the_firmware_does_not_admit(api):
    _server, _url, store = api
    raw = walk(api, [(b"1\r", 0.8), (b"7\r", 0.8), (b"get stats\r", 0.6), (b"1\r", 0.6)], raw=True)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]|\x1b[=>]", " ", raw)
    assert "Select Command" in text and "-- reads --" in text
    assert "57 greyed: need a newer build; this device reports unknown" in text
    assert re.search(r"get stats\s+needs ar-11", text) and re.search(r"get autoreply\s+yes", text)
    assert "get stats: needs ar-11; not sent." in text
    assert sent(store, "FD700369") == []
    # the greyed row reaches the terminal in another colour than an admitted one
    before = lambda i: "".join(re.findall(r"\x1b\[[0-9;]*m", raw[max(0, i - 40):i]))
    assert before(raw.index("get autoreply")) != before(raw.index("get stats"))


def test_a_bare_command_from_the_list_is_sent_at_once(api):
    _server, _url, store = api
    _far_on(store, "ar-11")
    transport.FAKE_SCRIPT["FD700369|ver"] = [0, "> esp32s3-v1.17.1-ar-11"]
    text = walk(api, [(b"1\r", 0.8), (b"7\r", 0.8), (b"ver\r", 0.6), (b"1\r", 1.2)])
    assert "this device reports" not in text
    assert "Command Results" in text and "> esp32s3-v1.17.1-ar-11" in text
    assert sent(store, "FD700369") == ["ver"]


def test_a_choice_is_asked_as_numbered_values_and_its_caution_confirmed(api):
    _server, _url, store = api
    _far_on(store, "ar-11")
    transport.FAKE_SCRIPT["FD700369|set autoreply off"] = [0, "OK"]
    text = walk(api, [(b"1\r", 0.8), (b"7\r", 0.8), (b"set autoreply\r", 0.6), (b"1\r", 0.6), (DOWN, 0.2), (b"1\r", 0.8),
                      (b"\r", 1.2)])
    assert "Select Value" in text and "set autoreply <value>" in text
    assert "stops this repeater answering test requests" in text
    assert "Command  . :  set autoreply off" in text and "1 of 1 answered." in text
    assert sent(store, "FD700369") == ["set autoreply off"]


def test_a_text_parameter_shows_examples_and_f12_backs_out(api):
    _server, _url, store = api
    _far_on(store, "ar-11")
    transport.FAKE_SCRIPT["FD700369|region put se-jkp *"] = [0, "OK"]
    text = walk(api, [(b"1\r", 0.8), (b"7\r", 0.8), (b"region put\r", 0.6), (b"1\r", 0.6), (F[12], 0.4),
                      (b"1\r", 0.6), (b"se-jkp *\r", 1.2)])
    assert "Specify Value" in text and "Examples:  se-jkp *   se-jkp-mul se-jkp" in text
    assert sent(store, "FD700369") == ["region put se-jkp *"]        # the first prompt sent nothing


def test_5_displays_the_device_and_its_history_and_8_its_last_answers(api):
    _server, _url, store = api
    transport.FAKE_SCRIPT["FD700369|ver"] = [0, "> v1.17.1-ar-10"]
    text = walk(api, [(b"send Far ver\r", 1.2), (F[3], 0.4), (b"1\r", 0.8), (b"5\r", 0.8), (b"5\r", 0.6), (F[3], 0.4),
                      (F[3], 0.4), (b"8\r", 0.8), (b"1\r", 1.2), (F[3], 0.4)])
    assert "Display Device" in text and "Node . . . . :  FD700369" in text and "Region . . :  GOT" in text
    assert re.search(r"\d\d\d\d-\d\d-\d\d \d\d:\d\d\s+ver\s+> v1\.17\.1-ar-10", text)
    assert "Display History Entry" in text and "Topic  . . :" in text
    assert "Display Last Answers" in text and "1=Read again" in text and re.search(r"now\s+ver\s+> v1\.17\.1-ar-10", text)
    assert sent(store, "FD700369") == ["ver", "ver"]                 # the send, then the read again


def test_2_changes_a_device_and_4_forgets_one_after_a_confirm(api):
    _server, _url, store = api
    text = walk(api, [(b"1\r", 0.8), (b"2\r", 0.6), (b"\x08\x08\x08Near\r", 0.8), (DOWN, 0.2), (b"4\r", 0.6), (F[12], 0.4),
                      (b"4\r", 0.6), (b"\r", 0.8)])
    assert "Change Device" in text and "Saved." in text
    assert "Confirm Forget of Device" in text and "its history file stays" in text and "Forgotten." in text
    names = {d["node"]: d["name"] for d in store.devices()}
    assert names == {"FD700369": "Near", "4AC61A7F": "Gisebo"}         # the cursor stayed on Near; the row under it went


def test_6_shows_statistics_live_then_from_beacon(api, monkeypatch):
    from conftest import BEACON_FAKE
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", BEACON_FAKE)
    transport.FAKE_SCRIPT["4AC61A7F|get stats"] = [0, "> batt=4100mV up=99s noise=-120"]
    text = walk(api, [(b"1\r", 0.8), (DOWN, 0.2), (b"6\r", 1.5), (F[10], 1.5)], rows=40)
    assert "Statistics: Gisebo" in text and "4100 mV" in text
    assert "Beacon: sampled" in text and "4261 mV" in text
    assert "last 24h" in text and "last 48h" in text and "F11=7 days" in text


def test_11_reads_names_and_versions_and_f7_refreshes_the_tagged_from_beacon(api, monkeypatch):
    from conftest import BEACON_FAKE
    _server, _url, store = api
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", BEACON_FAKE)
    text = walk(api, [(b"1\r", 0.8), (DOWN, 0.2), (DOWN, 0.2), (b"11\r", 1.5), (F[3], 0.4), (UP, 0.2), (b"1\r", 0.6),
                      (F[7], 1.0)], cols=100)
    assert "Names and Versions" in text and "1 of 1 gave a version; 1 named." in text
    assert "SE-JKG-Vaster-65c1" in text and "1.17.1-ar-10" in text
    assert "Refreshed 1 of 1 from Beacon." in text
    devs = {d["node"]: d for d in store.devices()}
    assert devs["65C1D12C"]["name"] == "SE-JKG-Vaster-65c1"
    assert devs["4AC61A7F"]["fleet"]["battery_mv"] == 4261 and "fleet" not in devs["FD700369"]   # the tagged one only


def test_5_discovers_a_region_1_adds_one_with_a_name_and_f6_adds_the_rest(api):
    _server, _url, store = api
    transport.FAKE_SCRIPT["13B58595"] = [17, ""]
    text = walk(api, [(b"5\r", 0.6), (b"\r", 1.5), (b"1\r", 0.6), (b"Torpa\r", 0.8), (F[6], 0.8)])
    assert "Discover Devices" in text and "* for every region you have" in text
    assert "Work with Found Devices" in text and "Found in JKG: 4" in text
    assert re.search(r"4AC61A7F\s+JKG\s+SE-JKG-Gisebo\s+= Gisebo", text)
    assert "= (unnamed)" in text                                  # 65C1D12C is known, without a name
    assert "Name the Device" in text and "1 device(s) remembered." in text
    names = {d["node"]: d["name"] for d in store.devices()}
    assert names["13B58595"] == "Torpa" and names["AB6B9285"] == "SE-JKG-UGORptr" and len(names) == 5


def test_discovery_where_nothing_answers_says_so(api):
    transport.FAKE_SCRIPT.clear()
    text = walk(api, [(b"discover GOT\r", 1.5)])
    assert "No node answered: none there holds your key, or its control plane is off." in text


def test_6_works_with_command_sets_creates_runs_and_removes(api):
    _server, _url, store = api
    transport.FAKE_SCRIPT.update({"FD700369|region put se-jkp *": [0, "OK"], "FD700369|region allowf se-jkp": [14, ""]})
    text = walk(api, [(b"6\r", 0.6), (F[6], 0.6), (b"reads", 0.2), (TAB, 0.2), (b"ver", 0.2), (TAB, 0.2), (b"get name\r", 0.6),
                      (b"9\r", 0.6), (TAB, 0.2), (b"se-jkp", 0.2), (TAB, 0.2), (b"\x08" * 10 + b"Far\r", 1.5), (F[3], 0.4),
                      (DOWN, 0.2), (b"4\r", 0.6), (b"\r", 0.6)])
    assert "Work with Command Sets" in text and "2=Change  4=Remove  5=Display  9=Run" in text
    assert "Create Command Set" in text and "Saved reads." in text
    assert "Run a Command Set" in text and "Set: add-scope" in text and "Stopped at step 2." in text
    assert "Confirm Remove of Command Set" in text and "Removed reads." in text
    assert sent(store, "FD700369") == ["region put se-jkp *", "region allowf se-jkp"]
    assert set(store.sets()) == {"add-scope"}


def test_7_edits_the_shortcuts_and_shift_f2_and_alt_2_send_one(api):
    _server, _url, store = api
    transport.FAKE_SCRIPT["FD700369|ver"] = [0, "> v1.17.1-ar-11"]
    text = walk(api, [(b"7\r", 0.6), (DOWN * 8, 0.3), (b"get flood.max\r", 0.6), (b"1\r", 0.8), (b"\x1b[1;2Q", 1.2), (F[3], 0.4),
                      (b"\x1b2", 1.2), (F[3], 0.4), (b"alt 8\r", 0.6)])
    assert "Work with Shortcuts" in text and "Shortcuts saved." in text
    assert store.shortcuts()[8] == "get flood.max" and store.shortcuts()[0] == "region"
    assert "Command  . :  ver" in text and "> v1.17.1-ar-11" in text
    assert "Shortcut 8 is not set; main menu option 7." in text
    assert sent(store, "FD700369") == ["ver", "ver"]


def test_8_shows_the_key_registers_it_and_replaces_it_after_a_confirm(api, monkeypatch):
    from mccontrol import envelope
    _server, _url, store = api
    monkeypatch.setenv("MCCONTROL_FAKE_REGISTER", "ok")
    before = store.public_key()
    text = walk(api, [(b"8\r", 0.8), (b"1\r", 1.2), (b"2\r", 0.6), (F[12], 0.4)], cols=100)
    assert "Work with Signing Key" in text and f"set mqtt.cmd.pubkey {before}" in text and "set mqtt.cmd on" in text
    assert "Registered: ok" in text and re.search(r"Registered with 127\.0\.0\.1 on 20\d\d-\d\d-\d\d", text)
    assert "Confirm Replace of Signing Key" in text and store.public_key() == before
    text = walk(api, [(b"8\r", 0.8), (b"2\r", 0.6), (b"\r", 0.6)])
    assert "Key replaced." in text and store.public_key() != before
    store.key_path.unlink()
    text = walk(api, [(b"8\r", 0.8), (b"2\r", 0.6), (b"22" * 32 + b"\r", 0.8)])
    assert "No signing key yet." in text and "Import a Seed" in text and "Key imported." in text
    assert store.public_key() == envelope.public_key_hex("22" * 32)


def test_9_changes_the_configuration_and_f10_tests_the_broker(api):
    _server, _url, store = api
    port = store.config()["port"]
    text = walk(api, [(b"9\r", 0.6), (TAB * 2, 0.2), (b"mcoar", 0.2), (TAB, 0.2), (b"pw", 0.2), (F[10], 2.0)])
    assert "Change Configuration" in text and "Statistics come from a Beacon server" in text
    assert "Broker Test" in text and "Broker test failed." in text and f"cannot reach 127.0.0.1:{port}" in text
    assert "registered with broker" in text
    cfg = store.config()
    assert cfg["username"] == "mcoar" and cfg["password"] == "pw" and cfg["broker"] == "127.0.0.1"
    text = walk(api, [(b"9\r", 0.6), (TAB, 0.2), (b"x\r", 0.6)])
    assert "Port and wait seconds must be numbers." in text


def test_11_picks_the_directory_columns(api):
    _server, _url, store = api
    text = walk(api, [(b"11\r", 0.6)] + [(b" " + DOWN, 0.15)] * 4 + [(b"\r", 0.6), (b"1\r", 0.8)])
    assert "Change Directory Columns" in text and "Columns: ver, result." in text
    assert store.config()["columns"] == ["ver", "result"]
    assert re.search(r"Opt\s+Name\s+Node\s+Ver\s+Result", text)


def test_f1_opens_help_for_the_screen_follows_a_link_and_help_names_a_topic(api):
    text = walk(api, [(b"1\r", 0.6), (F[1], 0.6), (b"\r", 0.6), (F[11], 0.6), (F[3], 0.4), (F[3], 0.4), (b"help cautions\r", 0.6)])
    assert "Help: The screen" in text                             # Work with Devices opens "screen"
    assert "Help: Keys on the main screen" in text                # its first link, followed with Enter
    assert "Help Contents" in text and "5=Display" in text
    assert "Help: Commands that take something away" in text


def test_a_wait_shows_its_progress_and_f12_cancels_the_job(api, monkeypatch):
    server, _url, store = api
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 4.0)
    text = walk(api, [(b"send Gisebo,65C1D12C region\r", 1.0), (F[12], 1.0)])
    assert "waiting for Gisebo (1 of 2)" in text and "F12=Cancel" in text
    assert "Cancelled: what landed is kept." in text
    job = server.ctx.jobs.all()[0]
    assert job.verb == "send" and job.snapshot()["status"] == "cancelled"
    assert sent(store, "65C1D12C") == []                          # the second device was never asked
