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


def test_an_api_error_out_of_an_interface_is_a_message_not_a_traceback(home, monkeypatch, capsys):
    def run(client):
        raise apiclient.ApiError("unreachable", "the mccontrol API cannot be reached", 0)
    monkeypatch.setattr(MeshcoreCommander, "run", run)
    assert cli.main(["--ui", "MeshcoreCommander"]) == 1
    assert capsys.readouterr().err == "mccontrol: the mccontrol API cannot be reached\n"


def test_discovery_says_why_when_the_regions_cannot_be_resolved():
    from mccontrol.ui.MeshcoreCommander.app import App

    class Client:
        def resolve_regions(self, text):
            raise apiclient.ApiError("unreachable", "the mccontrol API cannot be reached", 0)

        def start_discover(self, *a, **k):
            raise AssertionError("nothing may start")
    app = App.__new__(App)                       # no terminal: only what discover() reads
    app.client, app.right_mode, app.devices, refused = Client(), "history", [], []
    app.ask_region = lambda *a, **k: "JKG"
    app.refuse = lambda why, title="Not done": refused.append(why)
    app.discover()
    assert refused == ["the mccontrol API cannot be reached"]
