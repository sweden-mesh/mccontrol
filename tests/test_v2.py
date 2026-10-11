"""The encrypted v2 envelope and its r3 result (firmware ar-13, planning TODO 17a).

Three layers: RFC 7748's own vectors for the ladder; a round trip against a model of the
node side written here from the contract alone; and the firmware's known-answer file,
`tests/data/v2-vectors-ar-13.txt`, written by its host test, which both sides must produce
and open byte for byte."""
import hashlib
import pathlib

import pytest
from Crypto.Cipher import ChaCha20_Poly1305
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

from mccontrol import envelope, firmware

DATA = pathlib.Path(__file__).parent / "data"
VECTORS = DATA / "v2-vectors-ar-13.txt"
OPERATOR = "11" * 32
NODE_SEED = "22" * 32
NODE_PUB = envelope.public_key_hex(NODE_SEED)
TOPIC = f"meshcore/JKG/{NODE_PUB}/cmd"
NOW = 1700000000


# -- RFC 7748 ------------------------------------------------------------------------------

@pytest.mark.parametrize("scalar, u, out", [
    ("a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4",
     "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c",
     "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"),
    ("4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d",
     "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493",
     "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"),
])
def test_the_ladder_meets_rfc_7748_section_5_2(scalar, u, out):
    assert envelope.x25519(bytes.fromhex(scalar), bytes.fromhex(u)).hex() == out


