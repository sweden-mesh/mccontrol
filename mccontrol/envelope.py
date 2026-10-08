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

import secrets
import time

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
