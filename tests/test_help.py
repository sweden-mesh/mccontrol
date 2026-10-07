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
