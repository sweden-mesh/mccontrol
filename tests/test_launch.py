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


def test_with_no_name_and_none_kept_monolith_starts_and_nothing_is_kept(home, ran, monkeypatch, capsys):
    from mccontrol.ui import Monolith
    started = []
    monkeypatch.setattr(Monolith, "run", lambda client: started.append(client.devices()["devices"]) or 0)
    assert cli.main([]) == 0
    assert started == [[]] and ran == [] and capsys.readouterr().out == ""
    assert not (home / "config.json").exists() or "ui" not in json.loads((home / "config.json").read_text())
    assert launch.interfaces()["MeshcoreCommander"] == MeshcoreCommander.DESCRIPTION


def test_the_name_used_is_kept_and_is_the_default_next_time(home, ran):
    assert cli.main(["--ui", "MeshcoreCommander"]) == 0
    assert json.loads((home / "config.json").read_text())["ui"] == "MeshcoreCommander"
    assert cli.main([]) == 0
    assert len(ran) == 2
    with pytest.raises(apiclient.ApiError) as exc:          # the server stopped with the interface
        ran[0].config()
    assert exc.value.code == "unreachable"


def test_the_name_is_matched_without_regard_to_case_and_kept_in_its_own_spelling(home, ran):
    assert cli.main(["--ui", "meshcorecommander"]) == 0
    assert json.loads((home / "config.json").read_text())["ui"] == "MeshcoreCommander"
    assert len(ran) == 1


def test_a_stored_name_that_is_gone_starts_monolith_says_so_and_is_forgotten(home, ran, monkeypatch, capsys):
    from mccontrol.ui import Monolith
    started = []
    monkeypatch.setattr(Monolith, "run", lambda client: started.append(client.config()["ui"]) or 0)
    (home / "config.json").write_text(json.dumps({"ui": "Retired"}))
    assert cli.main([]) == 0
    assert started == [""] and ran == []                    # running Monolith, the stored name already cleared
    assert capsys.readouterr().err == "mccontrol: the stored interface 'Retired' is gone; starting Monolith\n"
    assert json.loads((home / "config.json").read_text())["ui"] == ""
    assert cli.main([]) == 0 and capsys.readouterr().err == "" and len(started) == 2      # quietly, next time


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


@pytest.fixture
def running(home):
    """An API another mccontrol started: its base URL; stopped after the test."""
    from mccontrol.api.server import serve, stop
    server, url = serve()
    yield url
    stop(server)


def test_with_api_the_interface_runs_on_that_server_and_none_is_started(running, ran, monkeypatch):
    def serve(*a, **k):
        raise AssertionError("no server may be started")
    monkeypatch.setattr(launch, "serve", serve)
    assert cli.main(["--ui", "MeshcoreCommander", "--api", running]) == 0
    assert [c.base_url for c in ran] == [running.rstrip("/")]
    assert apiclient.Client(running).config()["ui"] == "MeshcoreCommander"   # kept by that server
    assert cli.main(["--api", running]) == 0                                 # and the default there
    assert len(ran) == 2
    assert ran[0].config()["ui"] == "MeshcoreCommander"                      # still up: not ours to stop


def test_an_unreachable_api_is_the_cli_message_and_exits_1(home, ran, monkeypatch, capsys):
    def serve(*a, **k):
        raise AssertionError("no server may be started")
    monkeypatch.setattr(launch, "serve", serve)
    url = "http://127.0.0.1:9/nothing/"
    assert cli.main(["--ui", "MeshcoreCommander", "--api", url]) == 1
    assert capsys.readouterr().err == "mccontrol: the mccontrol API at 127.0.0.1:9 cannot be reached: Connection refused\n"
    assert ran == []
