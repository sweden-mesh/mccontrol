import pytest

from mccontrol import envelope

SEED = "11" * 32


def test_the_signed_bytes_are_the_topic_a_newline_and_the_body():
    assert envelope.signed_message("meshcore/JKG/all/cmd", "v1|1|2|get autoreply") == \
        b"meshcore/JKG/all/cmd\nv1|1|2|get autoreply"


def test_an_envelope_verifies_with_the_public_key_and_not_with_another():
    payload = envelope.build(SEED, "meshcore/JKG/all/cmd", 1700000000, "get autoreply", now=1700000000)
    assert envelope.verify(envelope.public_key_hex(SEED), "meshcore/JKG/all/cmd", payload)
    assert not envelope.verify(envelope.public_key_hex("22" * 32), "meshcore/JKG/all/cmd", payload)
    assert not envelope.verify(envelope.public_key_hex(SEED), "meshcore/JKG/other/cmd", payload)


def test_signing_is_deterministic_so_two_implementations_can_be_compared():
    # Ed25519 is deterministic: the same seed, topic, counter, expiry and command give the
    # same bytes. The Meshcore-Panopticon repository signs this exact message in its own
    # test; the two must agree or the copy has drifted.
    payload = envelope.build(SEED, "meshcore/JKG/all/cmd", 1700000000, "get autoreply",
                             ttl_secs=300, now=1700000000)
    assert payload.startswith("v1|1700000000|1700000300|get autoreply|")
    assert payload.rsplit("|", 1)[1] == "D804708B7BF2CC3F62766C2F484F0E514AC384B696E0080EFEB9934BDF7E87242D287DCA9A3F6A1E59D58B8D0F4E95E3C811A1A5CE77C9EECAA2FCCB9FB20F03"
    assert payload == envelope.build(SEED, "meshcore/JKG/all/cmd", 1700000000, "get autoreply",
                                     ttl_secs=300, now=1700000000)


def test_a_command_with_the_separator_is_refused_with_the_region_hint():
    with pytest.raises(envelope.EnvelopeError, match="region put"):
        envelope.build(SEED, "meshcore/JKG/all/cmd", 1, "region def a|* b|*")


def test_results_parse_and_silence_is_none():
    r = envelope.parse_result(b"r1|5|0|get autoreply|> on", "ABCD")
    assert r.ok and r.reply == "> on" and r.node == "ABCD" and r.counter == 5
    assert envelope.parse_result("r1|5|14|region put x *|").code_name == "not-allowed"
    assert envelope.parse_result("garbage") is None
    assert envelope.parse_result(None) is None


def test_only_triggers_transmit():
    assert envelope.is_transmit("trigger test")
    assert envelope.is_transmit("Trigger private AA")
    assert not envelope.is_transmit("region put se-jkp *")
    assert not envelope.is_transmit("triggerx")


def test_result_topics():
    assert envelope.command_topic("jkg") == "meshcore/JKG/all/cmd"
    assert envelope.command_topic("JKG", "AB12CD34") == "meshcore/JKG/AB12CD34/cmd"
    # a whole key names the node by the whole key from ar-11 (2026-10-08); it used to be cut to 8
    assert envelope.command_topic("JKG", "ab12cd34" + "ff" * 28) == "meshcore/JKG/AB12CD34" + "FF" * 28 + "/cmd"
    assert envelope.node_from_result_topic("meshcore/JKG/AB12CD34/res") == "AB12CD34"
    assert envelope.node_from_result_topic("meshcore/JKG/AB12CD34/cmd") is None
    with pytest.raises(envelope.NodeIdError):
        envelope.command_topic("JKG", "AB")


# ---- the tamper argument, pinned (help/assurance.txt) ---------------------------------

PUB = envelope.public_key_hex(SEED)
TOPIC = "meshcore/JKG/4AC61A7F/cmd"
T0 = 1700000000


def _envelope(command="set autoreply.delay 60", counter=T0, now=T0, ttl=300):
    return envelope.build(SEED, TOPIC, counter, command, ttl_secs=ttl, now=now)


def test_a_fresh_envelope_is_accepted():
    assert envelope.check(PUB, TOPIC, _envelope(), last_counter=T0 - 1, now=T0 + 10) == 0


