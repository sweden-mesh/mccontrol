"""The facts a device's answers carry: the parser, and the newest answer winning."""
from mccontrol import facts


def test_each_known_answer_maps_to_its_fact_keys():
    assert facts.parse("ver", "> esp32s3-v1.17.1-ar-11-observer-90e86c88") == {"version": "esp32s3-v1.17.1-ar-11-observer-90e86c88"}
    assert facts.parse("region", "> * F\n se-jkp F") == {"regions": "* F\n se-jkp F"}
    assert facts.parse("get txdelay", "> 0.5") == {"txdelay": "0.5"}
    assert facts.parse("get  direct.txdelay", "> 0.2") == {"direct.txdelay": "0.2"}
    assert facts.parse("get name", "> SE-JKG-Gisebo") == {"name": "SE-JKG-Gisebo"}
    assert facts.parse("get autoreply.window", "> 10 120") == {"autoreply.window": "10 120"}
    assert facts.parse("get autoreply", "> on #test-jkg 'test' max 8 hops, direct zero-hop, mode flood, private off") == {
        "autoreply": "on", "autoreply.channel": "#test-jkg", "autoreply.hops": "8", "autoreply.direct.flood": "off",
        "autoreply.mode": "flood", "autoreply.private": "off"}
    assert facts.parse("get autoreply", "> off, no region - set mqtt.iata") == {"autoreply": "off"}
    assert facts.parse("set txdelay 0.7", "OK") == {"txdelay": "0.7"}
    assert facts.parse("set autoreply.window 10 120", "OK - saved") == {"autoreply.window": "10 120"}


def test_what_the_table_does_not_know_is_none_and_a_failed_write_teaches_nothing():
    assert facts.parse("neighbors", "> 0 neighbours") is None
    assert facts.parse("get stats", '> {"batt":4100}') is None
    assert facts.parse("get txdelay", "Err - unknown config") is None
    assert facts.parse("set txdelay 9", "Err - out of range") == {}
    assert facts.parse("", "> x") is None


def test_the_newest_answer_wins_and_unknown_reads_are_raw():
    reads = [("get txdelay", "> 0.5", "2026-10-01 10:00:00"), ("neighbors", "> 2", "2026-10-02 10:00:00")]
    history = [{"at": "2026-10-03 10:00:00", "command": "set txdelay 0.9", "code": 0, "reply": "OK"},
               {"at": "2026-10-04 10:00:00", "command": "set txdelay 1.5", "code": 14, "reply": None},
               {"at": "2026-10-05 10:00:00", "command": "reboot", "code": 0, "reply": "OK"},
               {"at": "2026-10-01 10:00:00", "command": "get txdelay", "code": 0, "reply": "> 0.5"}]
    known, raw = facts.derive(reads, history)
    assert known == {"txdelay": {"value": "0.9", "at": "2026-10-03 10:00:00", "source": "set txdelay 0.9"}}
    assert raw == {"neighbors": {"reply": "> 2", "at": "2026-10-02 10:00:00"}}     # reboot is no read
    assert facts.reader("txdelay") == "get txdelay" and facts.reader("version") == "ver"
    assert facts.reader("autoreply.hops") == "get autoreply.hops" and facts.reader("regions") == "region"
    assert sorted(["zzz", "regions", "name", "abc"], key=facts.order) == ["name", "regions", "abc", "zzz"]
    assert max(len(label) for label in facts.LABELS.values()) <= 17          # the stats tile's label column
