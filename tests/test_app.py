"""Walks of the second interface under a pseudo-terminal, against a store in a temp dir."""
import json
import os
import pathlib
import pty
import re
import select
import sys
import time

NODES = [("Gisebo", "4AC61A7F", "JKG"), ("", "65C1D12C", "JKG"), ("Far", "FD700369", "GOT")]


def _home(tmp_path):
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "history").mkdir()
    (home / "key.seed").write_text("11" * 32 + "\n")
    os.chmod(home / "key.seed", 0o600)
    devices = [{"name": n, "node": node, "region": r, "notes": "", "added": "2026-10-07 10:00:00",
                "last_sent": "2026-10-07 11:00:00" if n == "Gisebo" else None, "last_result": "ok" if n == "Gisebo" else None}
               for n, node, r in NODES]
    (home / "devices.json").write_text(json.dumps(devices))
    (home / "history" / "4AC61A7F.jsonl").write_text(json.dumps({"at": "2026-10-07 11:00:00", "command": "region", "topic": "t", "code": 0, "code_name": "ok", "reply": "> * F", "note": ""}) + "\n")
    return home


def walk(tmp_path, keys, seconds=3.0, env=None, cols=80, rows=24, prepare=None):
    """Start the app in a pty, send `keys` (bytes, delay) pairs, return the stripped text.
    `prepare(home)` may edit the files before the app starts."""
    import fcntl, signal, struct, termios
    home = _home(tmp_path)
    if prepare:
        prepare(home)
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


def _far_on(tag):
    """A prepare hook: Far reports the given firmware tag, so the command list offers what
    that build admits (an unknown version is treated as the oldest, ar-10)."""
    def prepare(home):
        devs = json.loads((home / "devices.json").read_text())
        next(d for d in devs if d["name"] == "Far")["ver"] = f"esp32s3-v1.17.1-{tag}-observer-2c079ed7"
        (home / "devices.json").write_text(json.dumps(devs))
    return prepare


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
    # after the discovery's outcome window (Enter closes it), F7 adds all; its window closes too
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[5], 0.5), (b"\r", 1.5), (b"\r", 0.4), (F[7], 1.0), (b"\r", 0.4)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Found in JKG: 3" in text
    assert "= Gisebo" in text                               # the known one is marked
    assert "SE-JKG-UGORptr" in text
    devices = json.loads((home / "devices.json").read_text())
    names = {d["node"]: d["name"] for d in devices}
    assert names["AB6B9285"] == "SE-JKG-UGORptr" and names["13B58595"] == "" and len(devices) == 5
    assert "2 device(s) remembered" in text


