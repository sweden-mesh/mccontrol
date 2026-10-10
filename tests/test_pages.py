"""A17: one page per interface beside its code, the README a summary that links to them. Every
relative link resolves, each interface's name in the README is a link to its page, and every
key a page names is a key that interface has (as test_help.py checks the help texts)."""
import pathlib
import re

from test_help import _has, _key_names, named_keys

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ("Monolith", "MeshcoreCommander", "IBMi")
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
TICKED = re.compile(r"`([!-/:-~])`")                  # a single key in backticks: `s`, `:`, `*` (no digit)


def _pages():
    return [ROOT / "README.md"] + [ROOT / "mccontrol" / "ui" / ui / "README.md" for ui in UI]


def test_every_relative_link_on_the_readme_and_the_pages_resolves():
    for page in _pages():
        assert page.is_file(), page
        for target in LINK.findall(page.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            assert (page.parent / target).resolve().is_file(), (page.relative_to(ROOT), target)


def test_the_readme_links_each_interface_to_its_page_and_says_monolith_is_the_default():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for ui in UI:
        assert f"[{ui}](mccontrol/ui/{ui}/README.md)" in text, ui
    assert "**[Monolith](mccontrol/ui/Monolith/README.md) is the default**" in text


def test_every_key_a_page_names_is_a_key_that_interface_has():
    names = _key_names()
    for ui in UI:
        text = (ROOT / "mccontrol" / "ui" / ui / "README.md").read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.S)       # the screen captures are not prose
        named = named_keys(text) + TICKED.findall(text)
        unknown = sorted({k for k in named if not _has(k, names[ui])})
        assert not unknown, (ui, unknown)
