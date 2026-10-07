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
    assert envelope.command_topic("JKG", "AB") == "meshcore/JKG/AB/cmd"
    assert envelope.node_from_result_topic("meshcore/JKG/AB/res") == "AB"
    assert envelope.node_from_result_topic("meshcore/JKG/AB/cmd") is None
