import json
import pathlib

from mccontrol import actions, nodestats, store as _store

REPLY = "> batt=4261mV up=1496188s noise=-114 tx=5518s rx=25369s sent=9065 recv=44345 err=10 rxerr=13369 q=0"
FAKE = str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json")


def test_the_short_pairs_map_to_the_status_blocks_names():
    block = nodestats.parse(REPLY)
    assert block == {"battery_mv": 4261, "uptime_secs": 1496188, "noise_floor": -114, "tx_air_secs": 5518,
                     "rx_air_secs": 25369, "packets_sent": 9065, "packets_received": 44345, "errors": 10,
                     "recv_errors": 13369, "queue_len": 0}
    assert nodestats.parse("> batt=4000mV") == {"battery_mv": 4000}
    assert nodestats.parse("> (no stats sources)") == {} and nodestats.parse("OK") == {}
    assert nodestats.parse("> batt=xmV junk=1") == {}


def test_the_live_sample_comes_first_and_an_older_node_falls_through_to_beacon(tmp_path, monkeypatch):
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", FAKE)
    monkeypatch.setenv("MCCONTROL_FAKE_RESULTS", json.dumps({"4AC61A7F|get stats": [0, REPLY]}))
    monkeypatch.setattr(actions.transport, "FAKE_WAIT_SECS", 0.0)
    store = _store.Store(tmp_path / "home")
    store.create_key()
    store.add_device("Gisebo", "4AC61A7F", "JKG")
    lines = actions.statistics(store, store.device("gisebo"), live=True)
    assert lines[0] == "live, just now (get stats)" and "battery            4261 mV" in lines[1:12]
    assert any(l.startswith("Beacon: sampled") for l in lines)
    assert [c for c, _r, _at in store.reads("gisebo")] == ["get stats"]          # kept as a read
    monkeypatch.setenv("MCCONTROL_FAKE_RESULTS", json.dumps({"4AC61A7F|get stats": [14, ""]}))
    lines = actions.statistics(store, store.device("gisebo"), live=True)
    assert lines[0].startswith("live: refused not-allowed") and "older firmware" in lines[0]
    assert any(l.startswith("Beacon: sampled") for l in lines)
    lines = actions.statistics(store, store.device("gisebo"), live=False)
    assert lines[0].startswith("Beacon: sampled")
