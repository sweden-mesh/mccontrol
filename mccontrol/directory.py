"""The device directory as rows: what the left panel shows, computed from the store."""

import datetime

SORTS = ("name", "region", "last sent")


def relative(when, now=None):
    """`2 h`, `3 d`, `now`, or `-` for never: the age of an ISO timestamp, short enough for a column."""
    if not when:
        return "-"
    try:
        then = datetime.datetime.fromisoformat(when)
    except ValueError:
        return "?"
    now = now or datetime.datetime.now()
    secs = max(0, int((now - then).total_seconds()))
    if secs < 60:
        return "now"
    if secs < 3600:
        return f"{secs // 60} min"
    if secs < 86400:
        return f"{secs // 3600} h"
    return f"{secs // 86400} d"


def sorted_devices(devices, by="name"):
    if by == "region":
        return sorted(devices, key=lambda d: ((d.get("region") or "").upper(), (d.get("name") or "~" + d["node"]).lower()))
    if by == "last sent":
        return sorted(devices, key=lambda d: d.get("last_sent") or "", reverse=True)
    return sorted(devices, key=lambda d: (d.get("name") or "~" + d["node"]).lower())


def row(device, width, now=None):
    """One list row for a device, laid out to `width` columns.

    name (18)  node (8)  reg (4)  last sent (7)  result (rest)
    """
    name = (device.get("name") or "(unnamed)")[:18].ljust(18)
    node = device["node"][:8].ljust(8)
    region = (device.get("region") or "?")[:4].ljust(4)
    sent = relative(device.get("last_sent"), now)[:7].ljust(7)
    result = (device.get("last_result") or "")
    line = f"{name} {node} {region} {sent} {result}"
    return line[: width - 1].ljust(width - 1)


def header(width):
    return f"{'Name':18} {'Node':8} {'Reg':4} {'Sent':7} Result"[: width - 1]


def tag_footer(tagged, total):
    if not total:
        return "no devices"
    if tagged:
        return f"{tagged} of {total} tagged"
    return f"{total} device{'s' if total != 1 else ''}"


def by_region(devices, region):
    """Indexes of the devices in `region`, for tagging a region at once."""
    region = (region or "").upper()
    return [i for i, d in enumerate(devices) if (d.get("region") or "").upper() == region]
