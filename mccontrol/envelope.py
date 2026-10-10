"""The signed envelope the repeater firmware verifies.

Must match `src/helpers/MqttControlLogic.h` in the Observer auto-reply firmware byte for byte:

    topic    meshcore/{IATA}/{NODE}/cmd        one node, NODE = first 8 hex of its key
             meshcore/{IATA}/all/cmd           every node in the region
    payload  v1|{counter}|{expiry}|{command}|{128 hex Ed25519 signature}
    signed   "{topic}\\n" + "v1|{counter}|{expiry}|{command}"
    result   r1|{counter}|{code}|{command}|{reply}   on meshcore/{IATA}/{NODE}/res

The topic is inside the signature, so a command captured from one node cannot be replayed at
another, nor a per-node command replayed as a broadcast. The counter only has to be greater
than the last one the node accepted; unix seconds do that without any bookkeeping.

This file is a copy of `panopticon/control.py` in the Meshcore-Panopticon repository, kept in
step by a test in each that signs a fixed message and compares the bytes.
"""

import base64
import hashlib
import secrets
import time

from Crypto.Cipher import ChaCha20_Poly1305
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

VERSION_TAG = "v1"
FIELD_SEP = "|"
TOPIC_PREFIX = "meshcore/"
SIG_HEX_LEN = 128
MAX_PAYLOAD = 512
MAX_TOPIC = 160
MAX_COMMAND = 160
MAX_FUTURE_SECS = 3600
DEFAULT_TTL_SECS = 300

RESULT_VERSION = "r1"
RESULT_FIELDS = 5

# Must match enum MqttCtrlResult in the firmware. It appends and never renumbers.
RESULT_CODES = {
    0: "ok",
    1: "empty", 2: "too-long", 3: "no-signature", 4: "bad-sig-len", 5: "bad-sig-hex",
    6: "bad-version", 7: "bad-fields", 8: "bad-counter",
    9: "replay", 10: "expired", 11: "expiry-too-far", 12: "no-clock",
    13: "command-chars", 14: "not-allowed", 15: "no-owner-key", 16: "sig-invalid",
    17: "rate-limited", 18: "broadcast-transmit",
}

# What a result code means to a person, so a refusal needs no manual.
RESULT_HELP = {
    0: "The node ran the command.",
    9: "The node has already seen a higher counter. Wait a second and send again.",
    10: "The envelope expired before the node read it. Check the node's clock and yours.",
    12: "The node has no clock yet, so it cannot judge expiry. It will after its first advert.",
    14: "The command is not on the node's allowlist. Only what its firmware admits travels this way.",
    15: "The node has no command key provisioned. Set mqtt.cmd.pubkey over serial first.",
    16: "The signature did not verify. The node holds a different public key than this one.",
    17: "The node's rate limit refused it. Wait a few minutes.",
    18: "A command that transmits may not be sent to every node at once.",
}


class EnvelopeError(ValueError):
    """A command could not be built or signed. Never carries the private key."""


def code_name(code):
    return RESULT_CODES.get(code, f"unknown-{code}")


def generate_seed():
    """A fresh 32-byte private seed, hex encoded."""
    return secrets.token_hex(32)


def key_from_seed(seed_hex):
    try:
        seed = bytes.fromhex(seed_hex.strip())
    except ValueError:
        raise EnvelopeError("the key must be hexadecimal") from None
    if len(seed) != 32:
        raise EnvelopeError(f"the key must be 32 bytes, got {len(seed)}")
    return ECC.construct(curve="Ed25519", seed=seed)


def public_key_hex(seed_hex):
    """The 64 hex characters to give the node with `set mqtt.cmd.pubkey`."""
    return key_from_seed(seed_hex).public_key().export_key(format="raw").hex().upper()


# A node is NAMED by the first eight hex characters of its public key everywhere a person
# sees it. On the control topics the firmware uses either form: builds up to ar-10 name the
# node by those eight characters (`toHex(node_hex, self_id.pub_key, 4)`); from ar-11
# (2026-10-08) by the whole 64-character key, as the observer's status topics do, so a
# broker's ACL can match the token login against the topic. The fleet runs both for months,
# so a topic takes whichever leaf the device carries: its stored `key` when known, its id
# otherwise.
NODE_HEX_LEN = 8
KEY_HEX_LEN = 64


