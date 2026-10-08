import re

from mccontrol import helptext


def test_every_topic_loads_and_every_link_points_at_a_topic_in_every_interface():
    for ui in [None] + helptext.interfaces():
        names = helptext.topics(ui)
        assert "index" in names and "commands" in names and len(names) >= 12
        for name in names:
            topic = helptext.load(name, ui)
            assert topic.title and topic.lines, (ui, name)
            for _line, _s, _e, target in topic.links:
                assert target in names, f"{ui}: {name} links to missing {target}"


def test_the_index_reaches_every_topic_in_every_interface():
    for ui in [None] + helptext.interfaces():
        seen, todo = set(), ["index"]
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            todo += [t for *_r, t in helptext.load(name, ui).links]
        assert seen == set(helptext.topics(ui)), ui


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


# -- the keys each interface's help names ----------------------------------------------

KEY = re.compile(r"\b(?:(?:Shift|Alt|Ctrl)\+)?(?:F\d{1,2}(?:-F\d{1,2})?|Enter|Esc|Tab|Ins|Del|Space|Backspace|PgUp|PgDn"
                 r"|Up|Down|Left|Right|Home|End)\b|\b(?:Shift|Alt|Ctrl)\+(?:\d(?:\.\.\d)?|[A-Z])\b|\"([a-z?:])\"")
WIDGET = re.compile(r"\bthe (?:screen|dialog|panel|menu|palette|tile)\b", re.I)    # one interface's furniture
MENU = re.compile(r"\b(?:Devices|Send|View|Keys|Options|Help)\s+>\s")


def named_keys(text):
    """The keys a help text names: F-keys, Shift/Alt/Ctrl chords, the named keys (Enter,
    Esc, Tab, Ins, Del, Space, Backspace, the arrows, PgUp, PgDn, Home, End), and a letter,
    ? or : in double quotes ("s"). A bare letter in a key table is not seen."""
    return [m.group(1) or m.group(0) for m in KEY.finditer(text)]


def _has(key, names):
    """Is `key` among `names`, where Alt+1..9 holds every Alt+digit and F13-F21 every key in it."""
    if key in names:
        return True
    if re.fullmatch(r"Alt\+\d", key):
        return "Alt+1..9" in names
    m = re.fullmatch(r"F(\d+)", key)
    return bool(m) and any(re.fullmatch(r"F(\d+)-F(\d+)", r) and int(r.split("-")[0][1:]) <= int(m.group(1)) <= int(r.split("-")[1][1:])
                           for r in names)


def _key_names():
    from mccontrol.ui.IBMi import app as ibmi
    from mccontrol.ui.MeshcoreCommander import app as commander
    from mccontrol.ui.Monolith import board
    return {"MeshcoreCommander": commander.KEY_NAMES, "IBMi": ibmi.KEY_NAMES, "Monolith": board.KEY_NAMES}


# The topics that are how an interface works; every interface has its own text for each.
MECHANICS = {"index", "keys", "screen", "dialogs", "commandlist", "devices", "send"}
EXTRA = {}          # an interface's own text for a topic outside MECHANICS, by interface, when one is earned


def test_every_key_an_interfaces_help_names_is_a_key_it_has():
    for ui, names in _key_names().items():
        paths = sorted((helptext.HERE / ui).glob("*.txt"))
        assert {p.stem for p in paths} == MECHANICS | EXTRA.get(ui, set()), ui
        for path in paths:
            text = path.read_text(encoding="utf-8")
            unknown = [k for k in named_keys(text) if not _has(k, names)]
            assert not unknown, (ui, path.name, unknown)
            assert ui == "MeshcoreCommander" or not MENU.search(text), (ui, path.name, MENU.findall(text))


def test_the_shared_texts_name_no_interfaces_keys_or_menus():
    for path in sorted(helptext.HERE.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        assert named_keys(text) == [] and not MENU.search(text), (path.name, named_keys(text), MENU.findall(text))
        assert not WIDGET.search(text), (path.name, WIDGET.findall(text))


def test_the_key_names_are_found_where_they_are_written():
    assert named_keys('F1 help; Shift+F1, Alt+1..9, Ctrl+P and F13-F21; "n" reads, "*" is a region; Up') == [
        "F1", "Shift+F1", "Alt+1..9", "Ctrl+P", "F13-F21", "n", "Up"]
    assert _has("Alt+4", ["Alt+1..9"]) and _has("F15", ["F13-F21"]) and not _has("F22", ["F13-F21"])
    assert not _has("F2", ["F1"]) and not _has("Alt+4", ["F1"])


def test_the_interfaces_texts_ship_with_the_package():
    import pathlib
    pyproject = (pathlib.Path(helptext.__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert '"help/*.txt"' in pyproject and '"help/*/*.txt"' in pyproject
    assert {p.name for p in helptext.HERE.iterdir() if p.is_dir()} <= set(helptext.interfaces())
