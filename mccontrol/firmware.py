"""What each firmware version admits over the control plane.

The fleet runs several versions at once and will for months, so the command list must know,
per device, what would come back `not-allowed` before a command slot is spent finding out.
One table, oldest first; a device's `ver` reply (`esp32s3-v1.17.1-ar-10-observer-42f48ce9`)
is matched on its `ar-N` tag. A version the table does not know, or none at all, is treated
as the oldest: nothing is offered that an old node would refuse.

The sets are taken from the firmware's own history (`src/helpers/MqttControlLogic.h`):

  ar-10   the fourteen-entry allowlist the field runs (built at 42f48ce9): the auto-reply
          family, the two triggers, and ten reads. Not `region`, not `ver`, not `get name`.
  ar-11   the deny list of 2026-10-08 (621218cb) and `get stats` (2c079ed7): everything
          `denylist.refusal` admits. `region`, `ver` and `get name` arrived between the
          two on the same branch and ship with ar-11.
  ar-12   ar-11's and `mqtt.cmd ping` (2026-10-10).
  ar-13   ar-12's, and the encrypted `v2` envelope (firmware TODO 17a): from here a command
          to one node is sent as v2, below it (and every broadcast) as v1.
"""

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
)

# Commands the deny list admits that arrived after ar-11: the revision that admits each.
ARRIVED = {"mqtt.cmd ping": 12}

# The first revision that opens a v2 envelope.
V2_FROM = 13


def revision(version_text):
    """The `ar-N` number in a version string, or None."""
    text = (version_text or "").lower()
    at = text.find("-ar-")
    if at < 0:
        return None
    digits = ""
    for ch in text[at + 4:]:
        if ch.isdigit():
            digits += ch
        else:
            break
    return int(digits) if digits else None


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
    """Does a node on `version_text` admit `command`? Unknown versions are the oldest."""
    return _row_admits(entry_for(version_text), command)


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
