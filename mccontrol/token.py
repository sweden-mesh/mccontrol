"""The login token the Observer firmware presents to a broker, made here with the tool's own
key, so mccontrol signs in to the default command broker the way a node does: no username
or password to hand out, the key is the identity (operator decision 2026-10-08).

The firmware's form (`src/helpers/JWTHelper.cpp`), byte for byte:

    username   v1_<64 upper-case hex of the public key>
    password   <base64url(header)>.<base64url(payload)>.<128 upper-case hex Ed25519 signature>
    header     {"alg":"Ed25519","typ":"JWT"}                 (ArduinoJson: compact, this order)
    payload    {"publicKey":"<64 hex>","aud":"<broker host>","iat":<secs>,"exp":<secs>,
                "client":"mccontrol"}                        (owner and email left out)
    signature  Ed25519 over the ASCII "<b64 header>.<b64 payload>" by the key

Base64url has no padding. The third part is HEX, not base64url -- that is what the firmware
writes and what the collectors' verifiers read. A verifier recovers the public key from the
payload (or the username), checks the signature over the first two parts, and checks `aud`
and `exp` against itself and the clock."""

import base64
import json
import time

from Crypto.Signature import eddsa

from . import envelope

LIFETIME_SECS = 86400          # the firmware's default; renewed before expiry
CLIENT = "mccontrol"


def _b64url(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _compact(obj):
    return json.dumps(obj, separators=(",", ":"))


USERNAME_PREFIX = "v1_"


def username(seed_hex):
    return USERNAME_PREFIX + envelope.public_key_hex(seed_hex)


def make(seed_hex, audience, now=None, lifetime=LIFETIME_SECS):
    """The token for `audience` (the broker's host name), as the password string."""
    now = int(time.time() if now is None else now)
    header = _b64url(_compact({"alg": "Ed25519", "typ": "JWT"}).encode())
    payload = _b64url(_compact({"publicKey": envelope.public_key_hex(seed_hex), "aud": audience,
                                "iat": now, "exp": now + lifetime, "client": CLIENT}).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    signature = eddsa.new(envelope.key_from_seed(seed_hex), "rfc8032").sign(signing_input)
    return f"{header}.{payload}.{signature.hex().upper()}"


def verify(token, audience=None, now=None):
    """Check a token the way a broker would: `(ok, payload_or_reason)`. The public key is
    taken from the payload, so a forged token needs the private key of the key it names."""
    try:
        header, payload, sig_hex = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        head = json.loads(base64.urlsafe_b64decode(pad(header)))
        body = json.loads(base64.urlsafe_b64decode(pad(payload)))
        if head.get("alg") != "Ed25519":
            return False, "alg"
        key = eddsa.import_public_key(bytes.fromhex(body["publicKey"]))
        eddsa.new(key, "rfc8032").verify(f"{header}.{payload}".encode("ascii"), bytes.fromhex(sig_hex))
    except (ValueError, KeyError, TypeError):
        return False, "signature"
    now = int(time.time() if now is None else now)
    if audience is not None and body.get("aud") != audience:
        return False, "audience"
    if "exp" in body and body["exp"] < now:
        return False, "expired"
    return True, body
