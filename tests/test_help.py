from mccontrol import helptext


def test_every_topic_loads_and_every_link_points_at_a_topic():
    names = helptext.topics()
    assert "index" in names and "commands" in names and len(names) >= 12
    for name in names:
        topic = helptext.load(name)
        assert topic.title and topic.lines, name
        for _line, _s, _e, target in topic.links:
            assert target in names, f"{name} links to missing {target}"


def test_the_index_reaches_every_topic():
    seen, todo = set(), ["index"]
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        todo += [t for *_r, t in helptext.load(name).links]
    assert seen == set(helptext.topics())


def test_links_are_resolved_to_positions_and_the_text_form_shows_them_in_brackets():
    topic = helptext.load("dialogs")
    line, start, end, target = topic.links[0]
    assert topic.lines[line][start:end] == target == "cautions"
    assert "[cautions]" in helptext.as_text("dialogs")
    assert helptext.as_text("no-such") is None


def test_f1_context_is_known_for_every_place():
    assert helptext.CONTEXT["main"] == "screen" and helptext.CONTEXT["found"] == "discover"


# -- per interface ----------------------------------------------------------------------

def _texts(tmp_path, monkeypatch):
    """A help tree of three topics: `b` has an IBMi text of its own, `c` exists only there."""
    (tmp_path / "IBMi").mkdir()
    (tmp_path / "a.txt").write_text("A\n\nshared a, see [[b]]\n")
    (tmp_path / "b.txt").write_text("B\n\nshared b\n")
    (tmp_path / "IBMi" / "b.txt").write_text("B on the green screen\n\nF12 goes back; see [[c]]\n")
    (tmp_path / "IBMi" / "c.txt").write_text("C\n\nonly here\n")
    monkeypatch.setattr(helptext, "HERE", tmp_path)


def test_an_interfaces_own_text_wins_and_a_topic_without_one_falls_back_to_the_shared(tmp_path, monkeypatch):
    _texts(tmp_path, monkeypatch)
    assert helptext.load("b", "IBMi").title == "B on the green screen"
    assert helptext.load("a", "IBMi").lines == ["shared a, see b"]               # no IBMi text: the shared one
    assert helptext.load("b").title == helptext.load("b", "Monolith").title == "B"
    assert helptext.topics() == ["a", "b"] and helptext.topics("IBMi") == ["a", "b", "c"]
    assert helptext.load("c") is None and helptext.load("c", "IBMi").links == []
    assert helptext.load("b", "IBMi").links[0][3] == "c"
    assert "[c]" in helptext.as_text("b", "IBMi")


def test_an_interface_that_is_not_there_is_refused_and_cannot_reach_outside_the_help(tmp_path, monkeypatch):
    _texts(tmp_path, monkeypatch)
    for ui in ("Nope", "..", "../help", "ibmi", ""):
        try:
            helptext.load("a", ui or "x/..")
        except ValueError:
            continue
        raise AssertionError(ui)
    assert helptext.load("../a", "IBMi") is None
    assert helptext.interfaces() == ["IBMi", "MeshcoreCommander", "Monolith"]


def test_the_context_is_per_interface():
    assert helptext.context()["main"] == "screen" and helptext.context("IBMi")["main"] == "screen"
    assert helptext.context("Monolith")["main"] == "keys" and helptext.context("Monolith")["send"] == "send"
