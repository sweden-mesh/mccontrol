"""The API against its own document: every route in the spec has a handler and every handler
a route; every response validates against its schema; the jobs run over the scripted broker."""
import json
import re
import time
import urllib.error
import urllib.request

import pytest

from conftest import BEACON_FAKE, SPEC, resolve, validate
from mccontrol import transport
from mccontrol.api import server as api_server

METHODS = ("get", "post", "put", "patch", "delete")
SEEN = set()


def call(url, method, path, body=None, check=True):
    """One request; the response is checked against the spec for its route and status."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, ctype, raw = resp.status, resp.headers["Content-Type"], resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status, ctype, raw = exc.code, exc.headers["Content-Type"], exc.read().decode("utf-8")
    payload = json.loads(raw) if ctype.startswith("application/json") else raw
    if check:
        conform(method, path.split("?")[0], status, ctype, payload)
    return status, payload


def conform(method, path, status, ctype, payload):
    template, _f, _p = api_server.match(method, path)
    op = SPEC["paths"][template][method.lower()]
    assert str(status) in op["responses"], f"{method} {template} answered {status}, not in the spec: {payload}"
    SEEN.add((method, template))
    response = resolve(op["responses"][str(status)])
    media = ctype.split(";")[0]
    assert media in response["content"], f"{method} {template}: {media}"
    errors = validate(response["content"][media]["schema"], payload)
    assert not errors, f"{method} {template} {status}: {errors}"
    if isinstance(payload, dict) and payload.get("status") in ("done", "cancelled") and payload.get("result") is not None:
        start = SPEC["paths"][f"/jobs/{payload['verb']}"]["post"]
        errors = validate(start["x-result-schema"], payload["result"])
        assert not errors, f"{payload['verb']} result: {errors}"


def finish(url, job, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        _s, job = call(url, "GET", f"/jobs/{job['id']}")
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job['id']} still running: {job['progress']}")


def run(url, verb, body=None):
    status, job = call(url, "POST", f"/jobs/{verb}", body or {})
    assert status == 202, job
    return finish(url, job)


def spec_routes():
    return {(m.upper(), p) for p, ops in SPEC["paths"].items() for m in ops if m in METHODS}


def test_every_route_in_the_spec_has_a_handler_and_every_handler_a_route():
    handlers = {(m, t) for m, t, _rx, _f in api_server.ROUTES}
    assert spec_routes() - handlers == set()
    assert handlers - spec_routes() == set()
    ids = [op["operationId"] for ops in SPEC["paths"].values() for m, op in ops.items() if m in METHODS]
    assert len(ids) == len(set(ids))


def test_every_schema_reference_resolves_and_every_job_names_its_result():
    text = json.dumps(SPEC)
    for ref in {part.split('"')[0] for part in text.split('"$ref": "')[1:]}:
        resolve({"$ref": ref})
    for path, ops in SPEC["paths"].items():
        if path.startswith("/jobs/") and "post" in ops:
            resolve(ops["post"]["x-result-schema"])


def test_every_route_answers_as_its_schema_says(api, monkeypatch):
    _server, url, _store = api
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", BEACON_FAKE)
    monkeypatch.setenv("MCCONTROL_FAKE_REGISTER", "ok")
    SEEN.clear()
    assert call(url, "GET", "/openapi.json")[1]["openapi"].startswith("3.0")
    assert "/openapi.json" in call(url, "GET", "/docs")[1]
    assert call(url, "GET", "/config")[1]["broker"] == "127.0.0.1"
    assert call(url, "PATCH", "/config", {"wait_secs": 5, "columns": ["ver", "reg"]})[1]["columns"] == ["reg", "ver"]
    assert call(url, "GET", "/key")[1]["exists"] is True
    assert call(url, "POST", "/key", {})[0] == 409
    assert call(url, "POST", "/key", {"overwrite": True, "seed": "22" * 32})[0] == 201
    listing = call(url, "GET", "/devices?sort=region&width=60")[1]
    assert [d["node"] for d in listing["devices"]] == ["FD700369", "4AC61A7F", "65C1D12C"]
    assert listing["footer"] == "3 devices" and len(listing["devices"][0]["row"]) <= 59
    assert call(url, "POST", "/devices", {"name": "LOUTEST", "node": "F415473D", "region": "jkg"})[1]["region"] == "JKG"
    assert call(url, "POST", "/devices", {"name": "again", "node": "F415473D", "region": "JKG"})[0] == 409
    assert call(url, "POST", "/devices/adopt", {"node": "AB6B9285", "region": "JKG", "name": "Gisebo"})[1]["name"] == "Gisebo-2"
    assert call(url, "GET", "/devices/gise")[0] == 404                          # ambiguous now
    assert call(url, "GET", "/devices/gisebo")[1]["node"] == "4AC61A7F"
    assert call(url, "PATCH", "/devices/LOUTEST", {"notes": "bench", "region": "got"})[1]["region"] == "GOT"
    assert call(url, "PATCH", "/devices/LOUTEST", {"name": "Far"})[0] == 409
    assert call(url, "DELETE", "/devices/LOUTEST")[1] == {"forgot": "F415473D"}
    assert call(url, "GET", "/devices/nobody")[0] == 404
    assert call(url, "GET", "/devices/gisebo/history")[1]["history"] == []
    assert call(url, "GET", "/history?limit=5")[1]["node"] is None
    assert call(url, "GET", "/devices/gisebo/reads")[1]["reads"] == []
    table = call(url, "GET", "/devices/gisebo/commands")[1]
    assert table["firmware"] == "ar-10" and table["greyed"] > 0           # no version read yet: the oldest
    assert call(url, "GET", "/commands")[1]["node"] is None
    check = call(url, "GET", "/commands/check?command=set+prv.key+00&device=gisebo")[1]
    assert check["refusal"].startswith("denied") and check["admitted"] is False
    assert call(url, "GET", "/commands/check")[0] == 400
    assert call(url, "POST", "/tags", {"region": "JKG"})[1]["footer"] == "3 of 4 tagged"
    assert call(url, "DELETE", "/tags?devices=gisebo")[1]["tagged"] == ["65C1D12C", "AB6B9285"]
    assert call(url, "POST", "/tags/invert", {})[1]["tagged"] == ["4AC61A7F", "FD700369"]
    assert call(url, "PUT", "/tags", {"nodes": ["far"]})[1]["tagged"] == ["FD700369"]
    assert call(url, "DELETE", "/tags")[1]["tagged"] == []
    assert call(url, "GET", "/tags")[1]["footer"] == "4 devices"
    assert call(url, "GET", "/regions")[1]["regions"] == ["JKG", "GOT"]
    assert "add-scope" in call(url, "GET", "/sets")[1]["sets"]
    assert call(url, "PUT", "/sets/reads", {"steps": ["ver", " get name "]})[1]["steps"] == ["ver", "get name"]
    assert call(url, "DELETE", "/sets/add-scope")[1] == {"removed": "add-scope"}
    assert call(url, "DELETE", "/sets/add-scope")[0] == 404
    assert call(url, "GET", "/shortcuts")[1]["shortcuts"][0] == "region"
    assert call(url, "PUT", "/shortcuts", {"shortcuts": ["ver"]})[1]["shortcuts"][:2] == ["ver", ""]
    assert any(t["name"] == "send" for t in call(url, "GET", "/help")[1]["topics"])
    assert call(url, "GET", "/help/index")[1]["links"]
    assert call(url, "GET", "/help/..%2F..%2Fetc%2Fpasswd")[0] == 404
    for verb, body in [("send", {"command": "region", "devices": ["gisebo"]}),
                       ("run-set", {"set": "reads", "devices": ["gisebo"]}),
                       ("discover", {"region": "JKG"}),
                       ("refresh-names", {"devices": ["gisebo"]}),
                       ("refresh-fleet", {"devices": ["gisebo"]}),
                       ("statistics", {"device": "gisebo", "live": False}),
                       ("register", {})]:
        assert run(url, verb, body)["status"] == "done", verb
    assert run(url, "test")["result"]["ok"] is False
    jobs = call(url, "GET", "/jobs")[1]["jobs"]
    assert jobs[0]["verb"] == "test" and len(jobs) == 8
    assert call(url, "DELETE", f"/jobs/{jobs[0]['id']}")[0] == 202
    assert call(url, "GET", "/jobs/nope")[0] == 404
    assert spec_routes() - SEEN == set(), "routes the walk did not exercise"


def test_errors_are_one_shape_with_proper_status(api):
    _server, url, _store = api
    assert call(url, "GET", "/nope", check=False) == (404, {"error": {"code": "not_found", "message": "no route /nope"}})
    status, body = call(url, "PUT", "/config", {}, check=False)
    assert status == 405 and body["error"]["code"] == "method_not_allowed"
    req = urllib.request.Request(url + "/config", data=b"{not json", method="PATCH")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req, timeout=5)
    assert exc.value.code == 400 and json.loads(exc.value.read())["error"]["code"] == "bad_request"
    assert call(url, "PATCH", "/config", {"colour": "green"})[1]["error"]["code"] == "bad_request"
    assert call(url, "PATCH", "/config", {"port": "1883"})[0] == 400
    assert call(url, "POST", "/devices", {"node": "XYZ", "region": "JKG"})[1]["error"]["code"] == "store"
    assert call(url, "POST", "/devices", {"node": "AAAAAAAA", "region": "J"})[1]["error"]["code"] == "store"


def test_a_send_is_refused_before_anything_is_signed(api):
    _server, url, _store = api
    status, body = call(url, "POST", "/jobs/send", {"command": "set prv.key 00", "devices": ["gisebo"]})
    assert status == 400 and body["error"]["code"] == "refused" and "denied" in body["error"]["message"]
    status, body = call(url, "POST", "/jobs/send", {"command": "trigger test", "region": "JKG"})
    assert status == 400 and "transmits" in body["error"]["message"]
    status, body = call(url, "POST", "/jobs/send", {"command": "region denyf *", "devices": ["gisebo"]})
    assert status == 409 and body["error"]["code"] == "confirm_required" and "no scope" in body["error"]["message"]
    assert call(url, "POST", "/jobs/send", {"command": "region", "devices": ["gisebo"], "region": "JKG"})[0] == 400
    assert call(url, "POST", "/jobs/send", {"command": "region", "tagged": True})[1]["error"]["message"] == "nothing is tagged"
    assert call(url, "POST", "/jobs/send", {"command": "region", "devices": ["nobody"]})[0] == 404
    assert call(url, "POST", "/jobs/run-set", {"set": "nope", "devices": ["gisebo"]})[0] == 404
    assert call(url, "POST", "/jobs/run-set", {"set": "add-scope", "devices": ["gisebo"]})[1]["error"]["code"] == "refused"
    assert call(url, "GET", "/jobs")[1]["jobs"] == []                       # nothing started


def test_a_send_to_the_tagged_lands_an_outcome_per_device_and_keeps_history(api):
    _server, url, store = api
    call(url, "POST", "/tags", {"region": "JKG"})
    job = run(url, "send", {"command": "region", "tagged": True})
    assert job["progress"]["text"] == "2 of 2 answered"        # a refusal is an answer
    assert [(o["node"], o["ok"], o["line"]) for o in job["outcomes"]] == [
        ("4AC61A7F", True, "> * F"), ("65C1D12C", False, "refused: not-allowed -- " + _help14())]
    assert job["result"]["answered"] == 2 and job["result"]["asked"] == 2
    assert [l["command"] for l in call(url, "GET", "/devices/4AC61A7F/history")[1]["history"]] == ["region"]
    assert call(url, "GET", "/devices/gisebo/reads")[1]["reads"][0]["reply"] == "> * F"


def _help14():
    from mccontrol import envelope
    return envelope.RESULT_HELP[14]


def test_a_region_broadcast_counts_the_known_nodes_and_a_dry_run_sends_nothing(api, monkeypatch):
    _server, url, store = api
    job = run(url, "send", {"command": "get autoreply", "region": "jkg"})
    assert job["progress"]["text"] == "2 of 2 known answered"
    assert {o["node"] for o in job["outcomes"]} == {"4AC61A7F", "65C1D12C"}
    assert job["result"]["topics"] == ["meshcore/JKG/all/cmd"]
    monkeypatch.setattr(transport, "send", lambda *a, **k: pytest.fail("sent"))
    job = run(url, "send", {"command": "region denyf *", "devices": ["gisebo"], "dry_run": True})
    assert job["result"]["lines"][-1] == "not sent (dry run)" and job["outcomes"][0]["line"] == "not sent (dry run)"


def test_a_job_is_cancelled_and_keeps_what_landed(api, monkeypatch):
    _server, url, store = api
    monkeypatch.setattr(transport, "FAKE_WAIT_SECS", 30.0)
    _s, job = call(url, "POST", "/jobs/send", {"command": "region", "devices": ["gisebo", "far"]})
    time.sleep(0.3)
    running = call(url, "GET", f"/jobs/{job['id']}")[1]
    assert running["status"] == "running" and "waiting for Gisebo (1 of 2)" in running["progress"]["text"]
    started = time.monotonic()
    assert call(url, "DELETE", f"/jobs/{job['id']}")[1]["cancel_requested"] is True
    job = finish(url, job, seconds=3.0)
    assert time.monotonic() - started < 2.0
    assert job["status"] == "cancelled" and job["result"]["asked"] == 1        # Far never sent
    assert [o["node"] for o in job["outcomes"]] == ["4AC61A7F"]


def test_a_command_set_stops_at_the_first_refusal(api):
    _server, url, store = api
    call(url, "PUT", "/sets/two", {"steps": ["region", "get autoreply"]})
    job = run(url, "run-set", {"set": "two", "devices": ["gisebo", "65C1D12C"]})
    assert job["result"]["runs"] == [{"target": "4AC61A7F", "stopped_at": None}, {"target": "65C1D12C", "stopped_at": 0}]
    assert [o["ok"] for o in job["outcomes"]] == [True, False]
    assert job["progress"]["steps"] == 2


def test_discovery_finds_names_and_adopts_with_add(api):
    _server, url, store = api
    call(url, "DELETE", "/devices/65C1D12C")
    job = run(url, "discover", {"region": "JKG", "add": True})
    found = {e["node"]: e for e in job["result"]["found"]}
    assert found["4AC61A7F"]["known_as"] == "Gisebo" and not found["4AC61A7F"]["added"]
    assert found["AB6B9285"]["added"] and found["AB6B9285"]["name"] == "SE-JKG-UGORptr"
    assert call(url, "GET", "/devices/AB6B9285")[1]["name"] == "SE-JKG-UGORptr"
    assert call(url, "POST", "/jobs/discover", {"region": " "})[0] == 400


def test_names_versions_fleet_and_statistics(api, monkeypatch):
    _server, url, store = api
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", BEACON_FAKE)
    job = run(url, "refresh-names", {"devices": ["65C1D12C"]})
    assert job["result"] == {"named": 1, "versioned": 1, "asked": 1, "lines": job["result"]["lines"]}
    assert call(url, "GET", "/devices/65C1D12C")[1]["name"] == "SE-JKG-Vaster-65c1"
    job = run(url, "refresh-fleet", {})
    assert job["result"] == {"updated": 1, "failed": 2, "asked": 3}
    assert call(url, "GET", "/devices/gisebo")[1]["cells"]["batt"].strip()
    job = run(url, "statistics", {"device": "gisebo", "range": "48h", "live": False})
    assert job["result"]["lines"][0].startswith("Beacon: sampled")
    assert call(url, "GET", "/devices/gisebo/reads")[1]["reads"][0]["command"] == "stats (Beacon)"
    job = run(url, "statistics", {"device": "far", "live": False})
    assert job["status"] == "failed" and job["error"]["code"] == "beacon"
    assert call(url, "POST", "/jobs/statistics", {"device": "gisebo", "range": "1y"})[0] == 400


def test_a_job_without_a_key_is_refused_at_once(api):
    _server, url, store = api
    store.key_path.unlink()
    status, body = call(url, "POST", "/jobs/send", {"command": "region", "devices": ["gisebo"]})
    assert status == 400 and body["error"]["code"] == "store" and "no signing key" in body["error"]["message"]
    assert call(url, "GET", "/key")[1] == {"exists": False, "public": None, "path": str(store.key_path),
                                          "registered": None, "provision": []}


def test_nothing_in_the_api_imports_curses_and_the_document_ships_with_the_package():
    import pathlib
    here = pathlib.Path(api_server.__file__).parent
    assert not [p.name for p in here.glob("*.py") if re.search(r"^\s*(import|from) +[\w.]*curses", p.read_text(encoding="utf-8"), re.M)]
    assert '"api/*.json"' in (here.parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert (here.parents[1] / "docs" / "api" / "openapi.json").samefile(here / "openapi.json")


def test_without_a_store_the_server_opens_the_one_in_mccontrol_home(tmp_path, monkeypatch):
    from mccontrol import store as _store
    monkeypatch.setenv("MCCONTROL_HOME", str(tmp_path))
    _store.Store().add_device("Home", "4AC61A7F", "JKG")
    server, url = api_server.serve()
    try:
        assert [d["name"] for d in call(url, "GET", "/devices")[1]["devices"]] == ["Home"]
    finally:
        api_server.stop(server)


def test_devices_can_be_listed_in_the_order_they_were_added(api):
    _server, url, _store = api
    listing = call(url, "GET", "/devices?sort=added")[1]
    assert [d["node"] for d in listing["devices"]] == ["4AC61A7F", "65C1D12C", "FD700369"]
    assert listing["sort"] == "added" and "added" in listing["sorts"]