class NodeIdError(ValueError):
    pass


def _hex(value):
    text = (value or "").strip().upper()
    if any(c not in "0123456789ABCDEF" for c in text):
        raise NodeIdError("a node is named by the hex of its public key")
    return text


def node_id(value):
    """Eight upper-case hex characters, from a prefix or a whole public key."""
    text = _hex(value)
    if len(text) < NODE_HEX_LEN:
        raise NodeIdError("a node is named by the first 8 hex characters of its public key")
    return text[:NODE_HEX_LEN]


def node_key(value):
    """The whole key, 64 upper-case hex characters, or None when only a prefix was given."""
    text = _hex(value)
    return text if len(text) == KEY_HEX_LEN else None


def topic_leaf(node, key=None):
    """What names the node on its topics: the whole key when known (ar-11), else the id."""
    full = node_key(key) if key else node_key(node)
    return full or node_id(node)


def command_topic(iata, node=None, key=None):
    leaf = topic_leaf(node, key) if node else "all"
    return f"{TOPIC_PREFIX}{iata.upper()}/{leaf}/cmd"


def result_topic(iata, node, key=None):
    return f"{TOPIC_PREFIX}{iata.upper()}/{topic_leaf(node, key)}/res"


def is_transmit(command):
    """Does this command put a packet on the air? The firmware refuses those as a broadcast."""
    head = command.strip().lower()
    return head == "trigger" or head.startswith("trigger ") or head.startswith("trigger.")


def signed_part(counter, expiry, command):
    if FIELD_SEP in command:
        raise EnvelopeError(f"a command may not contain {FIELD_SEP!r}; "
                            f"for a region tree use 'region put' one name at a time")
    if not command or len(command) > MAX_COMMAND:
        raise EnvelopeError(f"a command is 1..{MAX_COMMAND} characters")
    if not command.isprintable() or not command.isascii():
        raise EnvelopeError("a command is printable ASCII")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise EnvelopeError("the counter must fit in 32 bits")
    return f"{VERSION_TAG}{FIELD_SEP}{counter}{FIELD_SEP}{expiry}{FIELD_SEP}{command}"


def signed_message(topic, part):
    if not topic or len(topic) > MAX_TOPIC:
        raise EnvelopeError(f"a topic is 1..{MAX_TOPIC} characters")
    return f"{topic}\n{part}".encode("utf-8")


def build(seed_hex, topic, counter, command, ttl_secs=DEFAULT_TTL_SECS, now=None):
    """One signed envelope, as the payload string to publish."""
    now = int(time.time() if now is None else now)
    if not 0 < ttl_secs <= MAX_FUTURE_SECS:
        raise EnvelopeError(f"ttl is 1..{MAX_FUTURE_SECS} seconds")
    part = signed_part(counter, now + ttl_secs, command)
    signature = eddsa.new(key_from_seed(seed_hex), "rfc8032").sign(signed_message(topic, part))
    payload = f"{part}{FIELD_SEP}{signature.hex().upper()}"
    if len(payload) > MAX_PAYLOAD:
        raise EnvelopeError(f"the envelope is {len(payload)} bytes, over {MAX_PAYLOAD}")
    return payload


def verify(public_hex, topic, payload):
    """Check an envelope the way a node would."""
    split = payload.rfind(FIELD_SEP)
    if split < 0:
        raise EnvelopeError("no signature")
    part, sig_hex = payload[:split], payload[split + 1:]
    if len(sig_hex) != SIG_HEX_LEN:
        raise EnvelopeError("the signature is the wrong length")
    key = eddsa.import_public_key(bytes.fromhex(public_hex))
    try:
        eddsa.new(key, "rfc8032").verify(signed_message(topic, part), bytes.fromhex(sig_hex))
    except ValueError:
        return False
    return True


def check(public_hex, topic, payload, last_counter, now):
    """Judge an envelope in the order a node does -- signature, counter, expiry -- and return
    the result code the node would publish (0 for accepted). `mqttCtrlVerify` in the firmware
    is the authority; this is the same order, kept here so the tamper argument in
    `help/assurance.txt` is pinned by tests rather than asserted."""
    split = payload.rfind(FIELD_SEP)
    if split < 0:
        return 3
    part = payload[:split]
    if len(payload) - split - 1 != SIG_HEX_LEN:
        return 4
    try:
        if not verify(public_hex, topic, payload):
            return 16
    except ValueError:
        return 5
    fields = part.split(FIELD_SEP)
    if len(fields) != 4 or fields[0] != VERSION_TAG:
        return 7
    try:
        counter, expiry = int(fields[1]), int(fields[2])
    except ValueError:
        return 8
    if counter <= last_counter:
        return 9
    if now == 0:
        return 12
    if expiry < now:
        return 10
    if expiry - now > MAX_FUTURE_SECS:
        return 11
    return 0


