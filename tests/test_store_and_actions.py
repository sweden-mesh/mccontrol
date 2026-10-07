import os
import stat

import pytest

from mccontrol import actions, envelope, store as _store

NODE = "AAAAAAAA"


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
    store.add_device("LOUTEST", NODE, "JKG", "the bench repeater")
    store.add_device("Lovsjo", "BBBBBBBB", "JKG")
    assert store.device("loutest")["node"] == NODE
    assert store.device("AAAA")["name"] == "LOUTEST"
    assert store.device("A" * 64)["name"] == "LOUTEST"
    assert store.device("lo") is None                      # ambiguous
    assert store.device("lou")["name"] == "LOUTEST"
    with pytest.raises(_store.StoreError):
        store.add_device("again", NODE, "JKG")
    with pytest.raises(_store.StoreError):
        store.add_device("short", "ABC", "JKG")
    assert store.add_device("whole", "E" * 64, "JKG")["node"] == "EEEEEEEE"
    with pytest.raises(_store.StoreError, match="region"):
        store.add_device("noregion", "DDDDDDDD", "")
    assert store.regions() == ["JKG"]


def test_history_records_what_was_sent_and_what_came_back(store):
    store.add_device("LOUTEST", NODE, "JKG")
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
    store.add_device("LOUTEST", NODE, "JKG")
    monkeypatch.setattr(actions.transport, "send", lambda *a, **k: pytest.fail("sent"))
    topic, payload, answers, lines = actions.send(store, "region", device=store.device("LOUTEST"),
                                                  dry_run=True)
    assert topic == "meshcore/JKG/" + NODE + "/cmd"
    assert envelope.verify(store.public_key(), topic, payload)
    assert answers == {} and lines[-1] == "not sent (dry run)"


def test_a_broadcast_of_a_transmit_command_is_refused_before_signing(store):
    store.create_key()
    with pytest.raises(envelope.EnvelopeError, match="every node"):
        actions.send(store, "trigger test", region="JKG", dry_run=True)


def test_a_broadcast_goes_to_one_region_and_hears_only_that_regions_devices(store, monkeypatch):
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    store.add_device("FAR", "BBBBBBBB", "GOT")
    heard = {}
    monkeypatch.setattr(actions.transport, "send",
                        lambda cfg, seed, topic, cmd, nodes_to_hear=(), **k: heard.update(
                            {"topic": topic, "nodes": list(nodes_to_hear), "region": cfg["region"]}) or ("p", {}))
    actions.send(store, "region put se-jkp *", region="jkg")
    assert heard == {"topic": "meshcore/JKG/all/cmd", "nodes": [NODE], "region": "JKG"}


def test_a_device_without_a_region_cannot_be_sent_to(store):
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    store.update_device(NODE, region="")
    with pytest.raises(envelope.EnvelopeError, match="no region"):
        actions.send(store, "region", device=store.device("LOUTEST"), dry_run=True)


def test_send_records_every_device_asked_even_the_silent_ones(store, monkeypatch):
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    store.add_device("UGO", "BBBBBBBB", "JKG")
    answer = envelope.parse_result("r1|1|0|region put se-jkp *|OK", NODE)
    monkeypatch.setattr(actions.transport, "send",
                        lambda cfg, seed, topic, cmd, nodes_to_hear=(), **k: ("payload", {NODE: answer}))
    _t, _p, answers, lines = actions.send(store, "region put se-jkp *", region="JKG")
    assert lines == ["LOUTEST            OK", "UGO                no answer"]
    assert store.history("BBBBBBBB")[0]["code"] is None
    assert store.history(None)[0]["note"] == "1 of 2 answered"


class _FakeClient:
    """Enough of paho: connect fires on_connect; a publish to all/cmd draws the scripted answers."""

    answers = []          # (node, code, reply) published on .../{node}/res with the request's counter

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
        pass

    def publish(self, topic, payload, qos=0):
        import types
        counter = payload.split("|")[1]
        region = topic.split("/")[1]
        for node, code, reply in self.answers:
            self.on_message(self, None, types.SimpleNamespace(
                topic=f"meshcore/{region}/{node}/res",
                payload=f"r1|{counter}|{code}|get autoreply|{reply}".encode()))
        return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def test_discover_finds_the_nodes_that_answer_your_signature_and_marks_the_known_ones(store, monkeypatch):
    from mccontrol import transport
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    _FakeClient.answers = [(NODE, 0, "> on"), ("BBBBBBBB", 17, ""), ("short", 0, "x")]
    monkeypatch.setattr(transport, "_client", _FakeClient)
    monkeypatch.setattr(transport.time, "sleep", lambda _s: None)
    found = actions.discover(store, "jkg")
    assert [(e["node"], e["region"], e["code"]) for e in found] == [("AAAAAAAA", "JKG", 0), ("BBBBBBBB", "JKG", 17)]
    assert found[0]["known_as"] == "LOUTEST" and found[1]["known_as"] is None
    assert found[0]["outcome"] == "> on" and found[1]["outcome"].startswith("refused: rate-limited")
    entry = actions.adopt(store, found[1])
    assert entry["name"] == "BBBBBBBB" and entry["region"] == "JKG"
    assert actions.adopt(store, dict(found[1], node="CCCCCCCC"), name="UGO")["name"] == "UGO"


def test_discovery_broadcasts_one_signed_read_on_the_control_broker(store, monkeypatch):
    from mccontrol import transport, envelope
    store.create_key()
    published = {}

    class _Spy(_FakeClient):
        def publish(self, topic, payload, qos=0):
            published["topic"], published["payload"] = topic, payload
            return super().publish(topic, payload, qos)

    _FakeClient.answers = []
    monkeypatch.setattr(transport, "_client", _Spy)
    monkeypatch.setattr(transport.time, "sleep", lambda _s: None)
    assert actions.discover(store, "JKG") == []
    assert published["topic"] == "meshcore/JKG/all/cmd"
    assert envelope.verify(store.public_key(), published["topic"], published["payload"])
    assert "|get autoreply|" in published["payload"]


def test_the_connection_test_reports_each_step_and_stops_at_the_first_failure(monkeypatch):
    from mccontrol import transport

    class _Echo(_FakeClient):
        def subscribe(self, topic, qos=0):
            self._topic = topic
        def publish(self, topic, payload, qos=0):
            import types
            self.on_message(self, None, types.SimpleNamespace(topic=topic, payload=payload.encode()))
            return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)

    steps = transport.test_connection({"broker": "b", "port": 1, "username": "u", "password": "p"},
                                      client_factory=_Echo, timeout=0.2)
    assert [(s, ok) for s, ok, _d in steps] == [("connect and sign in", True),
                                                ("subscribe to a result topic", True),
                                                ("publish", True),
                                                ("receive what we published", True)]
    assert "TEST/mccontrol-test/res" in steps[1][2]

    class _Refuse(_FakeClient):
        def loop_start(self):
            class _RC:
                is_failure = True
                def __str__(self):
                    return "Not authorized"
            self.on_connect(self, None, None, _RC())

    steps = transport.test_connection({"broker": "b", "port": 1, "username": "u", "password": "p"},
                                      client_factory=_Refuse, timeout=0.2)
    assert steps == [("connect and sign in", False, "b refused the connection: Not authorized")]


def test_the_configuration_holds_only_the_control_broker(store):
    assert set(store.config()) == {"broker", "port", "username", "password", "ttl_secs", "wait_secs"}
