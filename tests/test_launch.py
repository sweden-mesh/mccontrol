"""The launcher: `mccontrol --ui <name>` lists the interfaces, runs one over an in-process API,
and remembers the last one used."""
import json

import pytest

from mccontrol import apiclient, cli, launch
from mccontrol.ui import MeshcoreCommander


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def ran(monkeypatch):
    """MeshcoreCommander's `run` replaced by one that records the client and asks the API."""
    clients = []

    def run(client):
        clients.append(client)
        assert client.devices()["devices"] == []            # the API is up while the interface runs
        return 0
    monkeypatch.setattr(MeshcoreCommander, "run", run)
    return clients


def test_with_no_name_and_none_kept_the_interfaces_are_listed(home, ran, capsys):
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "MeshcoreCommander" in out and MeshcoreCommander.DESCRIPTION in out
    assert launch.interfaces()["MeshcoreCommander"] == MeshcoreCommander.DESCRIPTION
    assert ran == []


def test_the_name_used_is_kept_and_is_the_default_next_time(home, ran):
    assert cli.main(["--ui", "MeshcoreCommander"]) == 0
    assert json.loads((home / "config.json").read_text())["ui"] == "MeshcoreCommander"
    assert cli.main([]) == 0
    assert len(ran) == 2
    with pytest.raises(apiclient.ApiError) as exc:          # the server stopped with the interface
        ran[0].config()
    assert exc.value.code == "unreachable"


def test_an_unknown_name_lists_the_interfaces_and_exits_2(home, ran, capsys):
    assert cli.main(["--ui", "Nothing"]) == 2
    err = capsys.readouterr().err
    assert "MeshcoreCommander" in err and "no interface named 'Nothing'" in err
    assert not (home / "config.json").exists() or "ui" not in json.loads((home / "config.json").read_text())
    assert ran == []