def test_every_signed_field_is_covered_so_altering_one_byte_fails():
    payload = _envelope()
    part, sig = payload.rsplit("|", 1)
    v, counter, expiry, command = part.split("|")
    altered = [
        f"{v}|{int(counter) + 1}|{expiry}|{command}|{sig}",          # a later counter
        f"{v}|{counter}|{int(expiry) + 3000}|{command}|{sig}",       # a longer life
        f"{v}|{counter}|{expiry}|set autoreply.delay 61|{sig}",      # the command
        f"{v}|{counter}|{expiry}|{command}|{sig[:-1]}{'0' if sig[-1] != '0' else '1'}",  # the signature
    ]
    for p in altered:
        assert envelope.check(PUB, TOPIC, p, last_counter=0, now=T0 + 10) == 16, p


def test_the_topic_is_signed_so_a_command_cannot_move_between_nodes_or_become_a_broadcast():
    payload = _envelope()
    assert envelope.check(PUB, "meshcore/JKG/65C1D12C/cmd", payload, 0, T0 + 10) == 16
    assert envelope.check(PUB, "meshcore/JKG/all/cmd", payload, 0, T0 + 10) == 16
    assert envelope.check(PUB, "meshcore/GOT/4AC61A7F/cmd", payload, 0, T0 + 10) == 16


def test_a_replay_at_the_same_node_is_refused_by_the_counter_even_within_its_life():
    payload = _envelope(counter=T0)
    assert envelope.check(PUB, TOPIC, payload, last_counter=T0 - 1, now=T0 + 10) == 0
    # the node stored T0 before running it; the same bytes again, or any lower counter:
    assert envelope.check(PUB, TOPIC, payload, last_counter=T0, now=T0 + 10) == 9
    assert envelope.check(PUB, TOPIC, _envelope(counter=T0 - 5), last_counter=T0, now=T0 + 10) == 9


def test_a_delayed_replay_dies_with_the_expiry_and_a_long_life_cannot_be_asked_for():
    assert envelope.check(PUB, TOPIC, _envelope(ttl=300), 0, now=T0 + 301) == 10
    assert envelope.check(PUB, TOPIC, _envelope(ttl=3600), 0, now=T0 - 1) == 11
    with pytest.raises(envelope.EnvelopeError, match="ttl"):
        _envelope(ttl=3601)


def test_a_node_without_a_clock_refuses_rather_than_guesses():
    assert envelope.check(PUB, TOPIC, _envelope(), 0, now=0) == 12


def test_another_key_cannot_sign_for_this_node():
    other = envelope.build("22" * 32, TOPIC, T0, "get autoreply", now=T0)
    assert envelope.check(PUB, TOPIC, other, 0, T0 + 10) == 16


def test_a_result_is_not_signed_so_it_proves_nothing_on_its_own():
    # The gap the argument names: anyone on the broker can publish this line.
    forged = envelope.parse_result("r1|1700000000|0|set autoreply.delay 60|ok", "4AC61A7F")
    assert forged is not None and forged.ok


# ---- the two topic forms (ar-10: 8 hex; ar-11: the whole key) --------------------------

KEY = "4AC61A7FF4800A079A7311CA606A4C7037FE6943D40E53932230142829A4F786"


def test_a_topic_takes_the_whole_key_when_known_and_the_id_otherwise():
    assert envelope.command_topic("jkg", "4AC61A7F") == "meshcore/JKG/4AC61A7F/cmd"
    assert envelope.command_topic("jkg", "4AC61A7F", KEY) == f"meshcore/JKG/{KEY}/cmd"
    assert envelope.command_topic("jkg", KEY) == f"meshcore/JKG/{KEY}/cmd"          # a whole key as the node
    assert envelope.result_topic("jkg", "4ac61a7f", KEY.lower()) == f"meshcore/JKG/{KEY}/res"
    assert envelope.command_topic("jkg") == "meshcore/JKG/all/cmd"
    assert envelope.node_key("4AC61A7F") is None and envelope.node_key(KEY) == KEY
    assert envelope.node_id(KEY) == "4AC61A7F"


def test_a_result_topic_yields_the_leaf_as_published_and_nothing_else():
    assert envelope.node_from_result_topic("meshcore/JKG/4AC61A7F/res") == "4AC61A7F"
    assert envelope.node_from_result_topic(f"meshcore/JKG/{KEY.lower()}/res") == KEY
    assert envelope.node_from_result_topic("meshcore/JKG/4AC6/res") is None
    assert envelope.node_from_result_topic("meshcore/JKG/all/cmd") is None
    assert envelope.node_from_result_topic("meshcore/JKG/zz-not-hex/res") is None
