"""The tool's login token is the firmware's JWT, byte for byte (src/helpers/JWTHelper.cpp)."""
import base64
import json

from mccontrol import envelope, token

SEED = "11" * 32
PUB = envelope.public_key_hex(SEED)


def test_the_shape_is_the_firmwares():
    t = token.make(SEED, "mqtt.louie.se", now=1700000000)
    header, payload, sig = t.split(".")
    pad = lambda s: s + "=" * (-len(s) % 4)
    assert base64.urlsafe_b64decode(pad(header)) == b'{"alg":"Ed25519","typ":"JWT"}'
    assert base64.urlsafe_b64decode(pad(payload)) == (
        b'{"publicKey":"' + PUB.encode() + b'","aud":"mqtt.louie.se","iat":1700000000,"exp":1700086400,"client":"mccontrol"}')
    assert "=" not in header + payload                          # base64url, no padding
    assert len(sig) == 128 and sig == sig.upper() and all(c in "0123456789ABCDEF" for c in sig)   # hex, not base64
    assert token.username(SEED) == "v1_" + PUB and PUB == PUB.upper()


def test_the_bytes_are_pinned_for_a_fixed_seed_and_time():
    t = token.make(SEED, "mqtt.louie.se", now=1700000000)
    assert t == token.make(SEED, "mqtt.louie.se", now=1700000000)          # Ed25519 is deterministic
    assert t.startswith("eyJhbGciOiJFZDI1NTE5IiwidHlwIjoiSldUIn0.")
    # the broker repository's verifier test signs this same token; the bytes must agree
    assert t.split(".")[2] == ("BA31AE79D5ACA969F15FAC795D3B60D334697A637858E23BD757535AFB5A1365"
                               "DEC43002A9EECCE59E7E57FDE751ED2EB3D4E3B0D03180C348574B7615594F01")


def test_a_broker_verifies_it_with_the_key_it_names_and_nothing_else():
    t = token.make(SEED, "mqtt.louie.se", now=1700000000)
    ok, body = token.verify(t, "mqtt.louie.se", now=1700000100)
    assert ok and body["publicKey"] == PUB and body["client"] == "mccontrol"
    assert token.verify(t, "other.broker", now=1700000100) == (False, "audience")
    assert token.verify(t, "mqtt.louie.se", now=1700086401) == (False, "expired")
    header, payload, sig = t.split(".")
    forged = f"{header}.{payload}.{sig[:-2]}{'00' if sig[-2:] != '00' else '11'}"
    assert token.verify(forged, "mqtt.louie.se", now=1700000100) == (False, "signature")
    # a payload naming another key cannot carry this signature
    other = base64.urlsafe_b64encode(json.dumps({"publicKey": "22" * 32, "aud": "mqtt.louie.se", "iat": 1, "exp": 9e9}, separators=(",", ":")).encode()).decode().rstrip("=")
    assert token.verify(f"{header}.{other}.{sig}", "mqtt.louie.se", now=1700000100) == (False, "signature")


def test_the_client_logs_in_with_the_token_when_no_username_is_configured():
    from mccontrol import transport
    user, pw = transport.login({"broker": "mqtt.louie.se", "username": "", "password": ""}, SEED)
    assert user == "v1_" + PUB and token.verify(pw, "mqtt.louie.se")[0]
    user, pw = transport.login({"broker": "mqtt.louie.se", "username": "mcoar", "password": "x"}, SEED)
    assert (user, pw) == ("mcoar", "x")                       # a region's own broker keeps its pair
    assert transport.login({"broker": "b", "username": ""}, None) == ("", "")
