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
        os.environ["TERM"] = "xterm"
        os.environ["MCCONTROL_HOME"] = str(home)
        os.environ.update(env or {})
        sys.path.insert(0, os.getcwd())
        os.execvp(sys.executable, [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from mccontrol import cli; sys.exit(cli.main([]))" % os.getcwd()])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    time.sleep(0.8)
    for k, delay in keys:
        os.write(fd, k)
        time.sleep(delay)
    os.write(fd, b"\x0c")            # Ctrl+L: a full repaint, so the capture holds the whole last frame
    time.sleep(0.4)
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


def test_f1_shows_the_keys_and_f10_quits(tmp_path):
    text, _ = walk(tmp_path, [(F[1], 0.5), (b"\r", 0.3), (F[10], 0.3)])
    assert "F5  discover a region" in text


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
