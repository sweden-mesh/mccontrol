"""The client against a live server on a free port: one method per operation, parsed JSON,
ApiError for failures, and wait_job over a running job."""
import inspect
import time

import pytest

from conftest import SPEC
from mccontrol import apiclient, transport


def test_every_operation_has_a_client_method_and_the_client_imports_nothing_of_the_backend():
    ids = {op["operationId"] for ops in SPEC["paths"].values() for op in ops.values()}
    assert ids - set(dir(apiclient.Client)) == set()
    source = inspect.getsource(apiclient)
    assert "from ." not in source and "import mccontrol" not in source


def test_the_client_round_trips_against_the_live_server(api):
    _server, url, _store = api
    c = apiclient.Client(url)
    assert c.openapi()["info"]["title"] == "mccontrol"
    assert "swagger-ui-dist" in c.docs_page()
    assert c.set_config(wait_secs=7)["wait_secs"] == 7 and c.config()["wait_secs"] == 7
    assert c.key()["exists"] and len(c.create_key(overwrite=True)["public"]) == 64
    assert c.add_device("A1A1A1A1", "jkg", name="Spare åäö")["name"] == "Spare åäö"
    assert c.device("Spare åäö")["node"] == "A1A1A1A1"
    assert c.edit_device("A1A1A1A1", notes="attic")["notes"] == "attic"
    assert c.adopt("B2B2B2B2", "JKG")["notes"] == "found by discovery"
    assert [d["node"] for d in c.devices(sort="version", width=80)["devices"]][0] == "FD700369"
    assert c.history("gisebo")["history"] == [] and c.broadcast_history(limit=3)["history"] == []
    assert c.reads("gisebo")["reads"] == []
    assert c.device_commands("gisebo")["groups"] and c.commands()["groups"]
    assert c.check_command("region denyf *")["caution"]
    assert c.tag(region="JKG")["footer"] == "4 of 5 tagged"
    assert c.untag(devices=["gisebo", "A1A1A1A1"])["tagged"] == ["65C1D12C", "B2B2B2B2"]
    assert len(c.invert_tags()["tagged"]) == 3 and c.set_tags(["far"])["tagged"] == ["FD700369"]
    assert c.untag()["tagged"] == [] and c.tags()["tagged"] == []
    assert c.regions()["regions"] == ["JKG", "GOT"]
    assert c.save_set("x", ["ver"])["steps"] == ["ver"] and "x" in c.sets()["sets"]
    assert c.remove_set("x") == {"removed": "x"}
    assert c.set_shortcuts(["ver", "region"])["shortcuts"][:2] == c.shortcuts()["shortcuts"][:2]
    assert c.help_topic(c.help_index()["topics"][0]["name"])["lines"]
    assert c.forget("B2B2B2B2") == {"forgot": "B2B2B2B2"}
    seen = []
    job = c.start_send("region", devices=["gisebo"])
    done = c.wait_job(job["id"], on_progress=lambda j: seen.append(j["progress"]["text"]), poll=0.02)
    assert done["status"] == "done" and done["result"]["answered"] == 1 and seen
    assert c.job(job["id"])["status"] == "done" and c.jobs()["jobs"][0]["id"] == job["id"]
    for start in (lambda: c.start_run_set("add-scope", arg="se-jkp", devices=["gisebo"], dry_run=True),
                  lambda: c.start_discover("JKG"), lambda: c.start_refresh_names(devices=["gisebo"]),
                  lambda: c.start_test()):
        assert c.wait_job(start()["id"], poll=0.02, timeout=15)["status"] == "done"


def test_failures_are_api_errors_with_code_and_status(api):
    _server, url, _store = api
    c = apiclient.Client(url)
    with pytest.raises(apiclient.ApiError) as exc:
        c.device("nobody")
    assert (exc.value.code, exc.value.status) == ("not_found", 404) and "nobody" in exc.value.message
    with pytest.raises(apiclient.ApiError) as exc:
        c.start_send("region denyf *", devices=["gisebo"])
    assert (exc.value.code, exc.value.status) == ("confirm_required", 409)
    with pytest.raises(apiclient.ApiError) as exc:
        c.create_key(overwrite=True, seed="")             # an empty import is refused, not a new key
    assert exc.value.status == 400 and exc.value.message.startswith("not a seed")
    with pytest.raises(apiclient.ApiError) as exc:
        c.start_statistics("gisebo", range="1y")
    assert exc.value.status == 400
    with pytest.raises(apiclient.ApiError) as exc:
        apiclient.Client("http://127.0.0.1:9", timeout=2).config()
    assert (exc.value.code, exc.value.status) == ("unreachable", 0)


def test_wait_job_cancels_when_on_progress_says_so(api, monkeypatch):
    _server, url, _store = api
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 30.0)
    c = apiclient.Client(url)
    job = c.start_send("region", region="JKG", confirm=True)
    started = time.monotonic()
    done = c.wait_job(job["id"], on_progress=lambda j: j["progress"]["elapsed"] > 0.2, poll=0.05, timeout=5)
    assert done["status"] == "cancelled" and time.monotonic() - started < 3
    job = c.start_send("region", region="JKG")
    with pytest.raises(apiclient.ApiError) as exc:
        c.wait_job(job["id"], poll=0.05, timeout=0.1)
    assert exc.value.code == "timeout"
    assert c.job(job["id"])["cancel_requested"] is True
    assert c.wait_job(job["id"], poll=0.05, timeout=3)["status"] == "cancelled"
    with pytest.raises(apiclient.ApiError) as exc:
        c.cancel_job(job["id"])
    assert (exc.value.code, exc.value.status) == ("finished", 409)


def test_two_servers_keep_their_own_scripted_broker(api, tmp_path):
    from mccontrol import store as _store
    from mccontrol.api import serve, stop
    _server, url, _s = api
    other = _store.Store(tmp_path / "other")
    other.create_key()
    other.add_device("Gisebo", "4AC61A7F", "JKG")
    server2, url2 = serve(other, transport={"4AC61A7F": [0, "> from two"]})
    first, second = apiclient.Client(url), apiclient.Client(url2)
    try:
        assert second.wait_job(second.start_send("region", devices=["gisebo"])["id"])["outcomes"][0]["reply"] == "> from two"
    finally:
        stop(server2)
    assert first.wait_job(first.start_send("region", devices=["gisebo"])["id"])["outcomes"][0]["reply"] == "> * F"
