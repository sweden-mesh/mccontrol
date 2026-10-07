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
