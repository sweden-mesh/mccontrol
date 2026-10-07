"""Statistics from a MeshCore Beacon server (github.com/MeshCore-Beacon/beacon-server).

Beacon ingests the MeshCore MQTT brokers and serves a read-only REST API with no
authentication. Every auto-reply node is an observer, so Beacon holds, for each node this
tool can command, the status block the node publishes (battery, uptime, noise floor,
airtime, packets, errors) and hourly history of it. The device's own counters cannot be
read over the control plane (they are serial-only), which is why this exists; see
`planning/mccontrol/statistics-research-20261007.md` in the workspace.

The shapes this relies on, verified against beacon.meshat.se 2.0.3 on 2026-10-07:

    GET /nodes?iata=<R>&limit=200      items[]: publicKey, observerId (null for a node that
                                       has never published a status), name, nodeTypeName
    GET /observers/<id>                statusMetadata.stats {...}, lastStatusAt (epoch ms),
                                       firmwareVersion, hardwareModel, batteryLevel
    GET /observers/<id>/telemetry      ?range=24h|48h|168h&interval=1h|6h|24h -> points[]

Plain HTTP reads; nothing here transmits. A failure is a `BeaconError` that says what was
asked; a number is never shown without the age of the sample it came from.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "https://beacon.meshat.se/api/v1"
TIMEOUT_SECS = 10
RANGES = ("24h", "48h", "168h")
INTERVALS = ("1h", "6h", "24h")

# The stats block, in the order a person reads it, with units.
STAT_FIELDS = [
    ("battery_mv", "battery", "mV"),
    ("uptime_secs", "uptime", "s"),
    ("noise_floor", "noise floor", "dBm"),
    ("tx_air_secs", "tx airtime", "s"),
    ("rx_air_secs", "rx airtime", "s"),
    ("packets_sent", "packets sent", ""),
    ("packets_received", "packets received", ""),
    ("errors", "errors", ""),
    ("recv_errors", "receive errors", ""),
    ("queue_len", "queue", ""),
    ("internal_heap", "free heap", "B"),
]


class BeaconError(Exception):
    """Beacon could not be asked, or did not know. Shown as "no statistics", never as zeros."""


def base_url(config):
    return (config.get("beacon_url") or DEFAULT_URL).rstrip("/")


def _get(config, path, params=None):
    """One GET, as parsed JSON. `MCCONTROL_FAKE_BEACON` names a JSON file whose keys are
    paths (`/observers/<id>`), for the tests; then no network is touched."""
    fake = os.environ.get("MCCONTROL_FAKE_BEACON")
    if fake is not None:
        with open(fake, encoding="utf-8") as f:
            table = json.load(f)
        if path not in table:
            raise BeaconError(f"Beacon has no {path}")
        return table[path]
    url = base_url(config) + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=TIMEOUT_SECS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise BeaconError(f"Beacon answered {exc.code} for {path}") from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise BeaconError(f"Beacon unreachable at {base_url(config)}: {exc}") from None


def observer_for(store, device, refresh=False):
    """Beacon's observer id for a device, cached on the device as `observer_id`.

    One `/nodes?iata=` read per region finds every device's id at once; a node that has
    never published a status has none, and that is "no statistics", not an error here."""
    cached = device.get("observer_id")
    if cached and not refresh:
        return cached
    region = (device.get("region") or "").upper()
    if not region:
        raise BeaconError(f"{device.get('name') or device['node']} has no region")
    data = _get(store.config(), "/nodes", {"iata": region, "limit": 200})
    by_node = {}
    for item in data.get("items", []):
        key = (item.get("publicKey") or "").upper()
        if len(key) >= 8:
            by_node[key[:8]] = item.get("observerId")
    for d in store.devices():                      # fill in every device of the region at once
        if (d.get("region") or "").upper() == region and d["node"] in by_node:
            oid = by_node[d["node"]]
            if oid and oid != d.get("observer_id"):
                store.update_device(d["node"], observer_id=oid)
    oid = by_node.get(device["node"])
    if device["node"] not in by_node:
        raise BeaconError(f"Beacon does not know {device['node']} in {region}")
    if not oid:
        raise BeaconError(f"{device.get('name') or device['node']} has never published a status to Beacon")
    return oid


def stats(store, device, now=None):
    """The latest status block for a device, with its age.

    Returns `{"stats": {...}, "sampled_at": epoch secs, "age_secs": int, "firmware": str,
    "model": str, "name": str}`. Raises `BeaconError` when there is none."""
    oid = observer_for(store, device)
    data = _get(store.config(), f"/observers/{oid}")
    block = ((data.get("statusMetadata") or {}).get("stats")) or {}
    sampled_ms = data.get("lastStatusAt")
    if not block or not sampled_ms:
        raise BeaconError(f"Beacon holds no status for {device.get('name') or device['node']}")
    sampled = int(sampled_ms) / 1000.0
    now = time.time() if now is None else now
    return {
        "stats": block,
        "sampled_at": sampled,
        "age_secs": max(0, int(now - sampled)),
        "firmware": data.get("firmwareVersion") or "",
        "model": data.get("hardwareModel") or "",
        "name": data.get("displayName") or "",
    }


def telemetry(store, device, range_="24h", interval="6h"):
    """The telemetry buckets for a device: `[{t, batteryMv, airtimeTxSecs, airtimeRxSecs,
    noiseFloorDb, uptimeSeconds, queueLength, receiveErrors}]`, oldest first."""
    if range_ not in RANGES or interval not in INTERVALS:
        raise BeaconError(f"range is one of {', '.join(RANGES)}; interval one of {', '.join(INTERVALS)}")
    oid = observer_for(store, device)
    data = _get(store.config(), f"/observers/{oid}/telemetry", {"range": range_, "interval": interval})
    return list(data.get("points") or [])


def age_text(secs):
    if secs < 90:
        return f"{secs} s ago"
    if secs < 5400:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600} h ago"
    return f"{secs // 86400} d ago"


def stat_lines(sample):
    """The stats block as `label  value unit` lines, the sample's age first."""
    lines = [f"sampled {age_text(sample['age_secs'])}"
             + (f"  {sample['model']}" if sample["model"] else "")
             + (f"  {sample['firmware']}" if sample["firmware"] else "")]
    block = sample["stats"]
    for key, label, unit in STAT_FIELDS:
        if key in block:
            value = block[key]
            if key == "uptime_secs":
                value, unit = f"{value // 86400} d {(value % 86400) // 3600} h", ""
            lines.append(f"{label:18} {value} {unit}".rstrip())
    return lines


def telemetry_lines(points):
    """One line per bucket: time, battery, noise floor, tx/rx airtime, receive errors."""
    lines = [f"{'time':13} {'batt':>5} {'noise':>6} {'tx s':>6} {'rx s':>6} {'rxerr':>6}"]
    for p in points:
        t = time.strftime("%m-%d %H:%M", time.localtime(int(p.get("t", 0)) / 1000))
        nf = p.get("noiseFloorDb")
        lines.append(f"{t:13} {p.get('batteryMv', '-'):>5} {('%.0f' % nf) if nf is not None else '-':>6} "
                     f"{p.get('airtimeTxSecs', '-'):>6} {p.get('airtimeRxSecs', '-'):>6} {p.get('receiveErrors', '-'):>6}")
    return lines
