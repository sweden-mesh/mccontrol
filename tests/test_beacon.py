"""The Beacon client against shapes captured from beacon.meshat.se 2.0.3 on 2026-10-07
(`tests/data/beacon-fake.json`): no network."""
import json
import os
import pathlib

import pytest

from mccontrol import beacon, store as _store

FAKE = str(pathlib.Path(__file__).parent / "data" / "beacon-fake.json")
OID = "867a1b43-3f2e-48c4-9ef5-3008a905cfdc"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", FAKE)
    s = _store.Store(tmp_path / "home")
    s.add_device("Gisebo", "4AC61A7F", "JKG")
    s.add_device("Torpa", "13B58595", "JKG")
    s.add_device("Braneryd", "FD700369", "JKG")      # known to Beacon, never published a status
    s.add_device("Nobody", "00000000", "JKG")        # not known to Beacon at all
    return s


def test_one_nodes_read_resolves_and_caches_every_observer_id_in_the_region(store):
    assert beacon.observer_for(store, store.device("gisebo")) == OID
    cached = {d["node"]: d.get("observer_id") for d in store.devices()}
    assert cached["4AC61A7F"] == OID and cached["13B58595"] == "38dd5381-949c-4c3e-89dd-33dc549729ad"
    assert cached["FD700369"] is None and cached["00000000"] is None
    with pytest.raises(beacon.BeaconError, match="never published"):
        beacon.observer_for(store, store.device("braneryd"))
    with pytest.raises(beacon.BeaconError, match="does not know"):
        beacon.observer_for(store, store.device("nobody"))


def test_the_cached_id_is_used_without_a_read(store, monkeypatch):
    beacon.observer_for(store, store.device("gisebo"))
    monkeypatch.setenv("MCCONTROL_FAKE_BEACON", "/nonexistent")
    assert beacon.observer_for(store, store.device("gisebo")) == OID


def test_stats_carry_the_sample_and_its_age_never_a_bare_number(store):
    sample = beacon.stats(store, store.device("gisebo"), now=1791406238.583 + 125)
    assert sample["stats"]["battery_mv"] == 4261 and sample["stats"]["noise_floor"] == -114
    assert sample["age_secs"] == 125 and sample["model"] == "Heltec V3"
    lines = beacon.stat_lines(sample)
    assert lines[0].startswith("sampled 2 min ago  Heltec V3")
    assert "battery            4261 mV" in lines
    assert any(l.startswith("uptime             17 d 7 h") for l in lines)


def test_telemetry_is_bucketed_and_the_bounds_are_checked(store):
    points = beacon.telemetry(store, store.device("gisebo"), "24h", "6h")
    assert len(points) == 5 and points[0]["batteryMv"] == 3982
    lines = beacon.telemetry_lines(points)
    assert lines[0].startswith("time") and len(lines) == 6 and "3982" in lines[1]
    with pytest.raises(beacon.BeaconError, match="range"):
        beacon.telemetry(store, store.device("gisebo"), "1h", "6h")


def test_an_unreachable_beacon_is_an_error_with_the_url_in_it(store, monkeypatch):
    monkeypatch.delenv("MCCONTROL_FAKE_BEACON")
    store.save_config(beacon_url="http://127.0.0.1:9/api/v1")
    monkeypatch.setattr(beacon, "TIMEOUT_SECS", 1)
    with pytest.raises(beacon.BeaconError, match="unreachable at http://127.0.0.1:9/api/v1"):
        beacon.observer_for(store, store.device("torpa"), refresh=True)


def test_age_text():
    assert beacon.age_text(30) == "30 s ago" and beacon.age_text(600) == "10 min ago"
    assert beacon.age_text(7200) == "2 h ago" and beacon.age_text(300000) == "3 d ago"
