"""The node's own counters, as `get stats` answers them over the control plane (firmware
2026-10-08): one line of short `k=v` pairs,

    > batt=4261mV up=1496188s noise=-114 tx=5518s rx=25369s sent=9065 recv=44345 err=10 rxerr=13369 q=0

mapped here to the long names the status block and Beacon use, so one set of labels
(`beacon.STAT_FIELDS`) serves the live sample and the kept one."""

SHORT_TO_LONG = {
    "batt": "battery_mv", "up": "uptime_secs", "noise": "noise_floor", "tx": "tx_air_secs",
    "rx": "rx_air_secs", "sent": "packets_sent", "recv": "packets_received", "err": "errors",
    "rxerr": "recv_errors", "q": "queue_len",
}


def parse(reply):
    """`{long_key: int}` from a `get stats` reply, or `{}` for anything that is not one."""
    text = (reply or "").strip().lstrip(">").strip()
    block = {}
    for pair in text.split():
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        long = SHORT_TO_LONG.get(key)
        if not long:
            continue
        digits = value.rstrip("mVs")
        try:
            block[long] = int(digits)
        except ValueError:
            continue
    return block