# Registering the signing key with the broker (operator ruling 2026-10-08): the owner's
# public key is published once to the broker's registration topic, as a line signed by that
# very key, so the broker knows the publisher holds it. The broker's hook then verifies every
# command against the registered keys before it fans out, and counts a bad one toward the
# 5-a-day / 48-hour lockout. A replay of the line registers nothing new (same key).
#
#     topic    mccontrol/register                        the tool publishes here
#     payload  reg1|<64 hex public key>|<unix secs>|<128 hex Ed25519 sig>
#     signed   "mccontrol/register\n" + "reg1|<key>|<secs>"
#     answer   mccontrol/register/<64 hex key>/ack       ok | refused <reason>
REGISTER_TOPIC = "mccontrol/register"
REGISTER_VERSION = "reg1"


def register_payload(seed_hex, now=None):
    now = int(time.time() if now is None else now)
    public = public_key_hex(seed_hex)
    part = f"{REGISTER_VERSION}{FIELD_SEP}{public}{FIELD_SEP}{now}"
    signature = eddsa.new(key_from_seed(seed_hex), "rfc8032").sign(signed_message(REGISTER_TOPIC, part))
    return f"{part}{FIELD_SEP}{signature.hex().upper()}"


def register_ack_topic(public_hex):
    return f"{REGISTER_TOPIC}/{public_hex.upper()}/ack"


def verify_registration(payload):
    """What the broker's hook does with a registration line: `(public_hex, secs)` when the
    signature is by the key the line names, else None."""
    try:
        version, public, secs, sig_hex = payload.split(FIELD_SEP)
        if version != REGISTER_VERSION or len(public) != 64 or len(sig_hex) != SIG_HEX_LEN:
            return None
        part = f"{version}{FIELD_SEP}{public}{FIELD_SEP}{secs}"
        key = eddsa.import_public_key(bytes.fromhex(public))
        eddsa.new(key, "rfc8032").verify(signed_message(REGISTER_TOPIC, part), bytes.fromhex(sig_hex))
        return public.upper(), int(secs)
    except (ValueError, TypeError):
        return None


SIGNED_RESULT_VERSION = "r2"


class Result:
    """One line a node published to its /res topic. Silence is its absence, never an instance.

    `verified` is True only for an `r2` whose signature checked against the node's key;
    None for an `r1` (older firmware: nothing to check); False for an `r2` that did not
    verify -- a forged or altered line, which is a finding, not a result."""

    __slots__ = ("node", "counter", "code", "command", "reply", "verified")

    def __init__(self, node, counter, code, command, reply, verified=None):
        self.node, self.counter, self.code, self.command, self.reply = node, counter, code, command, reply
        self.verified = verified

    @property
    def ok(self):
        """Ran, and -- when signed -- said so in the node's own voice. A forged ok is not ok."""
        return self.code == 0 and self.verified is not False

    @property
    def code_name(self):
        return code_name(self.code)

    @property
    def help(self):
        return RESULT_HELP.get(self.code, "")


def parse_result(payload, node=None, topic=None, key_hex=None):
    """One /res line as a Result, or None for anything that is not one.

    An `r2` (ar-11 and later) carries the node's Ed25519 signature over `"<topic>\n"` and
    the line before the last separator; with `topic` and the node's 64-hex `key_hex` it is
    checked and `verified` set. Without a key (an ar-10 device whose whole key is not known)
    an `r2` is reported as unverified rather than refused."""
    if payload is None:
        return None
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    verified = None
    line = payload
    if payload.startswith(SIGNED_RESULT_VERSION + FIELD_SEP):
        split = payload.rfind(FIELD_SEP)
        if split < 0 or len(payload) - split - 1 != SIG_HEX_LEN:
            return None
        line, sig_hex = payload[:split], payload[split + 1:]
        if topic and key_hex:
            try:
                key = eddsa.import_public_key(bytes.fromhex(key_hex))
                eddsa.new(key, "rfc8032").verify(signed_message(topic, line), bytes.fromhex(sig_hex))
                verified = True
            except ValueError:
                verified = False
    parts = line.split(FIELD_SEP, RESULT_FIELDS - 1)
    if len(parts) != RESULT_FIELDS or parts[0] not in (RESULT_VERSION, SIGNED_RESULT_VERSION):
        return None
    try:
        counter, code = int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if counter < 0 or code < 0:
        return None
    return Result(node, counter, code, parts[3], parts[4], verified)


