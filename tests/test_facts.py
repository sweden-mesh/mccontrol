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
    reads = [("get txdelay", "> 0.5", "2026-10-01 10:00:00", True), ("neighbors", "> 2", "2026-10-02 10:00:00", None)]
    history = [{"at": "2026-10-03 10:00:00", "command": "set txdelay 0.9", "code": 0, "reply": "OK"},
               {"at": "2026-10-04 10:00:00", "command": "set txdelay 1.5", "code": 14, "reply": None},
               {"at": "2026-10-05 10:00:00", "command": "reboot", "code": 0, "reply": "OK"},
               {"at": "2026-10-01 10:00:00", "command": "get txdelay", "code": 0, "reply": "> 0.5"}]
    known, raw = facts.derive(reads, history)
    assert known == {"txdelay": {"value": "0.9", "at": "2026-10-03 10:00:00", "source": "set txdelay 0.9", "verified": None}}
    assert raw == {"neighbors": {"reply": "> 2", "at": "2026-10-02 10:00:00", "verified": None}}     # reboot is no read
    assert facts.reader("txdelay") == "get txdelay" and facts.reader("version") == "ver"
    assert facts.reader("autoreply.hops") == "get autoreply.hops" and facts.reader("regions") == "region"
    assert sorted(["zzz", "regions", "name", "abc"], key=facts.order) == ["name", "regions", "abc", "zzz"]
    assert max(len(label) for label in facts.LABELS.values()) <= 17          # the stats tile's label column


def test_a_forged_answer_teaches_nothing_and_an_unsigned_one_says_so(tmp_path):
    from mccontrol import envelope, store as _store
    st = _store.Store(tmp_path)
    st.add_device("Gisebo", "4AC61A7F", "JKG")
    st.record("4AC61A7F", "ver", "t", envelope.Result("4AC61A7F", 1, 0, "ver", "> real-fw", verified=True))
    st.record("4AC61A7F", "ver", "t", envelope.Result("4AC61A7F", 2, 0, "ver", "> forged-fw", verified=False))
    st.record("4AC61A7F", "get txdelay", "t", envelope.Result("4AC61A7F", 3, 0, "get txdelay", "> 0.5"))   # r1
    lines = st.history("4AC61A7F")
    assert [line["verified"] for line in lines] == [True, False, None]
    known, _raw = facts.derive([], lines)
    assert known["version"]["value"] == "real-fw" and known["version"]["verified"] is True
    assert known["txdelay"] == {"value": "0.5", "at": lines[2]["at"], "source": "get txdelay", "verified": None}
    old = {k: v for k, v in lines[0].items() if k != "verified"}                    # a line from before
    (tmp_path / "history" / "4AC61A7F.jsonl").write_text(__import__("json").dumps(old) + "\n")
    assert st.history("4AC61A7F")[0]["verified"] is None
