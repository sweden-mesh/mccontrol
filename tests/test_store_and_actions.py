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
    assert lines == ["LOUTEST      OK", "UGO          no answer"]
    assert store.history("BBBBBBBB")[0]["code"] is None
    assert store.history(None)[0]["note"] == "1 of 2 answered"


class _FakeClient:
    """Enough of paho: connect fires on_connect; a publish to all/cmd draws the scripted answers."""

    answers = []          # (node, code, reply) published on .../{node}/res with the request's counter
    logins = []           # (username, password) each client was given

    def __init__(self, _config, seed_hex=None):
        self.on_connect = self.on_message = None

    def username_pw_set(self, user, password):
        _FakeClient.logins.append((user, password))

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
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
    found = actions.discover(store, "jkg")
    assert [(e["node"], e["region"], e["code"]) for e in found] == [("AAAAAAAA", "JKG", 0), ("BBBBBBBB", "JKG", 17)]
    assert found[0]["known_as"] == "LOUTEST" and found[1]["known_as"] is None
    assert found[0]["outcome"] == "> on" and found[1]["outcome"].startswith("refused: rate-limited")
    entry = actions.adopt(store, found[1])
    assert entry["name"] == "" and entry["region"] == "JKG"          # unnamed until you name it
    assert store.device("BBBBBBBB")["node"] == "BBBBBBBB"
    assert actions.adopt(store, dict(found[1], node="CCCCCCCC"), name="UGO")["name"] == "UGO"
    assert actions.adopt(store, dict(found[1], node="DDDDDDDD"), name="UGO")["name"] == "UGO-2"


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
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
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
    # beacon_url is a read-only HTTP service with no credentials, not a second broker; the
    # observer feed and its username and password never appear here.
    assert set(store.config()) == {"broker", "port", "username", "password", "ttl_secs", "wait_secs", "beacon_url", "columns"}


def test_discovery_asks_several_regions_in_one_run_and_star_means_every_region_you_have(store, monkeypatch):
    from mccontrol import transport
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    store.add_device("FAR", "FD700369", "GOT")
    asked = []

    class _Spy(_FakeClient):
        def publish(self, topic, payload, qos=0):
            asked.append(topic)
            _FakeClient.answers = [(NODE, 0, "> on")] if "/JKG/" in topic else [("FD700369", 0, "> off")]
            return super().publish(topic, payload, qos)

    monkeypatch.setattr(transport, "_client", _Spy)
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
    assert actions.regions_asked("jkg, got", store) == ["JKG", "GOT"]
    assert actions.regions_asked("*", store) == store.regions()
    assert actions.regions_asked("JKG JKG;got", store) == ["JKG", "GOT"]
    found = actions.discover(store, "JKG, GOT")
    assert [(e["node"], e["region"]) for e in found] == [(NODE, "JKG"), ("FD700369", "GOT")]
    # two broadcasts per region: the discovery read, then the names read
    assert asked == ["meshcore/JKG/all/cmd"] * 2 + ["meshcore/GOT/all/cmd"] * 2
    assert found[0]["known_as"] == "LOUTEST" and found[1]["known_as"] == "FAR"
    found = actions.discover(store, "*")
    assert {e["region"] for e in found} == set(store.regions())


def test_a_command_set_runs_in_order_and_stops_at_the_first_refusal(store, monkeypatch):
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    sent = []

    def fake_send(cfg, seed, topic, cmd, nodes_to_hear=(), **k):
        sent.append(cmd)
        code = 14 if cmd.startswith("region allowf") else 0
        return "payload", {NODE: envelope.parse_result(f"r1|1|{code}|{cmd}|> ok", NODE)}

    monkeypatch.setattr(actions.transport, "send", fake_send)
    assert store.sets()["add-scope"][0] == "region put {arg} *"
    with pytest.raises(envelope.EnvelopeError, match="arg"):
        actions.fill(store.sets()["add-scope"], "")
    steps = actions.fill(store.sets()["add-scope"], "se-jkp")
    lines, stopped = actions.run_set(store, steps, device=store.device("loutest"))
    assert sent == ["region put se-jkp *", "region allowf se-jkp"]       # save and read never sent
    assert stopped == 1 and "stopped at step 2" in lines[-1] and "2 step(s) not sent" in lines[-1]
    assert lines[0] == "-- 1/4: region put se-jkp *"
    sent.clear()
    lines, stopped = actions.run_set(store, ["region", "ver"], device=store.device("loutest"))
    assert stopped is None and sent == ["region", "ver"]
    # silence stops it too
    monkeypatch.setattr(actions.transport, "send", lambda *a, **k: ("payload", {}))
    _l, stopped = actions.run_set(store, ["region", "ver"], device=store.device("loutest"))
    assert stopped == 0