def test_the_ladder_meets_rfc_7748_section_6_1_both_ways():
    alice = bytes.fromhex("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
    bob = bytes.fromhex("5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb")
    nine = (9).to_bytes(32, "little")
    a_pub, b_pub = envelope.x25519(alice, nine), envelope.x25519(bob, nine)
    assert a_pub.hex() == "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
    assert b_pub.hex() == "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
    assert envelope.x25519(alice, b_pub) == envelope.x25519(bob, a_pub)
    assert envelope.x25519(alice, b_pub).hex() == \
        "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"


def test_the_agreement_is_the_same_from_either_end():
    assert envelope.shared_secret(OPERATOR, NODE_PUB) == \
        envelope.shared_secret(NODE_SEED, envelope.public_key_hex(OPERATOR))


# -- the node, modelled from 17a alone ----------------------------------------------------

class Node:
    """What an ar-13 node does with a v2 command, from the contract: its own expanded
    private key's first 32 bytes clamped as MeshCore does, times u of the operator's key;
    HKDF; open first and drop on a bad tag; strip the zeros; answer r3."""

    def __init__(self, seed_hex, operator_pub_hex, topic):
        e = bytearray(hashlib.sha512(bytes.fromhex(seed_hex)).digest()[:32])
        e[0] &= 248
        e[31] &= 63
        e[31] |= 64
        shared = envelope.x25519(bytes(e), envelope.montgomery_u(bytes.fromhex(operator_pub_hex)))
        self.key = HKDF(shared, 32, b"", SHA256, context=b"mccontrol-v2|" + topic.encode())
        self.topic = topic

    @staticmethod
    def _nonce(direction, counter):
        return bytes([direction]) + bytes(7) + counter.to_bytes(4, "big")

    def open(self, payload):
        """`(counter, expiry, command)`, or None for a line the node drops."""
        head, _, sig = payload.rpartition("|")
        fields = head.split("|")
        if len(fields) != 4 or fields[0] != "v2" or len(sig) != 128:
            return None
        counter, expiry, b64 = int(fields[1]), int(fields[2]), fields[3]
        sealed = __import__("base64").urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        cipher = ChaCha20_Poly1305.new(key=self.key, nonce=self._nonce(0, counter))
        cipher.update(f"v2|{counter}|{expiry}".encode())
        try:
            plain = cipher.decrypt_and_verify(sealed[:-16], sealed[-16:])
        except ValueError:
            return None
        if len(plain) not in (64, 160):
            return None
        return counter, expiry, plain.rstrip(b"\0").decode()

    def answer(self, counter, code, command, reply):
        plain = f"{code}|{command}|{reply}".encode()[:512]
        plain += bytes((256 if len(plain) <= 256 else 512) - len(plain))
        cipher = ChaCha20_Poly1305.new(key=self.key, nonce=self._nonce(1, counter))
        cipher.update(f"r3|{counter}".encode())
        body, tag = cipher.encrypt_and_digest(plain)
        return f"r3|{counter}|" + __import__("base64").urlsafe_b64encode(body + tag).decode().rstrip("=")


def _node():
    return Node(NODE_SEED, envelope.public_key_hex(OPERATOR), TOPIC)


def test_a_round_trip_through_the_node_model():
    node = _node()
    payload = envelope.build_v2(OPERATOR, TOPIC, 42, "get name", NODE_PUB, now=NOW)
    assert envelope.verify(envelope.public_key_hex(OPERATOR), TOPIC, payload)   # the broker's check
    assert "get name" not in payload
    assert node.open(payload) == (42, NOW + 300, "get name")
    result = envelope.parse_r3(node.answer(42, 0, "get name", "> Gisebo"), "A09AA5F4", OPERATOR, NODE_PUB, TOPIC)
    assert (result.counter, result.code, result.command, result.reply, result.verified) == \
        (42, 0, "get name", "> Gisebo", True)
    assert result.ok


def test_a_reply_with_a_separator_in_it_keeps_it():
    r = envelope.parse_r3(_node().answer(3, 0, "region", "a|b"), None, OPERATOR, NODE_PUB, TOPIC)
    assert (r.command, r.reply) == ("region", "a|b")


def test_a_command_for_another_node_or_topic_does_not_open():
    other_topic = TOPIC.replace("/JKG/", "/GOT/")
    payload = envelope.build_v2(OPERATOR, other_topic, 1, "get name", NODE_PUB, now=NOW)
    assert _node().open(payload) is None
    third = envelope.public_key_hex("33" * 32)
    assert _node().open(envelope.build_v2(OPERATOR, TOPIC, 1, "get name", third, now=NOW)) is None


def _flip(payload, field, at):
    parts = payload.split("|")
    b64 = parts[field]
    c = b64[at]
    parts[field] = b64[:at] + ("B" if c != "B" else "C") + b64[at + 1:]
    return "|".join(parts)


@pytest.mark.parametrize("where", ["ciphertext", "tag", "expiry", "counter"])
def test_a_tampered_command_is_dropped(where):
    payload = envelope.build_v2(OPERATOR, TOPIC, 9, "get name", NODE_PUB, now=NOW)
    parts = payload.split("|")
    if where == "ciphertext":
        bad = _flip(payload, 3, 0)
    elif where == "tag":
        bad = _flip(payload, 3, len(parts[3]) - 3)
    elif where == "expiry":
        bad = "|".join(parts[:2] + [str(int(parts[2]) + 1)] + parts[3:])
    else:
        bad = "|".join(parts[:1] + ["10"] + parts[2:])
    assert _node().open(payload) is not None
    assert _node().open(bad) is None


@pytest.mark.parametrize("where", ["ciphertext", "tag", "counter"])
def test_a_tampered_result_is_a_forgery(where):
    good = _node().answer(9, 0, "get name", "> Gisebo")
    head, counter, b64 = good.split("|")
    bad = {"ciphertext": _flip(good, 2, 0), "tag": _flip(good, 2, len(b64) - 3),
           "counter": f"r3|10|{b64}"}[where]
    r = envelope.parse_r3(bad, None, OPERATOR, NODE_PUB, TOPIC)
    assert r.verified is False and not r.ok
    assert envelope.parse_r3(good, None, OPERATOR, NODE_PUB, TOPIC).verified is True


def test_a_result_under_another_key_is_a_forgery():
    r3 = _node().answer(1, 0, "get name", "> x")
    assert envelope.parse_r3(r3, None, "33" * 32, NODE_PUB, TOPIC).verified is False


@pytest.mark.parametrize("line", ["r3|1", "r3|x|AAAA", "r2|1|0|a|b", "r3|1|A|B", ""])
def test_what_is_not_an_r3_is_not_one(line):
    assert envelope.parse_r3(line, None, OPERATOR, NODE_PUB, TOPIC) is None


def test_bad_base64_in_an_r3_is_a_forgery():
    assert envelope.parse_r3("r3|1|AA+A", None, OPERATOR, NODE_PUB, TOPIC).verified is False


# -- padding ----------------------------------------------------------------------------

@pytest.mark.parametrize("length, padded", [(1, 64), (64, 64), (65, 160), (160, 160)])
def test_a_command_is_padded_to_64_or_160(length, padded):
    command = "x" * length
    payload = envelope.build_v2(OPERATOR, TOPIC, 1, command, NODE_PUB, now=NOW)
    sealed = envelope._unb64(payload.split("|")[3])
    assert len(sealed) == padded + 16
    assert _node().open(payload)[2] == command


def test_a_command_over_160_is_refused():
    with pytest.raises(envelope.EnvelopeError):
        envelope.build_v2(OPERATOR, TOPIC, 1, "x" * 161, NODE_PUB, now=NOW)


def test_a_command_with_a_separator_is_refused_as_in_v1():
    with pytest.raises(envelope.EnvelopeError):
        envelope.build_v2(OPERATOR, TOPIC, 1, "region put a|b", NODE_PUB, now=NOW)


def test_v2_needs_the_whole_key():
    with pytest.raises(envelope.EnvelopeError):
        envelope.build_v2(OPERATOR, TOPIC, 1, "ver", "A09AA5F4", now=NOW)


@pytest.mark.parametrize("plain_len, padded", [(256, 256), (257, 512), (512, 512), (600, 512)])
def test_a_result_is_padded_to_256_or_512_and_cut_beyond(plain_len, padded):
    reply = "r" * (plain_len - len("0|ver|"))
    r3 = _node().answer(5, 0, "ver", reply)
    assert len(envelope._unb64(r3.split("|")[2])) == padded + 16
    r = envelope.parse_r3(r3, None, OPERATOR, NODE_PUB, TOPIC)
    assert r.verified and r.reply == reply[:512 - len("0|ver|")]


# -- the firmware's known answers --------------------------------------------------------

def _records():
    if not VECTORS.is_file():
        pytest.skip(f"{VECTORS.name} is written by the firmware's host test and is not here yet")
    out = []
    for line in VECTORS.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        kind, *fields = line.split("\t")
        out.append((kind, dict(f.split("=", 1) for f in fields)))
    return out


def test_the_firmwares_keys_shared_secret_and_key_are_ours():
    keys = [f for k, f in _records() if k == "keys"][0]
    assert keys["operator_pub"] == envelope.public_key_hex(keys["operator_seed"])
    assert keys["node_pub"] == envelope.public_key_hex(keys["node_seed"])
    assert keys["shared"] == envelope.shared_secret(keys["operator_seed"], keys["node_pub"]).hex().upper()
    assert keys["key"] == envelope.v2_key(keys["operator_seed"], keys["node_pub"], keys["topic"]).hex().upper()
    assert keys["topic"] == envelope.command_topic("JKG", keys["node_pub"][:8], keys["node_pub"])


def test_build_v2_produces_the_firmwares_command_payloads_byte_for_byte():
    records = _records()
    keys = [f for k, f in records if k == "keys"][0]
    commands = [f for k, f in records if k == "command"]
    assert len(commands) == 2
    for c in commands:
        counter, expiry = int(c["counter"]), int(c["expiry"])
        assert c["aad"] == f"v2|{counter}|{expiry}"
        assert c["nonce"] == envelope.v2_nonce(0, counter).hex().upper()
        ours = envelope.build_v2(keys["operator_seed"], keys["topic"], counter, c["command"],
                                 keys["node_pub"], ttl_secs=300, now=expiry - 300)
        assert ours == c["payload"]
        assert len(envelope._unb64(ours.split("|")[3])) == int(c["padded"]) + 16


def test_parse_r3_opens_the_firmwares_result_and_the_node_model_writes_it():
    records = _records()
    keys = [f for k, f in records if k == "keys"][0]
    results = [f for k, f in records if k == "result"]
    assert results
    node = Node(keys["node_seed"], keys["operator_pub"], keys["topic"])
    for r in results:
        counter = int(r["counter"])
        assert r["nonce"] == envelope.v2_nonce(1, counter).hex().upper() and r["aad"] == f"r3|{counter}"
        got = envelope.parse_r3(r["payload"], None, keys["operator_seed"], keys["node_pub"], keys["topic"])
        assert (got.code, got.command, got.reply, got.verified) == (int(r["code"]), r["command"], r["reply"], True)
        assert node.answer(counter, int(r["code"]), r["command"], r["reply"]) == r["payload"]
    for c in (f for k, f in records if k == "command"):
        assert node.open(c["payload"]) == (int(c["counter"]), int(c["expiry"]), c["command"])


# -- which nodes get v2 ------------------------------------------------------------------

def test_ar_12_admits_the_ping_and_ar_13_speaks_v2():
    assert not firmware.admits("esp32s3-v1.17.1-ar-11-observer-x", "mqtt.cmd ping")
    assert firmware.admits("esp32s3-v1.17.1-ar-12-observer-x", "mqtt.cmd ping")
    assert firmware.admits("esp32s3-v1.17.1-ar-13-observer-x", "mqtt.cmd ping")
    assert firmware.needs("mqtt.cmd ping") == "ar-12"
    assert firmware.needs("get name") == "ar-11" and firmware.needs("get autoreply") == "ar-10"
    assert firmware.entry_for("x-ar-13-y")[0] == "ar-13" and firmware.entry_for("x-ar-14-y")[0] == "lou-14"   # one counter
    assert [firmware.speaks_v2(v) for v in (None, "", "x-ar-11-y", "x-ar-12-y", "x-ar-13-y", "x-ar-20-y")] == \
        [False, False, False, False, True, True]


def test_the_firmwares_ar_13_verdicts_match_the_ar_13_row():
    fixture = DATA / "command-verdicts-ar-13.txt"
    if not fixture.is_file():
        pytest.skip("command-verdicts-ar-13.txt comes with the ar-13 firmware's host test")
    mismatches = []
    for line in (l.strip() for l in fixture.read_text().splitlines()):
        if not line or line.startswith("#"):
            continue
        verdict, command = line.split("|", 1)
        if firmware.admits("esp32s3-v1.17.1-ar-13-x", command) != (verdict == "allowed"):
            mismatches.append((command, verdict))
    assert mismatches == [], mismatches


# -- through actions and the transport: r3 is signed everywhere ---------------------------

class _Broker:
    """Enough of paho: a v2 to the node's topic is opened by the node model and answered r3
    (or by `forge` instead); a v1 is answered r1. Every publish is kept."""

    published = []
    forge = None

    def __init__(self, _config, seed_hex=None):
        self.on_connect = self.on_message = None
        self.operator_pub = envelope.public_key_hex(seed_hex)

    def username_pw_set(self, *_a):
        pass

    def connect(self, *_a, **_k):
        pass

    def loop_start(self):
        class _RC:
            is_failure = False
        self.on_connect(self, None, None, _RC())

    def subscribe(self, *_a, **_k):
        pass

    def publish(self, topic, payload, qos=0):
        import types
        _Broker.published.append((topic, payload))
        res = topic[:-3] + "res"
        counter = int(payload.split("|")[1])
        if payload.startswith("v2|"):
            node = Node(NODE_SEED, self.operator_pub, topic)
            opened = node.open(payload)
            line = self.forge or node.answer(counter, 0, opened[2], "> Gisebo")
        else:
            line = f"r1|{counter}|0|{payload.split('|')[3]}|> Gisebo"
        self.on_message(self, None, types.SimpleNamespace(topic=res, payload=line.encode()))
        return types.SimpleNamespace(wait_for_publish=lambda timeout=0: None)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    from mccontrol import store as _store, transport
    monkeypatch.delenv("MCCONTROL_FAKE_RESULTS", raising=False)
    monkeypatch.setattr(transport, "_client", _Broker)
    monkeypatch.setattr(transport, "_wait", lambda *a, **k: None)
    _Broker.published, _Broker.forge = [], None
    store = _store.Store(tmp_path / "home")
    store.create_key()
    store.add_device("Gisebo", NODE_PUB, "JKG")
    return store


@pytest.mark.parametrize("ver, tag", [("esp32s3-v1.17.1-ar-13-observer-x", "v2"),
                                      ("esp32s3-v1.17.1-ar-12-observer-x", "v1"), (None, "v1")])
def test_a_node_gets_v2_from_ar_13_and_v1_below(fleet, ver, tag):
    from mccontrol import actions
    fleet.update_device(NODE_PUB[:8], ver=ver)
    actions.send(fleet, "get name", device=fleet.device(NODE_PUB[:8]))
    assert _Broker.published[-1][1].startswith(tag + "|")
    _t, payload, _a, _l = actions.send(fleet, "get name", device=fleet.device(NODE_PUB[:8]), dry_run=True)
    assert payload.startswith(tag + "|")


def test_a_broadcast_stays_v1_whatever_the_versions(fleet):
    from mccontrol import actions
    fleet.update_device(NODE_PUB[:8], ver="x-ar-13-y")
    _t, payload, _a, _l = actions.send(fleet, "get name", region="JKG", dry_run=True)
    assert payload.startswith("v1|") and "/all/cmd" in _t


def test_an_ar_13_node_without_its_whole_key_gets_v1(tmp_path, monkeypatch, fleet):
    from mccontrol import actions
    fleet.add_device("Short", "4AC61A7F", "JKG")
    fleet.update_device("4AC61A7F", ver="x-ar-13-y")
    _t, payload, _a, _l = actions.send(fleet, "ver", device=fleet.device("4AC61A7F"), dry_run=True)
    assert payload.startswith("v1|")


def test_an_r3_is_signed_in_the_outcome_the_history_the_reads_and_the_facts(fleet):
    from mccontrol import actions, facts
    node = NODE_PUB[:8]
    fleet.update_device(node, ver="x-ar-13-y")
    _t, _p, answers, lines = actions.send(fleet, "get name", device=fleet.device(node))
    assert answers[node].verified is True and answers[node].reply == "> Gisebo"
    assert lines[0].endswith("> Gisebo [signed]")
    assert fleet.history(node)[-1]["verified"] is True and fleet.history(node)[-1]["code"] == 0
    d = fleet.device(node)
    assert d["reads"]["get name"]["verified"] is True
    reads = [(c, v["reply"], v["at"], v.get("verified")) for c, v in d["reads"].items()]
    found, _raw = facts.derive(reads, fleet.history(node))
    assert found["name"]["verified"] is True


def test_a_forged_r3_is_a_bad_signature_everywhere(fleet):
    from mccontrol import actions
    node = NODE_PUB[:8]
    fleet.update_device(node, ver="x-ar-13-y")
    counter_holder = {}
    real = _Broker.publish

    def forging(self, topic, payload, qos=0):
        counter = payload.split("|")[1]
        other = Node("33" * 32, self.operator_pub, topic)      # not the node's key
        _Broker.forge = other.answer(int(counter), 0, "get name", "> evil")
        return real(self, topic, payload, qos)
    _Broker.publish = forging
    try:
        _t, _p, answers, lines = actions.send(fleet, "get name", device=fleet.device(node))
    finally:
        _Broker.publish = real
    assert answers[node].verified is False and not answers[node].ok
    assert lines[0].split(None, 1)[1].startswith("BAD SIGNATURE")
    assert fleet.history(node)[-1]["verified"] is False
    assert "get name" not in (fleet.device(node).get("reads") or {})
