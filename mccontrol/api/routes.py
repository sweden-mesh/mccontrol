"""The quick routes: configuration, keys, devices, tags, sets, shortcuts, history, the
command table and help. Each reads or writes the store and answers at once; anything that
waits on the broker or Beacon is a job (`jobs.py`)."""

import pathlib

from .. import actions, commands, denylist, directory, envelope, facts, firmware, helptext, store as _store
from .errors import ApiError, bad, not_found

SPEC = pathlib.Path(__file__).with_name("openapi.json")
SWAGGER = "https://unpkg.com/swagger-ui-dist@5"


class Html:
    def __init__(self, text):
        self.text = text


# -- helpers -----------------------------------------------------------------------------

def device_of(ctx, ref):
    d = ctx.store.device(ref)
    if d is None:
        raise not_found(f"no device matches {ref!r}")
    return d


def devices_of(ctx, refs):
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise bad("devices is a list of device names or node ids")
    return [device_of(ctx, r) for r in refs]


def _str(body, key, default=None):
    value = body.get(key, default)
    if value is not None and not isinstance(value, str):
        raise bad(f"{key} is a string")
    return value


def _int(query, key, default):
    try:
        return int(query.get(key, default))
    except ValueError:
        raise bad(f"{key} is an integer") from None


def view(ctx, d, columns=None, width=100):
    """A device as the API shows it: the stored fields, without the reads (their own route),
    plus the directory's cells and row, stale and tagged."""
    out = {k: v for k, v in d.items() if k != "reads"}
    for k in ("key", "added", "last_sent", "last_result", "last_heard", "ver", "fleet", "observer_id"):
        out.setdefault(k, None)
    out.setdefault("notes", "")
    out["tagged"] = d["node"] in ctx.tags
    out["stale"] = directory.stale(d)
    out["cells"] = directory.cells(d)
    out["row"] = directory.row(d, width, columns=columns).rstrip()
    return out


def tags_view(ctx):
    known = {d["node"] for d in ctx.store.devices()}
    ctx.tags &= known                   # a forgotten device is no longer tagged
    return {"tagged": sorted(ctx.tags), "footer": directory.tag_footer(len(ctx.tags), len(known))}


def probe(c):
    """The sample a table entry's admission is judged on, as the command list judges it."""
    if c.text in ("get", "set"):
        return "get autoreply" if c.text == "get" else "set txdelay 0.5"
    return commands.build(c, [c.choices[-1]] if c.kind in ("choice", "multi") and c.choices
                          else ([c.examples[0]] if c.kind == "text" and c.examples and not c.examples[0].startswith("<") else []))


def command_list(d):
    version = d.get("ver") if d else None
    groups, greyed = [], 0
    for group, cs in commands.grouped():
        rows = []
        for c in cs:
            sample = probe(c)
            admitted = firmware.admits(version, sample) if d else None
            greyed += admitted is False
            rows.append({"text": c.text, "group": c.group, "kind": c.kind, "choices": list(c.choices),
                         "examples": list(c.examples), "read": c.read, "probe": sample, "admitted": admitted,
                         "needs": firmware.needs(sample), "refusal": denylist.refusal(sample),
                         "caution": actions.caution(sample)})
        groups.append({"group": group, "commands": rows})
    return {"node": d["node"] if d else None, "firmware": firmware.entry_for(version)[0] if d else None,
            "version": version, "revision": firmware.revision(version) if d else None, "greyed": greyed, "groups": groups}


def public_config(store):
    """The configuration without the broker's password -- only whether one is set -- and
    without the sets and shortcuts, which have their own routes."""
    config = store.config()
    out = {k: v for k, v in config.items() if k not in ("sets", "shortcuts", "password")}
    out["password_set"] = bool(config.get("password"))
    out.setdefault("ui_settings", {})
    return out


# -- meta --------------------------------------------------------------------------------

def openapi(_ctx, _p, _q, _b):
    import json
    return 200, json.loads(SPEC.read_text(encoding="utf-8"))