def test_sets_are_saved_removed_and_the_shipped_one_can_go(store):
    assert store.save_set("Rollout", ["region put {arg} *", "", "region save"]) == ["region put {arg} *", "region save"]
    assert store.sets()["rollout"] == ["region put {arg} *", "region save"]
    with pytest.raises(_store.StoreError):
        store.save_set("two words", ["x"])
    with pytest.raises(_store.StoreError):
        store.save_set("empty", ["", " "])
    store.remove_set("add-scope")
    assert "add-scope" not in store.sets() and "rollout" in store.sets()
    with pytest.raises(_store.StoreError):
        store.remove_set("nothing")


def test_refresh_names_names_the_unnamed_and_stores_every_version(store, monkeypatch):
    store.create_key()
    store.add_device("", NODE, "JKG")
    store.add_device("UGO", "BBBBBBBB", "JKG")

    def fake_send(cfg, seed, topic, cmd, nodes_to_hear=(), **k):
        node = topic.split("/")[2]
        if node == "BBBBBBBB" and cmd == "get name":
            return "p", {node: envelope.parse_result(f"r1|1|14|{cmd}|", node)}      # older firmware
        reply = "> SE-JKG-Gisebo-4AC6" if cmd == "get name" else "> esp32s3-v1.17.1-ar-10-observer-42f48ce9"
        return "p", {node: envelope.parse_result(f"r1|1|0|{cmd}|{reply}", node)}

    monkeypatch.setattr(actions.transport, "send", fake_send)
    named, versioned, lines = actions.refresh_names(store, store.devices())
    assert (named, versioned) == (1, 2)
    assert store.device(NODE)["name"] == "SE-JKG-Gisebo-4AC6"
    assert store.device("ugo")["name"] == "UGO"                        # a name you gave is kept
    assert store.device("ugo")["ver"].endswith("42f48ce9")
    assert any("get name: refused: not-allowed" in l for l in lines)


def test_a_result_of_any_kind_counts_as_heard_and_silence_does_not(store):
    store.add_device("LOUTEST", NODE, "JKG")
    store.record(NODE, "region", "t", None)
    assert store.device("loutest").get("last_heard") is None
    store.record(NODE, "region", "t", envelope.parse_result("r1|1|17||", NODE))      # a refusal
    assert store.device("loutest")["last_heard"] == store.device("loutest")["last_sent"]


def test_a_good_answer_to_a_read_is_kept_and_a_refusal_keeps_the_last_good_one(store, monkeypatch):
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    answers = {}
    monkeypatch.setattr(actions.transport, "send", lambda cfg, seed, topic, cmd, nodes_to_hear=(), **k: ("p", dict(answers)))
    answers[NODE] = envelope.parse_result("r1|1|0|ver|> v1.17.1-ar-10", NODE)
    actions.send(store, "ver", device=store.device("loutest"))
    answers[NODE] = envelope.parse_result("r1|1|0|set autoreply on|OK", NODE)
    actions.send(store, "set autoreply on", device=store.device("loutest"))       # a write: not kept
    assert [(c, r) for c, r, _at in store.reads("loutest")] == [("ver", "> v1.17.1-ar-10")]
    answers[NODE] = envelope.parse_result("r1|1|14|ver|", NODE)
    actions.send(store, "ver", device=store.device("loutest"))                    # refused: the old stays
    answers.clear()
    actions.send(store, "ver", device=store.device("loutest"))                    # silence: the old stays
    assert store.reads("loutest")[0][1] == "> v1.17.1-ar-10"
    answers[NODE] = envelope.parse_result("r1|1|0|get name|> Gisebo", NODE)
    actions.send(store, "get  name", device=store.device("loutest"))
    assert [c for c, _r, _at in store.reads("loutest")][0] == "get name"           # normalised, newest first


