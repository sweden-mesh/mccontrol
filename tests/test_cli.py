import ast
import pathlib

import pytest

from conftest import closed_port
from mccontrol import apiclient, cli, transport
from mccontrol.api.server import stop

NODE = "CCCCCCCC"


@pytest.fixture(autouse=True)
def quick_broker(monkeypatch):
    """Every command starts its own API over MCCONTROL_HOME; the scripted broker answers fast."""
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 0.05)


def test_the_command_line_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["keygen"]) == 0
    assert "set mqtt.cmd.pubkey" in capsys.readouterr().out
    assert cli.main(["keygen"]) == 1                        # refuses to overwrite
    assert cli.main(["config", "--username", "mcoar"]) == 0
    out = capsys.readouterr().out
    assert "username     mcoar\npassword     (not set)\n" in out and "password_set" not in out
    assert cli.main(["config", "--password", "hunter2"]) == 0
    out = capsys.readouterr().out
    assert "password     (set)\n" in out and "hunter2" not in out
    assert cli.main(["add", "LOUTEST", NODE, "jkg", "bench"]) == 0
    assert cli.main(["devices"]) == 0
    out = capsys.readouterr().out
    assert "LOUTEST" in out and "JKG" in out
    assert cli.main(["send", "loutest", "region", "put", "se-jkp", "*", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would publish to meshcore/JKG/" + NODE + "/cmd" in out and "not sent" in out
    assert cli.main(["send", "nobody", "region", "--dry-run"]) == 1
    assert "no device matches" in capsys.readouterr().err


def test_shortcuts_list_set_and_send_from_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["keygen"]) == 0
    assert cli.main(["add", "LOUTEST", NODE, "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["shortcuts"]) == 0
    out = capsys.readouterr().out
    assert "Alt+1  region" in out and "Alt+2  ver" in out and "Alt+9  -" in out
    assert cli.main(["shortcuts", "--set", "9", "get flood.max"]) == 0
    assert "Alt+9  get flood.max" in capsys.readouterr().out
    assert cli.main(["alt", "9", "loutest", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "get flood.max" in out and "not sent" in out
    assert cli.main(["shortcuts", "--set", "9", ""]) == 0
    capsys.readouterr()
    assert cli.main(["alt", "9", "loutest", "--dry-run"]) == 1
    assert "not set" in capsys.readouterr().err
    assert cli.main(["shortcuts", "--set", "10", "x"]) == 1


def test_command_sets_from_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["keygen"]) == 0
    assert cli.main(["add", "LOUTEST", NODE, "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["sets"]) == 0
    out = capsys.readouterr().out
    assert "add-scope" in out and "1. region put {arg} *" in out
    assert cli.main(["run", "add-scope", "loutest", "--dry-run"]) == 1          # needs {arg}
    assert "{arg}" in capsys.readouterr().err
    assert cli.main(["run", "add-scope", "loutest", "se-jkp", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "-- 1/4: region put se-jkp *" in out and "-- 4/4: region" in out
    assert cli.main(["sets", "--set", "reads", "ver", "get name"]) == 0
    assert "reads\n  1. ver\n  2. get name" in capsys.readouterr().out
    assert cli.main(["sets", "--remove", "add-scope"]) == 0
    assert "add-scope" not in capsys.readouterr().out
    assert cli.main(["run", "nothing", "loutest"]) == 1


def test_stats_from_the_command_line(tmp_path, monkeypatch, capsys):
    import pathlib
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json"))
    assert cli.main(["add", "Gisebo", "4AC61A7F", "jkg", ""]) == 0
    assert cli.main(["add", "Braneryd", "FD700369", "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["stats", "gisebo"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Beacon: sampled ") and "battery            4261 mV" in out and "last 24h at 6h:" in out
    assert out.count("\n") >= 17
    assert cli.main(["stats", "braneryd"]) == 1
    assert "never published" in capsys.readouterr().err
    assert cli.main(["config", "--beacon-url", "https://beacon.example/api/v1"]) == 0
    assert "beacon.example" in capsys.readouterr().out


def test_fleet_refreshes_battery_and_seen_from_beacon(tmp_path, monkeypatch, capsys):
    import pathlib
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json"))
    assert cli.main(["add", "Gisebo", "4AC61A7F", "jkg", ""]) == 0
    assert cli.main(["add", "Braneryd", "FD700369", "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["fleet"]) == 1                        # one device has nothing on Beacon
    out = capsys.readouterr().out
    assert "Batt  Seen" in out and "4.26" in out and "refreshed 1 of 2; 1 without statistics" in out
    assert cli.main(["fleet", "gisebo"]) == 0


def test_refresh_reads_name_and_version_over_the_control_plane(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("MCCONTROL_FAKE_RESULTS", json.dumps({f"{NODE}|get name": [0, "> Gisebo"], f"{NODE}|ver": [0, "> v1.17.1-ar-10"]}))
    assert cli.main(["keygen"]) == 0
    assert cli.main(["add", "", NODE, "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["refresh"]) == 0
    out = capsys.readouterr().out
    assert "get name: Gisebo" in out and "1 of 1 gave a version; 1 named" in out
    assert cli.main(["devices"]) == 0
    assert "Gisebo" in capsys.readouterr().out


def test_commands_lists_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["commands"]) == 0
    out = capsys.readouterr().out
    assert "reads:\n  ver   (kept)" in out and "set autoreply <on|off>" in out and "region put <se-jkp *>" in out


def test_reads_prints_what_the_device_last_answered(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("MCCONTROL_FAKE_RESULTS", json.dumps({f"{NODE}|ver": [0, "> v1.17.1-ar-10"]}))
    assert cli.main(["keygen"]) == 0
    assert cli.main(["add", "LOUTEST", NODE, "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["reads", "loutest"]) == 0 and "nothing read yet" in capsys.readouterr().out
    assert cli.main(["send", "loutest", "ver"]) == 0
    capsys.readouterr()
    assert cli.main(["reads", "loutest"]) == 0
    assert "ver                          > v1.17.1-ar-10" in capsys.readouterr().out


def test_commands_marks_what_a_devices_firmware_lacks(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["add", "Old", NODE, "jkg", ""]) == 0
    capsys.readouterr()
    assert cli.main(["commands", "old"]) == 0
    out = capsys.readouterr().out
    assert "old: firmware ar-10 (version unknown" in out
    assert "  ver   (kept)   -- needs ar-11" in out and "  get autoreply   (kept)\n" in out


def test_register_from_the_command_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    monkeypatch.setenv("MCCONTROL_FAKE_REGISTER", "ok")
    assert cli.main(["keygen"]) == 0
    capsys.readouterr()
    assert cli.main(["register"]) == 0 and "registered: ok" in capsys.readouterr().out
    assert cli.main(["key"]) == 0 and "registered with mqtt.louie.se on" in capsys.readouterr().out
    monkeypatch.setenv("MCCONTROL_FAKE_REGISTER", "refused locked out")
    assert cli.main(["register"]) == 1 and "not registered: refused locked out" in capsys.readouterr().out


def test_api_uses_a_running_server_and_starts_none(api, monkeypatch, capsys):
    _server, url, store = api
    monkeypatch.setattr(cli, "serve", lambda *a, **k: pytest.fail("--api must not start a server"))
    assert cli.main(["--api", url, "devices"]) == 0
    out = capsys.readouterr().out
    assert out.index("Gisebo") < out.index("65C1D12C") < out.index("Far")         # the order they were added
    assert cli.main(["--api", url, "send", "gisebo", "region"]) == 0
    assert "Gisebo       > * F" in capsys.readouterr().out
    assert store.history("4AC61A7F")[-1]["command"] == "region"
    assert cli.main(["--api", f"http://127.0.0.1:{closed_port()}", "devices"]) == 1
    assert "cannot be reached" in capsys.readouterr().err


def test_a_job_shows_its_progress_on_stderr_and_a_dry_run_shows_none(api, monkeypatch, capsys):
    _server, url, _store = api
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 0.6)
    assert cli.main(["--api", url, "send", "gisebo", "ver"]) == 0
    out, err = capsys.readouterr()
    assert "waiting for Gisebo (1 of 1)" in err and "waiting" not in out
    assert cli.main(["--api", url, "send", "gisebo", "ver", "--dry-run"]) == 0
    assert capsys.readouterr().err == ""
    assert cli.main(["--api", url, "run", "add-scope", "gisebo", "se-jkp", "--dry-run"]) == 0
    assert capsys.readouterr().err == ""

def test_ctrl_c_cancels_the_job_before_exiting(api, monkeypatch, capsys):
    server, url, _store = api
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 5)

    wait_job = apiclient.Client.wait_job

    def interrupted(self, job_id, on_progress=None, **_k):
        """Ctrl+C once the job is waiting on the broker."""
        def tick(job):
            on_progress(job)
            if "waiting" in job["progress"]["text"]:
                raise KeyboardInterrupt
        return wait_job(self, job_id, on_progress=tick, timeout=5)
    monkeypatch.setattr(apiclient.Client, "wait_job", interrupted)
    assert cli.main(["--api", url, "send", "gisebo", "ver"]) == 130
    err = capsys.readouterr().err
    assert "cancelled" in err and "waiting for Gisebo" in err
    job = apiclient.Client(url).jobs()["jobs"][0]
    assert job["verb"] == "send" and job["cancel_requested"]
    assert wait_job(apiclient.Client(url), job["id"], timeout=5)["status"] == "cancelled"


def test_ctrl_c_as_the_job_finishes_still_exits_cancelled(api, monkeypatch, capsys):
    _server, url, _store = api
    wait_job = apiclient.Client.wait_job

    def finished_then_interrupted(self, job_id, on_progress=None, **_k):
        wait_job(self, job_id, timeout=5)
        raise KeyboardInterrupt
    monkeypatch.setattr(apiclient.Client, "wait_job", finished_then_interrupted)
    assert cli.main(["--api", url, "send", "gisebo", "ver"]) == 130
    assert capsys.readouterr().err.endswith("cancelled\n")

def test_ctrl_c_outside_a_job_exits_130_and_stops_the_server(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    stopped = []
    monkeypatch.setattr(cli, "stop", lambda server: (stopped.append(server), stop(server)))
    call = apiclient.Client.call

    def interrupted(self, method, path, *a, **k):
        if path == "/devices":
            raise KeyboardInterrupt
        return call(self, method, path, *a, **k)
    monkeypatch.setattr(apiclient.Client, "call", interrupted)
    assert cli.main(["devices"]) == 130
    assert capsys.readouterr().err == "interrupted\n" and len(stopped) == 1

def test_the_command_line_imports_only_the_client_the_server_and_the_stdlib():
    """Every import in cli.py is the stdlib, the API client or the server that starts the
    backend; the one exception is the door to the screens, the launcher."""
    import sys
    tree = ast.parse(pathlib.Path(cli.__file__).read_text(encoding="utf-8"))
    screens = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "args.command is None")
    exempt = {id(n) for n in ast.walk(screens)}
    found = set()
    for n in ast.walk(tree):
        if id(n) in exempt:
            continue
        if isinstance(n, ast.Import):
            found |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            found |= {base} if n.module else {base + a.name for a in n.names}
    package = {m for m in found if m.startswith(".")}
    assert package == {".apiclient", ".api.server"}, package
    assert not {m for m in found - package if m.split(".")[0] not in sys.stdlib_module_names}
    assert {(a.name) for n in ast.walk(screens) if isinstance(n, ast.ImportFrom) for a in n.names} == {"launch"}
