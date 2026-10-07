"""Walks of the second interface under a pseudo-terminal, against a store in a temp dir."""
import json
import os
import pty
import re
import select
import sys
import time

NODES = [("Gisebo", "4AC61A7F", "JKG"), ("", "65C1D12C", "JKG"), ("Far", "FD700369", "GOT")]


def _home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "history").mkdir()
    (home / "key.seed").write_text("11" * 32 + "\n")
    os.chmod(home / "key.seed", 0o600)
    devices = [{"name": n, "node": node, "region": r, "notes": "", "added": "2026-10-07 10:00:00",
                "last_sent": "2026-10-07 11:00:00" if n == "Gisebo" else None, "last_result": "ok" if n == "Gisebo" else None}
               for n, node, r in NODES]
    (home / "devices.json").write_text(json.dumps(devices))
    (home / "history" / "4AC61A7F.jsonl").write_text(json.dumps({"at": "2026-10-07 11:00:00", "command": "region", "topic": "t", "code": 0, "code_name": "ok", "reply": "> * F", "note": ""}) + "\n")
    return home


def walk(tmp_path, keys, seconds=3.0, env=None, cols=80, rows=24):
    """Start the app in a pty, send `keys` (bytes, delay) pairs, return the stripped text."""
    import fcntl, signal, struct, termios
    home = _home(tmp_path)
    pid, fd = pty.fork()
    if pid == 0:
        # the size is set in the child, before anything reads it: setting it from the parent
        # after the fork races the application's own start-up
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        os.environ["TERM"] = "xterm"
        os.environ["COLUMNS"] = str(cols)      # curses prefers these to the real size when set,
        os.environ["LINES"] = str(rows)        # and the test runner sets them to 80x24
        os.environ["MCCONTROL_HOME"] = str(home)
        os.environ.update(env or {})
        sys.path.insert(0, os.getcwd())
        os.execvp(sys.executable, [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from mccontrol import cli; sys.exit(cli.main([]))" % os.getcwd()])
    time.sleep(0.8)
    for k, delay in keys:
        os.write(fd, k)
        time.sleep(delay)
    os.write(fd, b"\x0c")            # Ctrl+L: a full repaint, so the capture holds the whole last frame
    time.sleep(0.8)
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                out += os.read(fd, 65536)
            except OSError:
                break
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return re.sub(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]|\x1b[=>]", b" ", out).decode("utf-8", "replace"), home


F = {n: f"\x1b[{c}~".encode() for n, c in zip(range(5, 11), (15, 17, 18, 19, 20, 21))}
F.update({1: b"\x1bOP", 2: b"\x1bOQ", 3: b"\x1bOR", 4: b"\x1bOS"})
INS = b"\x1b[2~"
DOWN = b"\x1bOB"


def test_the_directory_shows_every_device_with_its_columns_and_tags(tmp_path):
    text, _ = walk(tmp_path, [(INS, 0.3), (INS, 0.3)])
    assert "Devices" in text and "History" in text
    assert "Gisebo" in text and "(unnamed)" in text and "FD700369" in text and "GOT" in text
    assert "2 of 3 tagged" in text
    assert re.search(r"1\s*Help", text) and re.search(r"10\s*Quit", text)


def test_f10_quits(tmp_path):
    text, _ = walk(tmp_path, [(F[10], 0.3)], seconds=1.5)
    assert "Devices" in text


def test_the_right_panel_follows_the_cursor_and_opens_a_reply(tmp_path):
    # the cursor starts on Far (sorted by name), moves down to Gisebo, whose history has one line
    text, _ = walk(tmp_path, [(DOWN, 0.4), (b"\t", 0.3), (b"\r", 0.5)])
    assert "History: Gisebo" in text
    assert "region" in text and "> * F" in text
    assert "topic t" in text                      # the reply dialog shows the topic line


def test_f2_sends_to_the_tagged_devices_and_shows_the_results(tmp_path):
    fake = json.dumps({"4AC61A7F": [0, "> * F"], "65C1D12C": [14, ""]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (INS, 0.3), (INS, 0.3), (F[2], 0.5),
                                 (b"region", 0.3), (b"\r", 1.5)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "2 tagged devices in JKG" in text
    assert "2 of 2 answered" in text
    assert "Results: region" in text
    assert "refused: not-allow" in text          # the 80-column panel cuts the rest
    hist = (home / "history" / "65C1D12C.jsonl").read_text()
    assert '"command": "region"' in hist


def test_a_command_that_takes_something_away_asks_first_and_esc_sends_nothing(tmp_path):
    fake = json.dumps({"4AC61A7F": [0, "OK"]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[2], 0.5), (b"region denyf *", 0.3), (b"\r", 0.6), (b"\x1b", 0.6)],
                      env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "stops this repeater forwarding every message" in text
    assert not (home / "history" / "4AC61A7F.jsonl").read_text().count("denyf")


def test_tags_across_two_regions_are_refused(tmp_path):
    text, _ = walk(tmp_path, [(INS, 0.3), (DOWN, 0.3), (INS, 0.3), (b"*", 0.3), (b"*", 0.3), (F[2], 0.6)])
    # rows: Far(GOT), Gisebo(JKG), (unnamed)(JKG); Ins on row 0 and 2 -> GOT and JKG tagged
    assert "span more than one region" in text


def test_f7_sends_to_every_node_in_a_region_and_hears_that_regions_devices(tmp_path):
    fake = json.dumps({"4AC61A7F": [0, "OK"], "65C1D12C": [0, "OK"], "FD700369": [0, "OK"]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[7], 0.5), (b"\r", 0.5), (b"region put se-jkp *", 0.3), (b"\r", 1.5)],
                      env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "EVERY node in JKG" in text
    assert "2 of 2 answered" in text                 # Far is in GOT and is not asked
    assert '"note": "broadcast"' in (home / "history" / "4AC61A7F.jsonl").read_text()
    assert not (home / "history" / "FD700369.jsonl").exists()


def test_f5_discovers_a_region_and_f7_remembers_everyone_with_names_where_the_fleet_gives_them(tmp_path):
    fake = json.dumps({"AB6B9285": [0, "> on"], "AB6B9285|get name": [0, "> SE-JKG-UGORptr"],
                       "4AC61A7F": [0, "> on"], "4AC61A7F|get name": [14, ""],
                       "13B58595": [17, ""], "13B58595|get name": [14, ""]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[5], 0.5), (b"\r", 1.5), (F[7], 1.0)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Found in JKG: 3" in text
    assert "= Gisebo" in text                               # the known one is marked
    assert "SE-JKG-UGORptr" in text
    devices = json.loads((home / "devices.json").read_text())
    names = {d["node"]: d["name"] for d in devices}
    assert names["AB6B9285"] == "SE-JKG-UGORptr" and names["13B58595"] == "" and len(devices) == 5
    assert "2 device(s) remembered" in text


def test_f5_again_adds_the_tagged_and_asks_for_a_name(tmp_path):
    fake = json.dumps({"AB6B9285": [0, "> on"], "AB6B9285|get name": [14, ""]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[5], 0.5), (b"\r", 1.5), (INS, 0.3), (F[5], 0.6), (b"UGO\r", 0.8)],
                      env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Name the device" in text
    assert {d["name"] for d in json.loads((home / "devices.json").read_text())} >= {"UGO"}


def test_f4_edits_the_cursor_device(tmp_path):
    # cursor on Far: rename it, Tab past region, Tab to notes, Enter saves
    text, home = walk(tmp_path, [(b"\x08\x08\x08Near", 0.3), (F[4], 0.5), (b"\x08\x08\x08Near", 0.3), (b"\r", 0.8)])
    names = {d["node"]: d["name"] for d in json.loads((home / "devices.json").read_text())}
    assert names["FD700369"] == "Near"
    assert "Saved." in text


def test_f6_reads_the_regions_of_the_cursor_device(tmp_path):
    fake = json.dumps({"FD700369": [0, "> * F\n se-jkp F"]})
    text, home = walk(tmp_path, [(F[6], 1.5)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Results: region" in text and "1 of 1 answered" in text
    assert '"command": "region"' in (home / "history" / "FD700369.jsonl").read_text()


def test_f8_forgets_after_a_confirm(tmp_path):
    text, home = walk(tmp_path, [(F[8], 0.5), (b"\x1bOD", 0.3), (b"\r", 0.8)])
    nodes = {d["node"] for d in json.loads((home / "devices.json").read_text())}
    assert "FD700369" not in nodes and len(nodes) == 2
    assert "Forgotten." in text


def test_the_keys_dialog_shows_the_provisioning_command_on_one_line(tmp_path):
    text, _ = walk(tmp_path, [(b"\x1bk", 0.6), (b"\r", 1.2)], cols=100, seconds=4.0)   # Alt+K opens Keys; Enter runs the item
    assert "set mqtt.cmd.pubkey" in text
    from mccontrol import envelope
    # the terminal stream may carry an attribute change inside the key, which strips to a space
    assert envelope.public_key_hex("11" * 32) in text.replace(" ", "")


def test_setup_saves_the_control_broker(tmp_path):
    text, home = walk(tmp_path, [(b"\x1bo", 0.5), (b"\r", 0.5), (b"\x08" * 20 + b"mqtt.example", 0.3), (b"\t", 0.2), (b"\t", 0.2),
                                 (b"mcoar", 0.3), (b"\t", 0.2), (b"pw", 0.3), (b"\r", 0.8)])
    cfg = json.loads((home / "config.json").read_text())
    assert cfg["broker"] == "mqtt.example" and cfg["username"] == "mcoar" and cfg["password"] == "pw"
    assert "Saved." in text




def test_f1_opens_help_for_the_screen_and_enter_follows_a_link(tmp_path):
    text, _ = walk(tmp_path, [(F[1], 0.6), (b"\r", 0.6)])
    assert "Help: The screen" in text
    assert "Help: Keys on the main screen" in text         # the first link on the screen topic


def test_the_command_line_prints_a_topic(tmp_path, capsys):
    from mccontrol import cli
    import os
    os.environ["MCCONTROL_HOME"] = str(tmp_path)
    assert cli.main(["help", "commands"]) == 0
    out = capsys.readouterr().out
    assert "region put" in out and "[cautions]" in out
    assert cli.main(["help", "nothing"]) == 1


def test_f3_opens_the_history_window(tmp_path):
    text, _ = walk(tmp_path, [(DOWN, 0.3), (F[3], 0.6)])
    assert "History: Gisebo" in text and "region" in text


def test_alt_digit_sends_the_shortcut_without_a_dialog_and_the_send_menu_lists_it(tmp_path):
    fake = json.dumps({"FD700369": [0, "MeshCore Observer 1.2"]})
    text, home = walk(tmp_path, [(b"\x1b2", 1.5), (b"\x1bs", 0.6), (b"\x1b", 0.3)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Results: ver" in text and "1 of 1 answered" in text
    assert '"command": "ver"' in (home / "history" / "FD700369.jsonl").read_text()
    assert "Alt+2" in text and "get name" in text            # the Send menu, opened with Alt+S


def test_an_unset_shortcut_says_so_and_options_edits_the_slots(tmp_path):
    text, home = walk(tmp_path, [(b"\x1b9", 0.5), (b"\x1bo", 0.5), (DOWN, 0.2), (DOWN, 0.2), (b"\r", 0.5),
                                 (b"\t" * 8, 0.4), (b"get flood.max", 0.3), (b"\r", 0.6), (b"\x1b9", 0.5)])
    assert "Alt+9 is not set" in text
    cfg = json.loads((home / "config.json").read_text())
    assert cfg["shortcuts"][8] == "get flood.max" and cfg["shortcuts"][0] == "region"
    assert "Shortcuts saved." in text


def test_f9_opens_the_menu(tmp_path):
    text, _ = walk(tmp_path, [(F[9], 0.5), (b"\x1b", 0.3)])
    assert "Sort by name" in text


def _click(x, y):
    """An SGR (1006) mouse report, press then release, as xterm sends it; x and y zero-based."""
    return f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m".encode()


def test_a_click_moves_the_cursor_presses_a_key_bar_label_and_opens_a_menu(tmp_path):
    # rows: Far(GOT) on row 3 (frame at 1, header at 2); a click on row 4 lands on Gisebo
    text, _ = walk(tmp_path, [(_click(10, 4), 0.6), (_click(58, 23), 0.6), (b"\x1b", 0.4), (_click(3, 0), 0.6), (b"\x1b", 0.3)])
    assert "History: Gisebo" in text                  # the right panel followed the click
    assert "Forget device" in text                    # key 8 (Del) at column 58 asked
    assert "Sort by name" in text                     # the Devices menu opened by a click on its title