def test_a_device_keeps_its_whole_key_and_commands_go_to_the_topic_it_listens_on(store, monkeypatch):
    store.create_key()
    KEY = "4AC61A7FF4800A079A7311CA606A4C7037FE6943D40E53932230142829A4F786"
    store.add_device("New", KEY, "JKG")                       # a whole key given as the node
    store.add_device("Old", "BBBBBBBB", "JKG")                 # only the id: an ar-10 node
    assert store.device("new")["node"] == "4AC61A7F" and store.device("new")["key"] == KEY
    assert store.device("old")["key"] is None
    seen = {}

    def fake_send(cfg, seed, topic, cmd, nodes_to_hear=(), keys=None, **k):
        seen["topic"], seen["keys"] = topic, dict(keys or {})
        return "p", {}
    monkeypatch.setattr(actions.transport, "send", fake_send)
    actions.send(store, "ver", device=store.device("new"))
    assert seen["topic"] == f"meshcore/JKG/{KEY}/cmd"
    actions.send(store, "ver", device=store.device("old"))
    assert seen["topic"] == "meshcore/JKG/BBBBBBBB/cmd"
    actions.send(store, "ver", region="JKG")
    assert seen["topic"] == "meshcore/JKG/all/cmd" and seen["keys"] == {"4AC61A7F": KEY}


def test_discovery_learns_a_known_devices_key_from_its_whole_key_topic(store, monkeypatch):
    from mccontrol import transport
    store.create_key()
    store.add_device("Old", "4AC61A7F", "JKG")
    KEY = "4AC61A7FF4800A079A7311CA606A4C7037FE6943D40E53932230142829A4F786"

    class _Keyed(_FakeClient):
        def publish(self, topic, payload, qos=0):
            import types
            counter = payload.split("|")[1]
            self.on_message(self, None, types.SimpleNamespace(topic=f"meshcore/JKG/{KEY}/res",
                                                               payload=f"r1|{counter}|0|get autoreply|> on".encode()))
            return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)
    monkeypatch.setattr(transport, "_client", _Keyed)
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
    found = actions.discover(store, "JKG")
    assert found[0]["node"] == "4AC61A7F" and found[0]["key"] == KEY and found[0]["known_as"] == "Old"
    assert store.device("old")["key"] == KEY                  # learned, so the next command uses it


def test_a_send_to_the_default_broker_signs_in_with_the_tools_own_key(store, monkeypatch):
    from mccontrol import token, transport
    store.create_key()
    store.add_device("LOUTEST", NODE, "JKG")
    _FakeClient.logins.clear()
    _FakeClient.answers = []
    monkeypatch.setattr(transport, "_client", _FakeClient)
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
    actions.send(store, "ver", device=store.device("loutest"))
    user, password = _FakeClient.logins[-1]
    assert user == token.username(store.seed()) and token.verify(password, "mqtt.louie.se")[0]
    store.save_config(username="mcoar", password="pw")              # a region's own broker
    actions.send(store, "ver", device=store.device("loutest"))
    assert _FakeClient.logins[-1] == ("mcoar", "pw")


def test_registering_publishes_the_signed_key_once_and_remembers_the_brokers_ok(store, monkeypatch):
    from mccontrol import envelope, transport
    store.create_key()
    public = store.public_key()
    seen = {}

    class _Broker(_FakeClient):
        """A broker with the registration hook: verifies the line and acknowledges."""
        def publish(self, topic, payload, qos=0):
            import types
            seen["topic"], seen["payload"] = topic, payload
            verdict = "ok" if envelope.verify_registration(payload) else "refused bad signature"
            self.on_message(self, None, types.SimpleNamespace(topic=envelope.register_ack_topic(public), payload=verdict.encode()))
            return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)
    monkeypatch.setattr(transport, "_client", _Broker)
    ok, detail = actions.register(store)
    assert ok and detail == "ok" and seen["topic"] == "mccontrol/register"
    assert envelope.verify_registration(seen["payload"])[0] == public
    assert store.registration()["broker"] == "mqtt.louie.se"

    class _Silent(_FakeClient):
        def publish(self, topic, payload, qos=0):
            import types
            return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)
    ok, detail = transport.register(store.config(), store.seed(), wait_secs=0, client_factory=_Silent)
    assert not ok and "no acknowledgement" in detail
