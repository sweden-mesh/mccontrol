from mccontrol import actions, commands, denylist


def test_every_entry_travels_and_the_groups_are_in_order():
    assert commands.check() == []
    assert [g for g, _ in commands.grouped()] == list(commands.GROUPS)
    assert all(items for _, items in commands.grouped())


def test_build_appends_the_values():
    by_text = {c.text: c for c in commands.TABLE}
    c = by_text["set autoreply"]
    assert commands.build(c, ["off"]) == "set autoreply off"
    assert commands.build(by_text["trigger test"], [""]) == "trigger test"
    assert commands.build(by_text["region put"], ["se-jkp *"]) == "region put se-jkp *"


def test_reads_are_what_is_worth_keeping():
    assert commands.is_read("ver") and commands.is_read("get autoreply.hops") and commands.is_read("region")
    assert commands.is_read("GET  name")
    assert not commands.is_read("set autoreply on") and not commands.is_read("reboot") and not commands.is_read("region put x *")


def test_every_caution_names_something_the_table_offers():
    for prefix, _why in actions.CAUTIONS:
        head = prefix.rstrip("*").strip()
        assert any(c.text.startswith(head) or head.startswith(c.text) for c in commands.TABLE), prefix


def test_nothing_denied_is_offered():
    for c in commands.TABLE:
        assert denylist.refusal(c.text if c.kind == "none" else c.text + " x") is None or c.text in ("get", "set", "trigger private"), c.text
