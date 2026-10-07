import os
import stat

import pytest

from mccontrol import actions, envelope, store as _store

NODE = "A" * 64


@pytest.fixture
def store(tmp_path):
    return _store.Store(tmp_path / "home")


def test_the_key_is_created_once_private_and_never_overwritten_by_accident(store):
    public = store.create_key()
    assert len(public) == 64
    assert stat.S_IMODE(store.key_path.stat().st_mode) == 0o600
    with pytest.raises(_store.StoreError, match="already exists"):
        store.create_key()
    assert store.public_key() == public
    assert store.create_key(overwrite=True) != public


def test_a_key_readable_by_others_is_refused(store):
    store.create_key()
    os.chmod(store.key_path, 0o644)
    with pytest.raises(_store.StoreError, match="chmod 600"):
        store.seed()


def test_devices_are_found_by_name_node_or_unique_prefix(store):
    store.add_device("LOUTEST", NODE, "the bench repeater")
    store.add_device("Lovsjo", "B" * 64)
    assert store.device("loutest")["node"] == NODE
    assert store.device("AAAA")["name"] == "LOUTEST"
    assert store.device("lo") is None                      # ambiguous
    assert store.device("lou")["name"] == "LOUTEST"
    with pytest.raises(_store.StoreError):
        store.add_device("again", NODE)
    with pytest.raises(_store.StoreError):
        store.add_device("short", "ABC")


def test_history_records_what_was_sent_and_what_came_back(store):
    store.add_device("LOUTEST", NODE)
    ok = envelope.parse_result("r1|1|0|region|> * F", NODE)
    store.record(NODE, "region", "meshcore/JKG/" + NODE + "/cmd", ok)
    store.record(NODE, "region save", "meshcore/JKG/" + NODE + "/cmd", None)
    lines = store.history(NODE)
    assert [l["command"] for l in lines] == ["region", "region save"]
    assert lines[0]["reply"] == "> * F" and lines[1]["code"] is None
    assert store.device("LOUTEST")["last_result"] is None   # the last send got no answer


def test_cautions_name_what_a_command_takes_away():
    assert "no scope" in actions.caution("region denyf *")
    assert "that scope" in actions.caution("region denyf se-jkp")
    assert actions.caution("region remove se06")
    assert actions.caution("region put se-jkp *") is None
    assert actions.caution("get autoreply") is None


def test_a_dry_run_signs_and_sends_nothing(store, monkeypatch):
    store.create_key()
    store.save_config(region="JKG")
    store.add_device("LOUTEST", NODE)
    monkeypatch.setattr(actions.transport, "send", lambda *a, **k: pytest.fail("sent"))
    topic, payload, answers, lines = actions.send(store, "region", device=store.device("LOUTEST"),
                                                  dry_run=True)
    assert topic == "meshcore/JKG/" + NODE + "/cmd"
    assert envelope.verify(store.public_key(), topic, payload)
    assert answers == {} and lines[-1] == "not sent (dry run)"


def test_a_broadcast_of_a_transmit_command_is_refused_before_signing(store):
    store.create_key()
    store.save_config(region="JKG")
    with pytest.raises(envelope.EnvelopeError, match="every node"):
        actions.send(store, "trigger test", broadcast=True, dry_run=True)


def test_sending_needs_a_region(store):
    store.create_key()
    store.add_device("LOUTEST", NODE)
    with pytest.raises(envelope.EnvelopeError, match="no region"):
        actions.send(store, "region", device=store.device("LOUTEST"), dry_run=True)


def test_send_records_every_device_asked_even_the_silent_ones(store, monkeypatch):
    store.create_key()
    store.save_config(region="JKG")
    store.add_device("LOUTEST", NODE)
    store.add_device("UGO", "B" * 64)
    answer = envelope.parse_result("r1|1|0|region put se-jkp *|OK", NODE)
    monkeypatch.setattr(actions.transport, "send",
                        lambda cfg, seed, topic, cmd, nodes_to_hear=(), **k: ("payload", {NODE: answer}))
    _t, _p, answers, lines = actions.send(store, "region put se-jkp *", broadcast=True)
    assert lines == ["LOUTEST            OK", "UGO                no answer"]
    assert store.history("B" * 64)[0]["code"] is None
    assert store.history(None)[0]["note"] == "1 of 2 answered"


class _FakeClient:
    """Enough of paho for discover(): connect fires on_connect, subscribe delivers retained statuses."""

    retained = []

    def __init__(self, _config):
        self.on_connect = self.on_message = None

    def username_pw_set(self, *_a):
        pass

    def connect(self, *_a, **_k):
        pass

    def loop_start(self):
        class _RC:
            is_failure = False
        self.on_connect(self, None, None, _RC())

    def subscribe(self, topic, qos=0):
        import types
        for node, payload in self.retained:
            self.on_message(self, None, types.SimpleNamespace(
                topic=f"meshcore/JKG/{node}/status", payload=payload, retain=True))

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def test_discover_lists_status_publishers_and_marks_the_ones_you_have(store, monkeypatch):
    from mccontrol import transport
    import json
    store.save_config(region="JKG")
    store.add_device("LOUTEST", NODE)
    _FakeClient.retained = [
        (NODE, json.dumps({"origin": "SE-JKG-LOUTEST", "model": "Heltec V3", "firmware_version": "v1",
                           "repeat": "on", "timestamp": "2026-10-07T18:00:00+00:00"}).encode()),
        ("B" * 64, json.dumps({"origin": "SE-JKG-UGORptr", "timestamp": "2026-09-01T00:00:00"}).encode()),
        ("short", b"{}"), (NODE[:63] + "Z", b"not json"),
    ]
    monkeypatch.setattr(transport, "_client", _FakeClient)
    monkeypatch.setattr(transport.time, "sleep", lambda _s: None)
    found = actions.discover(store)
    assert [e["name"] for e in found] == ["SE-JKG-LOUTEST", "SE-JKG-UGORptr"]
    assert found[0]["known_as"] == "LOUTEST" and found[1]["known_as"] is None
    assert found[0]["at"] == "2026-10-07 18:00" and found[0]["retained"]
    entry = actions.adopt(store, found[1])
    assert entry["name"] == "SE-JKG-UGORptr" and entry["node"] == "B" * 64
    assert actions.adopt(store, dict(found[1], node="C" * 64))["name"] == "SE-JKG-UGORptr-2"
