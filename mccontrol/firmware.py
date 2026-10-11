"""What each firmware version admits over the control plane.

The fleet runs several versions at once and will for months, so the command list must know,
per device, what would come back `not-allowed` before a command slot is spent finding out.
One table, oldest first; a device's `ver` reply is matched on its revision: `lou-N` from
lou-14 (`esp32s3-v1.17.1-lou-14-arrc-observer-1a2b3c4d`), `ar-N` before it
(`esp32s3-v1.17.1-ar-10-observer-42f48ce9`). One counter: lou-14 follows ar-13. A version the
table does not know, or none at all, is treated as the oldest: nothing is offered that an old
node would refuse.

From lou-14 the version also names the image's features (firmware docs/fork.md, TODO 22a
ruling 8): `ar` auto-reply, `rc` this command plane, `as` auto-scope, run together in that
order. Every release before lou-14 carried both auto-reply and the plane, so a version with
no feature token is `arrc`. Without `ar` a node refuses the triggers and the `autoreply`
settings as not-allowed (ruling 10); without `rc` it has no plane at all.

The sets are taken from the firmware's own history (`src/helpers/MqttControlLogic.h`):

  ar-10   the fourteen-entry allowlist the field runs (built at 42f48ce9): the auto-reply
          family, the two triggers, and ten reads. Not `region`, not `ver`, not `get name`.
  ar-11   the deny list of 2026-10-08 (621218cb) and `get stats` (2c079ed7): everything
          `denylist.refusal` admits. `region`, `ver` and `get name` arrived between the
          two on the same branch and ship with ar-11.
  ar-12   ar-11's and `mqtt.cmd ping` (2026-10-10).
  ar-13   ar-12's, and the encrypted `v2` envelope (firmware TODO 17a): from here a command
          to one node is sent as v2, below it (and every broadcast) as v1.
  lou-14  ar-13's, less what the image's features leave out (above).
"""
import re

from . import denylist

AR10 = (
    "set autoreply", "get autoreply", "trigger test", "trigger private",
    "get txdelay", "get direct.txdelay", "get rxdelay", "get af", "get cad", "get int.thresh",
    "get radio", "get repeat", "get flood.max", "get flood.max.unscoped",
)
AR10_FAMILIES = ("set autoreply", "get autoreply", "trigger test", "trigger private")

# (tag, number, admitted): admitted is a tuple of families/exact entries, or None for
# "everything the deny list admits". Oldest first.
ADMITTED = (
    ("ar-10", 10, AR10),
    ("ar-11", 11, None),
    ("ar-12", 12, None),
    ("ar-13", 13, None),
    ("lou-14", 14, None),
)

FEATURE_TOKENS = ("as", "ar", "rc", "asar", "asrc", "arrc", "asarrc")
# Every image before lou-14 had auto-reply and the plane, and said nothing about it.
FEATURES_BEFORE_LOU = "arrc"
# What a node without auto-reply refuses: the firmware's mqttCtrlCommandAllowed (T-08 of F06).
AUTOREPLY_FAMILIES = ("trigger", "set autoreply", "get autoreply")

# Commands the deny list admits that arrived after ar-11: the revision that admits each.
ARRIVED = {"mqtt.cmd ping": 12}

# The first revision that opens a v2 envelope.
V2_FROM = 13


def revision(version_text):
    """The revision in a version string, or None: `-lou-N-` first, then `-ar-N-`.

    `-lou-` wins because an image with auto-reply alone reads `...-lou-14-ar-1a2b3c4d`,
    whose `-ar-` would otherwise give the hash's leading digits."""
    text = (version_text or "").lower()
    for tag in ("-lou-", "-ar-"):
        at = text.find(tag)
        if at < 0:
            continue
        m = re.match(r"\d+", text[at + len(tag):])
        return int(m.group()) if m else None
    return None


def features(version_text):
    """The features a version names (`ar`, `rc`, `arrc`, ...). A version before lou-14
    names none and had both: `arrc`. A lou version whose token is not a combination in the
    fixed order is not trusted with any: ``""``."""
    text = (version_text or "").lower()
    m = re.search(r"-lou-\d+-([a-z]+)-", text)
    if m is None:
        return "" if "-lou-" in text else FEATURES_BEFORE_LOU
    return m.group(1) if m.group(1) in FEATURE_TOKENS else ""


def has(version_text, code):
    """Does the node's image carry the feature `code` (`ar`, `rc`, `as`)?"""
    token = features(version_text)
    return code in [token[i:i + 2] for i in range(0, len(token), 2)]


def entry_for(version_text):
    """The table row a version falls under: the newest row whose number it reaches; an
    unknown or missing version falls to the oldest row."""
    rev = revision(version_text)
    row = ADMITTED[0]
    if rev is None:
        return row
    for candidate in ADMITTED:
        if rev >= candidate[1]:
            row = candidate
    return row


def _boundary(text, prefix):
    if not text.lower().startswith(prefix):
        return False
    return len(text) == len(prefix) or text[len(prefix)] in " ."


def _row_admits(row, command):
    text = " ".join((command or "").split())
    _tag, n, allowed = row
    if allowed is None:
        return denylist.refusal(text) is None and n >= ARRIVED.get(text.lower(), 0)
    low = text.lower()
    for entry in allowed:
        if entry in AR10_FAMILIES:
            if _boundary(text, entry):
                return True
        elif low == entry:
            return True
    return False


def admits(version_text, command):
    """Does a node on `version_text` admit `command`? Unknown versions are the oldest; a
    node without the plane admits nothing, and one without auto-reply none of its commands."""
    if not has(version_text, "rc"):
        return False
    text = " ".join((command or "").split())
    if not has(version_text, "ar") and any(_boundary(text, f) for f in AUTOREPLY_FAMILIES):
        return False
    return _row_admits(entry_for(version_text), command)


# The sub-type byte of the private test request a node sends for `trigger private` and
# answers: 0x04 up to ar-13, 0xF0 from lou-14, with no overlap (TODO 22a ruling 20, a hard
# cut-off). The node builds the request; this only says which byte each side speaks.
PRIVATE_SUBTYPE_OLD = 0x04
PRIVATE_SUBTYPE = 0xF0
PRIVATE_SUBTYPE_FROM = 14


def private_subtype(version_text):
    """The private test sub-type a node on `version_text` sends and answers. Unknown: the
    oldest, 0x04."""
    rev = revision(version_text)
    return PRIVATE_SUBTYPE if rev is not None and rev >= PRIVATE_SUBTYPE_FROM else PRIVATE_SUBTYPE_OLD


def lacks(version_text, command):
    """The feature a node's image is built without that `command` belongs to -- "ar" for a
    trigger or an `autoreply` setting on an image without auto-reply, "rc" for anything on
    one without the plane -- or None. Such a command is not too new for the node: it does
    not exist there, and is hidden rather than offered for a later build (ruling 10)."""
    if not has(version_text, "rc"):
        return "rc"
    text = " ".join((command or "").split())
    if not has(version_text, "ar") and any(_boundary(text, f) for f in AUTOREPLY_FAMILIES):
        return "ar"
    return None


def needs(command):
    """The oldest tag that admits `command`, or None when none does."""
    for row in ADMITTED:
        if _row_admits(row, command):
            return row[0]
    return None


def speaks_v2(version_text):
    """Does a node on `version_text` open the encrypted v2 envelope? Unknown: no."""
    rev = revision(version_text)
    return rev is not None and rev >= V2_FROM