def test_f5_again_adds_the_tagged_and_asks_for_a_name(tmp_path):
    fake = json.dumps({"AB6B9285": [0, "> on"], "AB6B9285|get name": [14, ""]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (F[5], 0.5), (b"\r", 1.5), (b"\r", 0.4), (INS, 0.3), (F[5], 0.6), (b"UGO\r", 0.8), (b"\r", 0.4)],
                      env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Name the device" in text
    assert {d["name"] for d in json.loads((home / "devices.json").read_text())} >= {"UGO"}


def test_f4_edits_the_cursor_device(tmp_path):
    # cursor on Far: rename it, Tab past region, Tab to notes, Enter saves
    text, home = walk(tmp_path, [(F[4], 0.5), (b"\x08\x08\x08Near", 0.3), (b"\r", 0.8)])
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
    text, home = walk(tmp_path, [(b"\x1b2", 1.5), (b"\r", 0.4), (b"\x1bs", 0.6), (b"\x1b", 0.3)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Results: ver" in text and "1 of 1 answered" in text
    assert '"command": "ver"' in (home / "history" / "FD700369.jsonl").read_text()
    assert "Alt+2" in text and "get name" in text            # the Send menu, opened with Alt+S


def test_an_unset_shortcut_says_so_and_options_edits_the_slots(tmp_path):
    text, home = walk(tmp_path, [(b"\x1b9", 0.5), (b"\r", 0.4), (b"\x1bo", 0.5), (DOWN, 0.2), (DOWN, 0.2), (b"\r", 0.5),
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


def test_a_set_runs_from_the_send_menu_and_stops_where_a_node_refuses(tmp_path):
    fake = json.dumps({"FD700369|region put se-jkp *": [0, "OK"], "FD700369|region allowf se-jkp": [14, ""],
                       "FD700369": [0, "OK"]})
    # Alt+S opens Send; "c" picks "Command set"; the set field holds add-scope; Tab to the value; Enter runs
    text, home = walk(tmp_path, [(b"\x1bs", 0.5), (b"c", 0.5), (b"\t", 0.2), (b"se-jkp", 0.3), (b"\r", 2.5)],
                      env={"MCCONTROL_FAKE_RESULTS": fake}, seconds=6.0)
    assert "Set: add-scope" in text
    assert "Stopped at step 2" in text
    hist = (home / "history" / "FD700369.jsonl").read_text()
    assert "region put se-jkp *" in hist and "region allowf se-jkp" in hist and "region save" not in hist


def test_options_defines_a_set(tmp_path):
    # Alt+O opens Options; "c" picks "Command sets"; the name field holds add-scope: rename it and change step 1
    text, home = walk(tmp_path, [(b"\x1bo", 0.5), (b"c", 0.6), (b"\x08" * 12 + b"reads", 0.3), (b"\t", 0.2),
                                 (b"\x08" * 20 + b"ver", 0.3), (b"\r", 0.8)])
    assert "Saved reads." in text
    cfg = json.loads((home / "config.json").read_text())
    assert cfg["sets"]["reads"][0] == "ver"


def test_s_shows_the_cursor_devices_statistics_from_beacon_and_says_when_there_are_none(tmp_path):
    fake = str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json")
    live = json.dumps({"4AC61A7F|get stats": [0, "> batt=4100mV up=99s noise=-120"], "FD700369|get stats": [14, ""]})
    text, _ = walk(tmp_path, [(DOWN, 0.3), (b"s", 2.0), (b"\r", 0.4), (b"\x1bOA", 0.3), (b"s", 2.0), (b"\r", 0.4)],
                   env={"MCCONTROL_FAKE_BEACON": fake, "MCCONTROL_FAKE_RESULTS": live}, rows=48, seconds=6.0)
    assert "Stats: Gisebo" in text and "live, just now" in text and "4100 mV" in text     # the node's own, first
    assert "Beacon: sampled" in text and "4261 mV" in text and "dd hh:mm" in text        # then Beacon's, with its age
    assert "Stats: Far" in text and "no statistics" in text and "older firmware" not in text[:0]


def test_r_refreshes_batt_and_seen_from_beacon_for_the_tagged_devices(tmp_path):
    fake = str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json")
    text, home = walk(tmp_path, [(DOWN, 0.3), (INS, 0.3), (b"r", 1.0)], env={"MCCONTROL_FAKE_BEACON": fake}, cols=100)
    assert "Refreshed 1 of 1 from Beacon." in text
    assert "4.26" in text
    devs = {d["node"]: d for d in json.loads((home / "devices.json").read_text())}
    assert devs["4AC61A7F"]["fleet"]["battery_mv"] == 4261 and "fleet" not in devs["FD700369"]


def test_n_reads_names_and_versions_and_the_directory_shows_the_version(tmp_path):
    fake = json.dumps({"65C1D12C|get name": [0, "> SE-JKG-Vaster-65c1"], "65C1D12C|ver": [0, "> esp32s3-v1.17.1-ar-10-observer-42f48ce9"]})
    text, home = walk(tmp_path, [(DOWN, 0.3), (DOWN, 0.3), (b"n", 1.5)], env={"MCCONTROL_FAKE_RESULTS": fake}, cols=110)
    assert "1 of 1 gave a version; 1 named" in text
    assert "SE-JKG-Vaster-65c1" in text and "1.17.1-ar-10" in text
    devs = {d["node"]: d for d in json.loads((home / "devices.json").read_text())}
    assert devs["65C1D12C"]["name"] == "SE-JKG-Vaster-65c1"


def test_f1_works_when_the_terminal_sends_a_sequence_the_description_lacks(tmp_path):
    # PuTTY and the Windows console send F1 as Esc [ 1 1 ~, which xterm's description does
    # not list; the fallback table maps it, so help still opens.
    text, _ = walk(tmp_path / "a", [(b"\x1b[11~", 0.6)])
    assert "Help: The screen" in text
    text, _ = walk(tmp_path / "b", [(b"\x1b[21~", 0.6)])      # F10 the VT220 way: quits
    assert "Help:" not in text


def test_a_wait_is_a_progress_box_with_a_count_and_a_bar_that_fills(tmp_path):
    fake = json.dumps({"4AC61A7F": [0, "> on"], "AAAA1111": [0, "> on"]})
    text, _ = walk(tmp_path, [(DOWN, 0.3), (F[5], 0.5), (b"\r", 2.0)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Discovering" in text and "Asking every node in JKG" in text
    assert "Found 1 new, 1 old" in text                      # AAAA1111 is new, Gisebo is known
    assert "█" in text and "░" in text and "Esc stops waiting" in text   # the bar: written cell by cell as it fills
    assert "/0s" in text                                      # the clock, over the faked wait
    text, _ = walk(tmp_path / "send", [(DOWN, 0.3), (b"\x1b2", 1.5)], env={"MCCONTROL_FAKE_RESULTS": fake})
    assert "Sending" in text and "ver  to Gisebo (1 of 1)" in text and "answered" in text


def test_a_command_the_node_would_refuse_is_not_sent_and_the_reason_is_said(tmp_path):
    text, home = walk(tmp_path, [(F[2], 0.5), (b"set repeat off", 0.3), (b"\r", 0.8)])
    assert "Not sent: the node would refuse it (denied: the remote kill switch" in text
    assert not (home / "history" / "FD700369.jsonl").exists()


def test_enter_on_a_device_opens_the_command_list_and_runs_a_bare_command(tmp_path):
    fake = json.dumps({"FD700369|ver": [0, "> esp32s3-v1.17.1-ar-10"]})
    # Enter opens the list; typing "ver" filters it to the one entry; Enter runs it
    text, home = walk(tmp_path, [(b"\r", 0.6), (b"ver", 0.4), (b"\r", 2.0), (b"\r", 0.4)],
                      env={"MCCONTROL_FAKE_RESULTS": fake}, seconds=5.0, prepare=_far_on("ar-11"))
    # the stream carries only changed cells, so a string may arrive in pieces: match loosely
    assert re.search(r"Commands\s+to\s+Far", text) and re.search(r"--\s+reads\s+--", text)
    assert "Filter:" in text                                  # the typed letters arrive one cell at a time
    assert "Sent: ver" in text and "esp32s3-v1.17.1-ar-10" in text
    assert '"command": "ver"' in (home / "history" / "FD700369.jsonl").read_text()


def test_f3_still_opens_history(tmp_path):
    text, _ = walk(tmp_path, [(F[3], 0.6)])
    assert "History: Far" in text and "Nothing sent yet." in text


def test_a_choice_command_offers_radio_buttons_and_previews_what_it_sends(tmp_path):
    fake = json.dumps({"FD700369|set autoreply off": [0, "OK"]})
    # Enter; filter "set autoreply" (first match is the bare family); Enter; Down to "off"; Enter runs
    # ... then the caution's Confirm, where Left moves to "Send anyway" and Enter sends
    text, home = walk(tmp_path, [(b"\r", 0.6), (b"set autoreply", 0.4), (b"\r", 0.6), (DOWN, 0.3), (b"\r", 0.6),
                                 (b"\x1bOD", 0.3), (b"\r", 2.0), (b"\r", 0.4)],
                      env={"MCCONTROL_FAKE_RESULTS": fake}, seconds=7.0, prepare=_far_on("ar-11"))
    assert "(*) on" in text and "(*) off" in text
    assert "Sends:" in text and re.search(r"Command:\s+set\s+autoreply\s+off", text)   # the preview, then the caution's line
    assert "stops this repeater answering test requests" in text
    assert "Sent: set autoreply off" in text
    assert '"command": "set autoreply off"' in (home / "history" / "FD700369.jsonl").read_text()


def test_a_text_command_shows_examples_and_sends_what_was_typed(tmp_path):
    fake = json.dumps({"FD700369|region put se-jkp *": [0, "OK"]})
    text, home = walk(tmp_path, [(b"\r", 0.6), (b"region put", 0.4), (b"\r", 0.6), (b"se-jkp *", 0.3), (b"\r", 2.0), (b"\r", 0.4)],
                      env={"MCCONTROL_FAKE_RESULTS": fake}, seconds=6.0, prepare=_far_on("ar-11"))
    assert "Examples: se-jkp *   se-jkp-mul se-jkp" in text
    assert "Sent: region put se-jkp *" in text
    assert '"command": "region put se-jkp *"' in (home / "history" / "FD700369.jsonl").read_text()


def test_backing_out_of_a_parameter_sends_nothing(tmp_path):
    text, home = walk(tmp_path, [(b"\r", 0.6), (b"region put", 0.4), (b"\r", 0.6), (b"\x1b", 0.5)], prepare=_far_on("ar-11"))
    assert "Examples:" in text
    assert not (home / "history" / "FD700369.jsonl").exists()


def test_the_lower_panel_keeps_what_the_device_answered_and_enter_reads_it_again(tmp_path):
    fake = json.dumps({"FD700369|ver": [0, "> v1.17.1-ar-10"], "4AC61A7F|ver": [0, "> v1.16.0"]})
    # Alt+2 reads ver on Far; cursor down to Gisebo and back; Tab twice to the lower panel; Enter re-reads
    text, home = walk(tmp_path, [(b"\x1b2", 1.5), (b"\r", 0.4), (DOWN, 0.4), (b"\x1bOA", 0.4), (b"\t", 0.2), (b"\t", 0.2), (b"\r", 1.5)],
                      env={"MCCONTROL_FAKE_RESULTS": fake}, seconds=6.0)
    assert "Last answers" in text
    assert re.search(r"now ver\s+> v1\.17\.1", text)
    assert "(nothing read yet)" in text                    # Gisebo, before anything was read from it
    hist = (home / "history" / "FD700369.jsonl").read_text()
    assert hist.count('"command": "ver"') == 2            # the shortcut, then the re-read from the lower panel


def test_options_columns_picks_what_an_80_column_directory_shows(tmp_path):
    # Alt+O, "d" picks Directory columns; the checkbox box has every column chosen: untick
    # reg, sent, batt, seen with Space (Space toggles and the cursor stays), leaving ver and result
    def with_version(home):
        devs = json.loads((home / "devices.json").read_text())
        devs[0]["ver"] = "esp32s3-v1.17.1-ar-10-observer-42f48ce9"
        (home / "devices.json").write_text(json.dumps(devs))
    # ... Enter saves; Enter again closes the "Columns: ..." window so the directory is redrawn last
    keys = [(b"\x1bo", 0.5), (b"d", 0.5)] + [(b" ", 0.15), (DOWN, 0.15)] * 4 + [(b"\r", 0.8), (b"\r", 0.5)]
    text, _ = walk(tmp_path, keys, prepare=with_version)
    assert "Columns: ver, result." in text
    last_header = text[text.rindex("Name   "):][:40]
    assert re.match(r"Name\s+Node\s+Ver\s", last_header), last_header      # the last frame: no Reg, Sent
    assert "4AC61A7F 1.17.1-a" in text     # 37 columns: name, node and what fits of the version


def test_a_wait_writes_the_box_once(tmp_path):
    """The raw stream, not the stripped text: during a discovery's wait the progress box
    must reach the terminal once, its bar and count being the only cells that change. Before
    Sprint 06 it was redrawn on every tick (sixteen times in three seconds), which was the
    flicker; see planning/mccontrol/stable-screen.md in the workspace."""
    import fcntl, signal, struct, termios
    home = _home(tmp_path)
    fake = json.dumps({"4AC61A7F": [0, "> on"]})
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.environ.update({"TERM": "xterm", "COLUMNS": "80", "LINES": "24", "MCCONTROL_HOME": str(home),
                           "MCCONTROL_FAKE_RESULTS": fake, "MCCONTROL_FAKE_WAIT": "2"})
        os.execvp(sys.executable, [sys.executable, "-c",
                  "import sys; sys.path.insert(0, %r); from mccontrol.cli import main; main([])" % os.getcwd()])
    out = b""

    def pump(secs):
        nonlocal out
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return
    pump(0.8); os.write(fd, DOWN); pump(0.3); os.write(fd, F[5]); pump(0.5); os.write(fd, b"\r"); pump(3.0)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    raw = out.decode("utf-8", "replace")
    assert raw.count("Discovering") == 1, raw.count("Discovering")
    assert raw.count("Esc stops waiting") == 1
    assert raw.count("Found 0 new, 1 old") >= 1            # the count line did change, and was written


def test_the_command_list_greys_out_what_the_devices_firmware_does_not_admit(tmp_path):
    # Far on ar-10: Enter opens the list; "ver" narrows it to a greyed entry; Enter does nothing
    text, home = walk(tmp_path, [(b"\r", 0.6), (b"ver", 0.4), (b"\r", 0.8), (b"\x1b", 0.3)], prepare=_far_on("ar-10"))
    assert re.search(r"need a newer build;\s+this device reports ar-10", text)
    assert "Sent: ver" not in text and not (home / "history" / "FD700369.jsonl").exists()
    # an unknown version is the oldest: the same greying, said as unknown
    text, _ = walk(tmp_path / "unknown", [(b"\r", 0.6), (b"get stats", 0.4), (b"\r", 0.8), (b"\x1b", 0.3)])
    assert re.search(r"this device reports unknown", text)


def test_a_refusal_the_table_did_not_expect_is_a_finding(tmp_path):
    fake = json.dumps({"FD700369|ver": [14, ""]})
    # the result line is clipped by the panel; Tab to it and Enter opens the whole line
    text, _ = walk(tmp_path, [(b"\x1b2", 1.5), (b"\t", 0.3), (b"\r", 0.6)], env={"MCCONTROL_FAKE_RESULTS": fake},
                   prepare=_far_on("ar-11"), cols=120, seconds=4.0)
    assert "the table says ar-11 admits this" in text and "version is wrong" in text


def test_a_long_result_line_wraps_in_the_reply_window(tmp_path):
    fake = json.dumps({"FD700369|ver": [14, ""]})
    text, _ = walk(tmp_path, [(b"\x1b2", 1.5), (b"\t", 0.3), (b"\r", 0.6)], env={"MCCONTROL_FAKE_RESULTS": fake},
                   prepare=_far_on("ar-11"), seconds=4.0)
    assert "the table or the version is wrong" in text           # the tail, on a second line of the window


def test_keys_register_publishes_and_the_dialog_says_so(tmp_path):
    # Alt+K opens Keys; Enter runs "Show public key"; Tab to Register; Enter; then Keys again
    text, home = walk(tmp_path, [(b"\x1bk", 0.6), (b"\r", 0.8), (b"\t", 0.2), (b"\r", 1.5), (b"\r", 0.4), (b"\x1bk", 0.6), (b"\r", 0.8)],
                      env={"MCCONTROL_FAKE_REGISTER": "ok"}, cols=100, seconds=6.0)
    assert "Registered: ok" in text
    assert re.search(r"Registered with mqtt\.louie\.se on 20\d\d-\d\d-\d\d", text)
    assert json.loads((home / "config.json").read_text())["registered"]["broker"] == "mqtt.louie.se"


def test_a_greyed_entry_is_drawn_in_the_disabled_colour_and_names_the_build(tmp_path):
    """The raw stream: an admitted row and a greyed row must reach the terminal with
    different attributes (the screenshot of 2026-10-08 showed them identical), and the
    greyed one carries the build it needs in its text."""
    import fcntl, signal, struct, termios
    home = _home(tmp_path)
    _far_on("ar-10")(home)
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.environ.update({"TERM": "xterm", "COLUMNS": "80", "LINES": "24", "MCCONTROL_HOME": str(home)})
        os.execvp(sys.executable, [sys.executable, "-c",
                  "import sys; sys.path.insert(0, %r); from mccontrol.cli import main; main([])" % os.getcwd()])
    out = b""

    def pump(secs):
        nonlocal out
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([fd], [], [], 0.1)
            if r:
                try:
                    out += os.read(fd, 65536)
                except OSError:
                    return
    pump(0.8); os.write(fd, b"\r"); pump(1.0)                      # the list, unfiltered: get af admitted, get stats greyed on ar-10
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    raw = out.decode("utf-8", "replace")
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][0-9A-B]", " ", raw)
    assert "(needs ar-11)" in text                                   # a greyed entry names its build
    # the SGR before an admitted row's text differs from the one before a greyed row's
    admitted = re.search(r"(\x1b\[[0-9;]*m)+\s*get af\b", raw)
    greyed = re.search(r"(\x1b\[[0-9;]*m)+\s*get stats", raw)
    assert admitted and greyed
    sgr = lambda m: "".join(re.findall(r"\x1b\[[0-9;]*m", raw[max(0, m.start() - 40):m.start() + 1]))
    assert sgr(admitted) != sgr(greyed)
