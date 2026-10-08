from mccontrol import cli

NODE = "CCCCCCCC"


def test_the_command_line_round_trip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    assert cli.main(["keygen"]) == 0
    assert "set mqtt.cmd.pubkey" in capsys.readouterr().out
    assert cli.main(["keygen"]) == 1                        # refuses to overwrite
    assert cli.main(["config", "--username", "mcoar"]) == 0
    assert "mcoar" in capsys.readouterr().out
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