def node_from_result_topic(topic):
    """The leaf of a result topic as published: 8 or 64 hex, or None for anything else."""
    parts = (topic or "").split("/")
    if len(parts) < 3 or parts[-1] != "res":
        return None
    leaf = parts[-2].upper()
    if len(leaf) not in (NODE_HEX_LEN, KEY_HEX_LEN) or any(c not in "0123456789ABCDEF" for c in leaf):
        return None
    return leaf


# -- v2: the command plane end to end encrypted (ar-13, firmware TODO 17a) -------------------
#
#     shared   MeshCore's ed25519_key_exchange: SHA-512(own seed)[0:32], clamped, times the
#              Montgomery u of the node's Ed25519 public key; raw u, 32 bytes
#     key      HKDF-SHA256(shared, salt empty, info "mccontrol-v2|" + command topic, 32)
#     nonce    dir(1) | 00 x 7 | counter (4, big-endian); dir 00 command, 01 result
#     command  v2|<counter>|<expiry>|<b64url(ChaCha20-Poly1305(command padded to 64 or 160),
#              AAD "v2|<counter>|<expiry>")>|<128 hex Ed25519 over "<topic>\n" + all before>
#     result   r3|<counter>|<b64url(seal("<code>|<command>|<reply>" padded to 256 or 512),
#              AAD "r3|<counter>")>   on the node's /res
#
# The outer signature is for the broker, which drops junk before fan-out without reading the
# command; the node checks the tag. Broadcasts stay v1.

V2_TAG = "v2"
R3_TAG = "r3"
V2_INFO = b"mccontrol-v2|"
V2_COMMAND_PADS = (64, 160)
TAG_LEN = 16
DIR_COMMAND, DIR_RESULT = 0x00, 0x01

_P = 2 ** 255 - 19
_A24 = 121665


def x25519(scalar, u):
    """RFC 7748 X25519 on raw bytes: the scalar clamped, u's top bit masked, the ladder."""
    k = bytearray(scalar)
    k[0] &= 248
    k[31] &= 127
    k[31] |= 64
    k = int.from_bytes(k, "little")
    x1 = int.from_bytes(u, "little") & ((1 << 255) - 1)
    x2, z2, x3, z3, swap = 1, 0, x1, 1, 0
    for t in range(254, -1, -1):
        bit = (k >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % _P, (x2 - z2) % _P
        aa, bb = a * a % _P, b * b % _P
        e = (aa - bb) % _P
        c, d = (x3 + z3) % _P, (x3 - z3) % _P
        da, cb = d * a % _P, c * b % _P
        x3, z3 = (da + cb) ** 2 % _P, x1 * (da - cb) ** 2 % _P
        x2, z2 = aa * bb % _P, e * (aa + _A24 * e) % _P
    if swap:
        x2, z2 = x3, z3
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, "little")


def montgomery_u(ed_public):
    """The Montgomery u of an Ed25519 public key, u = (1+y)/(1-y) mod p, as MeshCore's
    key exchange takes it (the sign bit of x dropped)."""
    y = int.from_bytes(ed_public, "little") & ((1 << 255) - 1)
    return ((1 + y) * pow((1 - y) % _P, _P - 2, _P) % _P).to_bytes(32, "little")


def shared_secret(seed_hex, node_key_hex):
    """MeshCore's `ed25519_key_exchange` from our seed and the node's public key."""
    seed = bytes.fromhex(seed_hex.strip())
    if len(seed) != 32:
        raise EnvelopeError(f"the key must be 32 bytes, got {len(seed)}")
    node = node_key(node_key_hex) if node_key_hex else None
    if not node:
        raise EnvelopeError("v2 needs the node's whole public key")
    # MeshCore's clamp (e[31] &= 63; |= 64) leaves the same bits as RFC 7748's (&= 127; |= 64)
    scalar = hashlib.sha512(seed).digest()[:32]
    return x25519(scalar, montgomery_u(bytes.fromhex(node)))


