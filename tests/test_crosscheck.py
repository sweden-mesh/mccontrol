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


def _check(fixture, version):
    lines = [l.strip() for l in (pathlib.Path(__file__).parent / "data" / fixture).read_text().splitlines() if l.strip()]
    assert len(lines) > 100
    wrong = [(c, v) for v, c in (l.split("|", 1) for l in lines)
             if firmware.admits(version, c) != (v == "allowed")]
    assert wrong == [], wrong


def test_lou_14_arrc_matches_the_firmwares_verdicts():
    """`command-verdicts-lou-14-arrc.txt` is the firmware's `command-verdicts.txt` (fork/observer,
    test_mqtt_control, an image with auto-reply), copied at lou-14."""
    _check("command-verdicts-lou-14-arrc.txt", "esp32s3-v1.17.1-lou-14-arrc-observer-1a2b3c4d")


def test_lou_14_rc_matches_the_firmwares_verdicts():
    """`command-verdicts-lou-14-rc.txt` is the firmware's `command-verdicts-rc.txt`: the same
    commands in an image without auto-reply, where its triggers and settings are refused."""
    _check("command-verdicts-lou-14-rc.txt", "esp32s3-v1.17.1-lou-14-rc-observer-1a2b3c4d")
