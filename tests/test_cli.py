from mccontrol import cli

NODE = "C" * 64


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
