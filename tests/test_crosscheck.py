"""The firmware's verdicts and the tool's tables agree, line for line.

`tests/data/command-verdicts-ar-11.txt` is written by the firmware's own host test
(`test_mqtt_control`, `CrossCheck.EveryKnownCommandsVerdictIsWritten`) from
`mqttCtrlCommandAllowed`; it is copied here at each release (U25). A line that disagrees
with `denylist.refusal` or `firmware.admits(ar-11)` is a drift to fix on one side or the
other, never to paper over."""
import pathlib

from mccontrol import denylist, firmware

FIXTURE = pathlib.Path(__file__).parent / "data" / "command-verdicts-ar-11.txt"


def test_the_firmwares_verdicts_match_the_denylist_and_the_ar_11_row():
    lines = [l.strip() for l in FIXTURE.read_text().splitlines() if l.strip()]
    assert len(lines) > 100
    mismatches = []
    for line in lines:
        verdict, command = line.split("|", 1)
        ours = denylist.refusal(command) is None
        admits = firmware.admits("esp32s3-v1.17.1-ar-11-x", command)
        if ours != (verdict == "allowed") or admits != (verdict == "allowed"):
            mismatches.append((command, verdict, "tool allows" if ours else "tool refuses", "ar-11 admits" if admits else "ar-11 refuses"))
    assert mismatches == [], mismatches