def docs_page(_ctx, _p, _q, _b):
    return 200, Html(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>mccontrol API</title>
<link rel="stylesheet" href="{SWAGGER}/swagger-ui.css"></head>
<body><div id="ui">The API viewer loads from {SWAGGER}; offline, read openapi.json beside this page.</div>
<script src="{SWAGGER}/swagger-ui-bundle.js"></script>
<script>SwaggerUIBundle({{url: "openapi.json", dom_id: "#ui"}});</script>
</body></html>
""")


# -- configuration and keys --------------------------------------------------------------

CONFIG_TYPES = {"broker": str, "port": int, "username": str, "password": str, "tls": bool,
                "ttl_secs": int, "wait_secs": (int, float), "beacon_url": str, "columns": list, "ui": str, "ui_settings": dict}


def get_config(ctx, _p, _q, _b):
    return 200, public_config(ctx.store)


def set_config(ctx, _p, _q, body):
    for key, value in body.items():
        kind = CONFIG_TYPES.get(key)
        if kind is None:
            raise bad(f"{key} is not a setting; settings are {', '.join(CONFIG_TYPES)}")
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise bad(f"{key} has the wrong type")
    if "columns" in body:
        body = dict(body, columns=directory.chosen(body["columns"]))
    if "ui_settings" in body:
        body = dict(body, ui_settings=merged_ui_settings(ctx.store.config().get("ui_settings", {}), body["ui_settings"]))
    ctx.store.save_config(**body)
    return 200, public_config(ctx.store)


def merged_ui_settings(stored, change):
    """Each interface's own preferences with `change` merged in, interface by interface and
    key by key: a null value removes its key, and an interface left with none goes."""
    out = {name: dict(keys) for name, keys in stored.items()}
    for name, keys in change.items():
        if not isinstance(keys, dict):
            raise bad(f"ui_settings.{name} has the wrong type: it is an object of settings")
        mine = out.setdefault(name, {})
        for key, value in keys.items():
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
        if not mine:
            del out[name]
    return out


def key_view(store):
    public = store.public_key() if store.has_key() else None
    return {"exists": store.has_key(), "public": public, "path": str(store.key_path),
            "registered": store.registration(),
            "provision": [f"set mqtt.cmd.pubkey {public}", "set mqtt.cmd on"] if public else []}


def get_key(ctx, _p, _q, _b):
    return 200, key_view(ctx.store)


def create_key(ctx, _p, _q, body):
    overwrite = bool(body.get("overwrite"))
    seed = _str(body, "seed")
    if ctx.store.has_key() and not overwrite:
        raise ApiError(409, "conflict", f"a key already exists at {ctx.store.key_path}; "
                                        f"replacing it would silence every node provisioned with it")
    try:
        if seed is not None:           # an empty seed is refused, never a new key
            ctx.store.import_key(seed, overwrite=overwrite)
        else:
            ctx.store.create_key(overwrite=overwrite)
    except ValueError as exc:
        raise bad(f"not a seed: {exc}") from None
    return 201, key_view(ctx.store)


# -- devices -----------------------------------------------------------------------------

def list_devices(ctx, _p, query, _b):
    sort = query.get("sort", "name")
    sorts = list(directory.SORTS) + ["version", "added"]
    if sort not in sorts:
        raise bad(f"sort is one of {', '.join(sorts)}")
    width = _int(query, "width", 100)
    columns = ctx.store.config().get("columns")
    tags = tags_view(ctx)
    rows = ctx.store.devices() if sort == "added" else directory.sorted_devices(ctx.store.devices(), sort)
    return 200, {"devices": [view(ctx, d, columns, width) for d in rows],
                 "sort": sort, "sorts": sorts, "columns": directory.chosen(columns),
                 "all_columns": list(directory.COLUMNS), "header": directory.header(width, columns),
                 "footer": tags["footer"], "tagged": tags["tagged"]}


def add_device(ctx, _p, _q, body):
    node, region = _str(body, "node"), _str(body, "region")
    if not node or not region:
        raise bad("a device needs node and region")
    try:
        entry = ctx.store.add_device(_str(body, "name", "") or "", node, region, _str(body, "notes", "") or "",
                                     key=_str(body, "key"))
    except _store.StoreError as exc:
        if "already" in str(exc):
            raise ApiError(409, "conflict", str(exc)) from None
        raise
    return 201, view(ctx, entry)


def adopt(ctx, _p, _q, body):
    node, region = _str(body, "node"), _str(body, "region")
    if not node or not region:
        raise bad("adopt needs the found entry's node and region")
    entry = actions.adopt(ctx.store, {"node": node, "region": region, "key": _str(body, "key")}, _str(body, "name", "") or "")
    return 201, view(ctx, entry)


def get_device(ctx, params, _q, _b):
    return 200, view(ctx, device_of(ctx, params["ref"]))


def edit_device(ctx, params, _q, body):
    d = device_of(ctx, params["ref"])
    changes = {}
    for key in ("name", "region", "notes"):
        if key in body:
            changes[key] = (_str(body, key) or "").strip()
    if "region" in changes:
        changes["region"] = changes["region"].upper()
        if not (2 <= len(changes["region"]) <= 8 and changes["region"].isalnum()):
            raise bad("a device needs its region: the IATA code its observers use, e.g. JKG")
    if changes.get("name") and any(o["name"].lower() == changes["name"].lower() and o["node"] != d["node"]
                                    for o in ctx.store.devices()):
        raise ApiError(409, "conflict", f"a device named {changes['name']!r} already exists")
    return 200, view(ctx, ctx.store.update_device(d["node"], **changes))


def forget(ctx, params, _q, _b):
    d = device_of(ctx, params["ref"])
    ctx.store.remove_device(d["node"])
    ctx.tags.discard(d["node"])
    return 200, {"forgot": d["node"]}


def history(ctx, params, query, _b):
    d = device_of(ctx, params["ref"])
    return 200, {"node": d["node"], "history": ctx.store.history(d["node"], limit=_int(query, "limit", 200))}


def broadcast_history(ctx, _p, query, _b):
    return 200, {"node": None, "history": ctx.store.history(None, limit=_int(query, "limit", 200))}


def reads(ctx, params, _q, _b):
    d = device_of(ctx, params["ref"])
    kept = d.get("reads") or {}
    return 200, {"node": d["node"], "reads": [{"command": c, "reply": r, "at": at, "age": directory.relative(at),
                                               "verified": kept[c].get("verified")}
                                              for c, r, at in ctx.store.reads(d)]}


def device_facts(ctx, params, _q, _b):
    d = device_of(ctx, params["ref"])
    reads = [(c, v["reply"], v["at"], v.get("verified")) for c, v in (d.get("reads") or {}).items()]
    known, raw = facts.derive(reads, ctx.store.history(d["node"], limit=None))
    return 200, {"node": d["node"],
                 "facts": [{"key": k, "label": facts.LABELS.get(k, k), "value": v["value"], "at": v["at"],
                            "age": directory.relative(v["at"]), "source": v["source"], "read": facts.reader(k),
                            "verified": v["verified"]}
                           for k, v in sorted(known.items(), key=lambda kv: facts.order(kv[0]))],
                 "raw": [{"command": c, "reply": v["reply"], "at": v["at"], "age": directory.relative(v["at"]),
                          "verified": v["verified"]}
                         for c, v in sorted(raw.items(), key=lambda kv: kv[1]["at"], reverse=True)]}


def device_commands(ctx, params, _q, _b):
    return 200, command_list(device_of(ctx, params["ref"]))


def table(_ctx, _p, _q, _b):
    return 200, command_list(None)


def check_command(ctx, _p, query, _b):
    command = " ".join((query.get("command") or "").split())
    if not command:
        raise bad("command is required")
    d = device_of(ctx, query["device"]) if query.get("device") else None
    return 200, {"command": command, "refusal": denylist.refusal(command), "caution": actions.caution(command),
                 "transmits": envelope.is_transmit(command), "is_read": commands.is_read(command),
                 "needs": firmware.needs(command), "node": d["node"] if d else None,
                 "firmware": firmware.entry_for(d.get("ver"))[0] if d else None,
                 "admitted": firmware.admits(d.get("ver"), command) if d else None}


def build_command(_ctx, _p, _q, body):
    text, values = _str(body, "command"), body.get("values")
    if not text or not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise bad("a build needs command, a table entry, and values, a list of strings")
    entry = next((c for c in commands.TABLE if c.text == " ".join(text.split())), None)
    if entry is None:
        raise not_found(f"no table entry {text!r}; GET /commands lists them")
    return 200, {"command": commands.build(entry, values)}


# -- tags, regions -----------------------------------------------------------------------

def _tag_targets(ctx, refs, region):
    nodes = {d["node"] for d in devices_of(ctx, refs or [])}
    if region:
        nodes |= {d["node"] for d in ctx.store.devices() if (d.get("region") or "").upper() == region.upper()}
    return nodes


def get_tags(ctx, _p, _q, _b):
    return 200, tags_view(ctx)


def set_tags(ctx, _p, _q, body):
    nodes = body.get("nodes")
    ctx.tags = {d["node"] for d in devices_of(ctx, nodes if nodes is not None else None)}
    return 200, tags_view(ctx)


def tag(ctx, _p, _q, body):
    ctx.tags |= _tag_targets(ctx, body.get("devices"), _str(body, "region"))
    return 200, tags_view(ctx)


def untag(ctx, _p, query, _b):
    refs = [r for r in (query.get("devices") or "").split(",") if r.strip()]
    if not refs and not query.get("region"):
        ctx.tags.clear()
    else:
        ctx.tags -= _tag_targets(ctx, refs, query.get("region"))
    return 200, tags_view(ctx)


def invert_tags(ctx, _p, _q, _b):
    ctx.tags = {d["node"] for d in ctx.store.devices()} - ctx.tags
    return 200, tags_view(ctx)


def regions(ctx, _p, _q, _b):
    return 200, {"regions": ctx.store.regions()}


def resolve_regions(ctx, _p, query, _b):
    if "text" not in query:
        raise bad("text is required: JKG, several with commas, or *")
    return 200, {"regions": actions.regions_asked(query["text"], ctx.store)}


# -- sets and shortcuts ------------------------------------------------------------------

def get_sets(ctx, _p, _q, _b):
    return 200, {"sets": ctx.store.sets(), "max_steps": _store.SET_STEPS}


def filled_set(ctx, params, query, _b):
    name = params["name"].strip().lower()
    steps = ctx.store.sets().get(name)
    if not steps:
        raise not_found(f"no set named {name}")
    try:
        return 200, {"name": name, "steps": actions.fill(steps, query.get("arg") or "")}
    except envelope.EnvelopeError as exc:
        raise bad(str(exc)) from None


def save_set(ctx, params, _q, body):
    steps = body.get("steps")
    if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
        raise bad("steps is a list of commands")
    if len(steps) > _store.SET_STEPS:
        raise bad(f"a set has at most {_store.SET_STEPS} steps")
    name = params["name"].strip().lower()
    return 200, {"name": name, "steps": ctx.store.save_set(name, steps)}


def remove_set(ctx, params, _q, _b):
    name = params["name"].strip().lower()
    if name not in ctx.store.sets():
        raise not_found(f"no set named {name}")
    ctx.store.remove_set(name)
    return 200, {"removed": name}


def get_shortcuts(ctx, _p, _q, _b):
    return 200, {"shortcuts": ctx.store.shortcuts()}


def set_shortcuts(ctx, _p, _q, body):
    slots = body.get("shortcuts")
    if not isinstance(slots, list) or not all(isinstance(s, str) for s in slots) or len(slots) > _store.SHORTCUT_SLOTS:
        raise bad(f"shortcuts is a list of at most {_store.SHORTCUT_SLOTS} commands")
    ctx.store.save_config(shortcuts=[s.strip() for s in slots])
    return 200, {"shortcuts": ctx.store.shortcuts()}


# -- help --------------------------------------------------------------------------------

def _ui(query):
    """The `ui` a help request names, None for the shared texts; 400 for one that is not there."""
    ui = query.get("ui") or None
    if ui is not None and ui not in helptext.interfaces():
        raise bad(f"no interface {ui!r}; there are {', '.join(helptext.interfaces())}")
    return ui


def help_index(_ctx, _p, query, _b):
    ui = _ui(query)
    return 200, {"topics": [{"name": n, "title": helptext.load(n, ui).title} for n in helptext.topics(ui)],
                 "context": helptext.context(ui)}


def help_topic(_ctx, params, query, _b):
    ui = _ui(query)
    topic = helptext.load(params["topic"], ui)
    if topic is None:
        raise not_found(f"no help topic {params['topic']!r}")
    return 200, {"name": topic.name, "title": topic.title, "lines": topic.lines,
                 "links": [{"line": l, "start": s, "end": e, "target": t} for l, s, e, t in topic.links],
                 "text": helptext.as_text(topic.name, ui)}


# Literal paths before parameterised ones: the first match wins.
TABLE = [
    ("GET", "/openapi.json", openapi),
    ("GET", "/docs", docs_page),
    ("GET", "/config", get_config),
    ("PATCH", "/config", set_config),
    ("GET", "/key", get_key),
    ("POST", "/key", create_key),
    ("GET", "/devices", list_devices),
    ("POST", "/devices", add_device),
    ("POST", "/devices/adopt", adopt),
    ("GET", "/devices/{ref}", get_device),
    ("PATCH", "/devices/{ref}", edit_device),
    ("DELETE", "/devices/{ref}", forget),
    ("GET", "/devices/{ref}/history", history),
    ("GET", "/devices/{ref}/reads", reads),
    ("GET", "/devices/{ref}/facts", device_facts),
    ("GET", "/devices/{ref}/commands", device_commands),
    ("GET", "/history", broadcast_history),
    ("GET", "/commands", table),
    ("GET", "/commands/check", check_command),
    ("POST", "/commands/build", build_command),
    ("GET", "/tags", get_tags),
    ("PUT", "/tags", set_tags),
    ("POST", "/tags", tag),
    ("DELETE", "/tags", untag),
    ("POST", "/tags/invert", invert_tags),
    ("GET", "/regions", regions),
    ("GET", "/regions/resolve", resolve_regions),
    ("GET", "/sets", get_sets),
    ("GET", "/sets/{name}", filled_set),
    ("PUT", "/sets/{name}", save_set),
    ("DELETE", "/sets/{name}", remove_set),
    ("GET", "/shortcuts", get_shortcuts),
    ("PUT", "/shortcuts", set_shortcuts),
    ("GET", "/help", help_index),
    ("GET", "/help/{topic}", help_topic),
]