def v2_key(seed_hex, node_key_hex, topic):
    """The command-plane key for one node and one command topic."""
    return HKDF(shared_secret(seed_hex, node_key_hex), 32, b"", SHA256,
                context=V2_INFO + topic.encode("utf-8"))


def v2_nonce(direction, counter):
    return bytes([direction]) + bytes(7) + counter.to_bytes(4, "big")


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text):
    if not text or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
                       for c in text) or len(text) % 4 == 1:
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def pad(data, sizes):
    """Zero-padded to the first size that holds it; None when none does."""
    for size in sizes:
        if len(data) <= size:
            return data + bytes(size - len(data))
    return None


def seal(key, direction, counter, aad, plaintext):
    cipher = ChaCha20_Poly1305.new(key=key, nonce=v2_nonce(direction, counter))
    cipher.update(aad)
    body, tag = cipher.encrypt_and_digest(plaintext)
    return body + tag


def open_sealed(key, direction, counter, aad, sealed):
    """The plaintext, or ValueError when the tag does not verify."""
    if len(sealed) < TAG_LEN:
        raise ValueError("too short")
    cipher = ChaCha20_Poly1305.new(key=key, nonce=v2_nonce(direction, counter))
    cipher.update(aad)
    return cipher.decrypt_and_verify(sealed[:-TAG_LEN], sealed[-TAG_LEN:])


def build_v2(seed_hex, topic, counter, command, node_key_hex, ttl_secs=DEFAULT_TTL_SECS, now=None):
    """One encrypted, signed v2 envelope for one node (ar-13 and later), as the payload."""
    now = int(time.time() if now is None else now)
    if not 0 < ttl_secs <= MAX_FUTURE_SECS:
        raise EnvelopeError(f"ttl is 1..{MAX_FUTURE_SECS} seconds")
    expiry = now + ttl_secs
    signed_part(counter, expiry, command)          # the same rules for the command as v1
    head = f"{V2_TAG}{FIELD_SEP}{counter}{FIELD_SEP}{expiry}"
    plain = pad(command.encode("ascii"), V2_COMMAND_PADS)
    sealed = seal(v2_key(seed_hex, node_key_hex, topic), DIR_COMMAND, counter, head.encode("ascii"), plain)
    part = f"{head}{FIELD_SEP}{_b64(sealed)}"
    signature = eddsa.new(key_from_seed(seed_hex), "rfc8032").sign(signed_message(topic, part))
    return f"{part}{FIELD_SEP}{signature.hex().upper()}"


def command_topic_of(result_topic_):
    """The command topic a result topic answers for: the key is derived from it."""
    if not result_topic_ or not result_topic_.endswith("/res"):
        return None
    return result_topic_[:-len("res")] + "cmd"


def parse_r3(payload, node, seed_hex, node_key_hex, topic):
    """An r3 line (ar-13) opened with the key for command topic `topic`, as a Result with
    `verified=True`; a tag that does not verify is a forged or altered line, returned as
    `verified=False` exactly as a bad r2 is. None for anything that is not an r3."""
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError:
            return None
    parts = (payload or "").split(FIELD_SEP)
    if len(parts) != 3 or parts[0] != R3_TAG or not parts[1].isdigit():
        return None
    counter = int(parts[1])
    if counter > 0xFFFFFFFF:
        return None
    forged = Result(node, counter, None, "", "", False)
    try:
        sealed = _unb64(parts[2])
    except ValueError:
        return forged
    try:
        key = v2_key(seed_hex, node_key_hex, topic)
    except (EnvelopeError, ValueError):
        return Result(node, counter, None, "", "", None)     # no key to open it with
    try:
        plain = open_sealed(key, DIR_RESULT, counter, f"{R3_TAG}{FIELD_SEP}{counter}".encode("ascii"), sealed)
    except ValueError:
        return forged
    fields = plain.rstrip(b"\0").decode("utf-8", "replace").split(FIELD_SEP, 2)
    if len(fields) != 3 or not fields[0].isdigit():
        return forged
    return Result(node, counter, int(fields[0]), fields[1], fields[2], True)
