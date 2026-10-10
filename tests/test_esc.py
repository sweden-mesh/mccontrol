"""A15 in all three interfaces: Esc closes a window at once, whatever ESCDELAY the shell
inherited; MCCONTROL_ESCDELAY sets a longer wait for a slow link. Monolith's own walk is in
test_monolith.py; here MeshcoreCommander and IBMi are timed the same way: Esc written to the pty
until the window's title is gone from the screen."""
import codecs
import fcntl
import os
import pathlib
import pty
import select
import signal
import struct
import sys
import termios
import time

import ptywalk
from conftest import NODES, closed_port

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import screenshots  # noqa: E402  (its terminal keeps the screen)


def _home(tmp_path):
    from mccontrol import store as _store
    st = _store.Store(tmp_path / "home")
    st.create_key()
    port = closed_port()
    st.save_config(broker="127.0.0.1", port=port, beacon_url=f"http://127.0.0.1:{port}")
    for name, node, region in NODES:
        st.add_device(name, node, region)
    return tmp_path / "home"


def esc_latency(tmp_path, ui, keys, title, env=None):
    """Seconds from Esc to `title` gone, after `keys` opened the window."""
    home = _home(tmp_path)
    pid, fd = pty.fork()
    if pid == 0:
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        os.environ.update(TERM="xterm", COLUMNS="80", LINES="24", MCCONTROL_HOME=str(home), LANG="C.UTF-8",
                          LC_ALL="C.UTF-8", ESCDELAY="1000", **(env or {}))
        os.environ.pop("MCCONTROL_ESCDELAY", None) if "MCCONTROL_ESCDELAY" not in (env or {}) else None
        os.execvp(sys.executable, [sys.executable, "-c", "import sys; sys.path.insert(0, %r); from mccontrol import cli; "
                                   "sys.exit(cli.main(['--ui', %r]))" % (str(ROOT), ui)])
    screen, decode = screenshots.Screen(80, 24), codecs.getincrementaldecoder("utf-8")("replace")

    def sink(data):
        screen.feed(decode.decode(data))

    def text():
        return "\n".join("".join(c for c, _a in row) for row in screen.grid)
    try:
        ptywalk.settle(fd, pid, 3.0, sink)
        for k, limit in keys:
            ptywalk.key(fd, pid, k, limit, sink)
        assert title in text(), text()
        started = time.perf_counter()
        os.write(fd, b"\x1b")
        while time.perf_counter() - started < 3.0:
            r, _, _ = select.select([fd], [], [], 0.001)
            if r:
                sink(os.read(fd, 65536))
                if title not in text():
                    return time.perf_counter() - started
        return None
    finally:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)


CASES = {"MeshcoreCommander": ([(b"\x1bOS", 0.8)], "Edit Far"),            # F4: the edit dialog
         "IBMi": ([(b"12\r", 0.8)], "Display Command History")}


def test_esc_closes_a_window_at_once_in_meshcorecommander_and_ibmi(tmp_path):
    for ui, (keys, title) in CASES.items():
        times = [esc_latency(tmp_path / f"{ui}{i}", ui, keys, title) for i in range(3)]
        print(f"{ui}: Esc to the window gone: {', '.join(f'{t * 1000:.1f}' if t else '-' for t in times)} ms")
        assert all(t is not None and t < 0.250 for t in times) and min(times) < 0.100, (ui, times)
        slow = esc_latency(tmp_path / f"{ui}-slow", ui, keys, title, env={"MCCONTROL_ESCDELAY": "400"})
        assert slow is not None and slow > 0.300, (ui, slow)                  # the escape hatch is taken


def test_the_esc_delay_is_25_ms_unless_mccontrol_escdelay_says_otherwise_in_both():
    from mccontrol.ui.IBMi.terminal import escdelay as ibmi
    from mccontrol.ui.MeshcoreCommander.screen import escdelay as commander
    for escdelay in (ibmi, commander):
        assert escdelay({}) == 25 and escdelay({"ESCDELAY": "1000"}) == 25
        assert escdelay({"MCCONTROL_ESCDELAY": "150"}) == 150 and escdelay({"MCCONTROL_ESCDELAY": "x"}) == 25
