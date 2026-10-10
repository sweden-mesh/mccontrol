"""A16: every verb Monolith has, and the three features of Sprint 12, has a counterpart in
MeshcoreCommander and in IBMi. A verb added to Monolith without one fails here."""
import inspect

from mccontrol.ui.IBMi import app as ibmi
from mccontrol.ui.MeshcoreCommander import app as commander
from mccontrol.ui.Monolith import board

# Monolith's verb (its v_ method) -> (MeshcoreCommander, IBMi). A name is a method of the
# interface's App; "do:<action>" an action its menu dispatches; "verb:<word>" a command its
# ===> line takes.
PARITY = {
    "commands": ("command_list", "commands_for"), "command": ("parameters", "parameters"),
    "send": ("send", "send_form"), "region": ("do:all", "region_form"), "alt": ("shortcut", "shortcut"),
    "runset": ("run_set", "run_set_form"), "saveset": ("sets_dialog", "set_form"),
    "removeset": ("sets_dialog", "work_with_sets"), "discover": ("discover", "discover"),
    "names": ("refresh_names", "names"), "fleet": ("refresh_fleet", "fleet"), "stats": ("statistics", "statistics"),
    "history": ("history_window", "display_device"), "reread": ("reread", "facts"), "open": ("open_line", "results"),
    "edit": ("edit", "change_device"), "forget": ("forget", "forget"), "keys": ("keys_dialog", "keys_screen"),
    "generate": ("keys_dialog", "keys_screen"), "import": ("keys_dialog", "keys_screen"),
    "register": ("keys_dialog", "keys_screen"), "setup": ("setup_dialog", "config_form"), "test": ("test_dialog", "test"),
    "columns": ("columns_dialog", "columns"), "shortcuts": ("shortcuts_dialog", "shortcuts_form"),
    "tagregion": ("tag_region", "verb:tag"), "invert": ("sync_tags", "verb:invert"), "untagall": ("do:untagall", "verb:untag"),
    "sort": ("do:sort:", "verb:sort"), "sortnext": ("do:sort:", "verb:sort"), "help": ("help", "help"),
}
EXEMPT = {"phosphor": "amber or green is Monolith's own look; the other two keep their own colours"}
# Sprint 12's features: (Monolith, MeshcoreCommander, IBMi)
FEATURES = {
    "facts (A12)": ("draw_device", "show_reads", "facts"),
    "a command line whose replies scroll up (A13)": ("submit", "from_line", "command_entry"),
    "the command history in blocks (A14)": ("draw_history", "show_blocks", "command_history"),
}


def _has(cls, ref):
    if ref.startswith("do:"):
        return f'"{ref[3:]}' in inspect.getsource(cls.do)
    if ref.startswith("verb:"):
        return f'"{ref[5:]}"' in inspect.getsource(cls.verb)
    return callable(getattr(cls, ref, None))


def test_every_monolith_verb_has_a_counterpart_in_the_other_two():
    verbs = {name[2:] for name in dir(board.Board) if name.startswith("v_")}
    assert verbs - set(PARITY) - set(EXEMPT) == set(), "Monolith verbs with no counterpart named here"
    assert set(PARITY) - verbs == set(), "counterparts for verbs Monolith no longer has"
    for verb, (mc, i) in PARITY.items():
        assert _has(commander.App, mc), (verb, "MeshcoreCommander", mc)
        assert _has(ibmi.App, i), (verb, "IBMi", i)


def test_the_sprint_12_features_are_in_all_three():
    for feature, (mono, mc, i) in FEATURES.items():
        assert _has(board.Board, mono) and _has(commander.App, mc) and _has(ibmi.App, i), feature
