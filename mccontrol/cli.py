"""mccontrol: the command line, and the door to the screens.

    mccontrol                      the screens
    mccontrol keygen               make a signing key and print what to provision
    mccontrol key                  print the public key
    mccontrol config [--broker host] [--username u] [--password p]
    mccontrol add <name> <8-hex node> <region> [notes...]
    mccontrol devices
    mccontrol send <device> <command...>      (--dry-run to only sign)
    mccontrol send <region> <command...> --all   every node in that region
    mccontrol regions <device>
    mccontrol history <device>
    mccontrol discover <region> [--add]   the nodes there that accept your key
    mccontrol test                 connect, sign in, subscribe and publish on the control broker
    mccontrol --api <url> ...      any of these through a running API instead of one of its own

Every subcommand is a client of the API (`apiclient`): it starts one in-process for the
command unless --api names one already running.
"""

import argparse
import contextlib
import json
import sys

from .apiclient import ApiError, Client
from .api.server import serve, stop


def build_parser():
    p = argparse.ArgumentParser(prog="mccontrol", description=__doc__.split("\n")[0])
    p.add_argument("--ui", metavar="NAME", help="the interface to start; the last one asked for when not given, else Monolith")
    p.add_argument("--api", metavar="URL", help="use a running mccontrol API instead of starting one")
    sub = p.add_subparsers(dest="command")
    sub.add_parser("keygen", help="create the signing key").add_argument(
        "--force", action="store_true", help="replace an existing key (silences provisioned nodes)")
    sub.add_parser("key", help="print the public key to provision with 'set mqtt.cmd.pubkey'")
    c = sub.add_parser("config", help="show or change the control broker")
    for name in ("broker", "username", "password", "beacon-url"):
        c.add_argument(f"--{name}")
    c.add_argument("--port", type=int)
    c.add_argument("--tls", choices=("on", "off"), help="TLS to the broker (the command plane is TLS on 8883)")
    c.add_argument("--wait-secs", type=int)
    c.add_argument("--columns", help="the directory's optional columns, comma-separated: reg,sent,batt,seen,ver,result")
    a = sub.add_parser("add", help="remember a device")
    a.add_argument("name")
    a.add_argument("node", help="the first 8 hex characters of its public key (a whole key is fine)")
    a.add_argument("region", help="the IATA code its observers use, e.g. JKG")
    a.add_argument("notes", nargs="*")
    sub.add_parser("devices", help="list your devices")
    r = sub.add_parser("remove", help="forget a device")
    r.add_argument("device")
    s = sub.add_parser("send", help="sign and send a command")
    s.add_argument("device", help="a device name, or a region code with --all")
    s.add_argument("words", nargs="+", help="the command, e.g. region put se-jkp '*'")
    s.add_argument("--all", action="store_true", help="every node in that region at once")
    s.add_argument("--dry-run", action="store_true", help="sign and print, send nothing")
    s.add_argument("--yes", action="store_true", help="do not ask about a command that takes something away")
    g = sub.add_parser("regions", help="read a device's region map")
    g.add_argument("device")
    al = sub.add_parser("alt", help="send shortcut n (Alt+n in the interface) to a device")
    al.add_argument("n", type=int)
    al.add_argument("device", help="a device name, or a region code with --all")
    al.add_argument("--all", action="store_true", help="every node in that region at once")
    al.add_argument("--dry-run", action="store_true", help="sign and print, send nothing")
    al.add_argument("--yes", action="store_true", help="do not ask about a command that takes something away")
    rn = sub.add_parser("run", help="send a command set, step by step, stopping at the first refusal")
    rn.add_argument("set", help="the set's name; 'mccontrol sets' lists them")
    rn.add_argument("device", help="a device name, or a region code with --all")
    rn.add_argument("arg", nargs="?", default="", help="the value for {arg} in the set's steps")
    rn.add_argument("--all", action="store_true", help="every node in that region at once")
    rn.add_argument("--dry-run", action="store_true", help="sign and print, send nothing")
    rn.add_argument("--yes", action="store_true", help="do not ask about a step that takes something away")
    st = sub.add_parser("sets", help="list the command sets, define one, or remove one")
    st.add_argument("--set", nargs="+", metavar="X", help="NAME then the steps in order: --set add-scope 'region put {arg} *' ...")
    st.add_argument("--remove", metavar="NAME")
    sts = sub.add_parser("stats", help="a device's statistics from Beacon: the latest status and the telemetry history")
    sts.add_argument("device")
    sts.add_argument("--range", default="24h", choices=("24h", "48h", "168h"))
    sts.add_argument("--interval", default="6h", choices=("1h", "6h", "24h"))
    sts.add_argument("--live", action="store_true", help="send 'get stats' first (one command slot) and show it above Beacon's")
    fl = sub.add_parser("fleet", help="battery and last-seen for every device, refreshed from Beacon")
    fl.add_argument("device", nargs="*", help="only these devices (default: all)")
    rf = sub.add_parser("refresh", help="read name and version from devices over the control plane")
    rf.add_argument("device", nargs="*", help="devices (default: all)")
    cm = sub.add_parser("commands", help="the commands the tool offers, by group, with their parameter kind")
    cm.add_argument("device", nargs="?", help="mark what this device's firmware does not admit")
    rd = sub.add_parser("reads", help="what a device last answered to each read, newest first")
    rd.add_argument("device")
    sc = sub.add_parser("shortcuts", help="list the Alt+1..9 shortcuts, or set one")
    sc.add_argument("--set", nargs=2, metavar=("N", "COMMAND"), help="put COMMAND on Alt+N ('' frees it)")
    sub.add_parser("test", help="test the control broker: connect, sign in, subscribe, publish")
    sub.add_parser("register", help="register your signing key with the broker, once; the broker then checks commands against it")
    d = sub.add_parser("discover", help="find the nodes in a region that accept your key")
    d.add_argument("region", help="the IATA code, e.g. JKG; several with commas, or '*' for every region you have")
    d.add_argument("--add", action="store_true", help="remember every node not yet in your list")
    d.add_argument("--wait-secs", type=float, default=None, help="how long to listen for answers")
    hp = sub.add_parser("help", help="the help topics as text")
    hp.add_argument("topic", nargs="?", default="index")
    h = sub.add_parser("history", help="what was sent to a device and what came back")
    h.add_argument("device")
    h.add_argument("--limit", type=int, default=30)
    return p


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.command is None:
            from . import launch
            return launch.run(None, args.ui, args.api)
        with _client(args) as api:
            return COMMANDS[args.command](api, args)
    except ApiError as exc:
        print(f"mccontrol: {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:                   # the server, if one was started, is stopped by now
        print("interrupted", file=sys.stderr)
        return 130


@contextlib.contextmanager
def _client(args):
    """A client of the API at --api, or of one started for this command and stopped after it."""
    if args.api:
        yield Client(args.api)
        return
    server, url = serve()
    try:
        yield Client(url)
    finally:
        stop(server)


def _job(api, started, quiet=False):
    """Wait for a job, its progress on stderr (none when `quiet`); Ctrl+C cancels it. The
    finished record, or None when cancelled; a failed job is its error."""
    shown = [None]

    def show(job):
        text = job["progress"]["text"]
        if text != shown[0] and not quiet:
            print(text, file=sys.stderr, flush=True)
            shown[0] = text

    try:
        job = api.wait_job(started["id"], on_progress=show)
    except KeyboardInterrupt:
        try:
            api.cancel_job(started["id"])
        except ApiError as exc:
            if exc.status != 409:               # it finished meanwhile: nothing left to cancel
                raise
        print("cancelled", file=sys.stderr)
        return None
    if job["status"] == "failed":
        raise ApiError(job["error"]["code"], job["error"]["message"], 0)
    return job


def _keygen(api, args):
    key = api.create_key(overwrite=args.force)
    print(f"key created at {key['path']} (mode 600; keep it, never share it)")
    print("provision each repeater over serial with:")
    for line in key["provision"]:
        print(f"  {line}")
    return 0


def _key(api, _args):
    key = api.key()
    if not key["exists"]:
        raise ApiError("store", "no signing key yet: create one first", 0)
    print(key["public"])
    reg = key["registered"]
    if reg:
        print(f"registered with {reg['broker']} on {reg['at'][:10]}")
    return 0


def _register(api, _args):
    job = _job(api, api.start_register())
    if job is None:
        return 130
    ok, detail = job["result"]["ok"], job["result"]["detail"]
    print(f"{'registered' if ok else 'not registered'}: {detail}")
    return 0 if ok else 1


def _config(api, args):
    changes = {k: getattr(args, k) for k in ("broker", "username", "password", "port")}
    changes["tls"] = {"on": True, "off": False}.get(args.tls)
    changes["wait_secs"] = args.wait_secs
    changes["beacon_url"] = args.beacon_url
    changes["columns"] = args.columns.split(",") if args.columns else None
    changes = {k: v for k, v in changes.items() if v is not None}
    config = api.set_config(**changes) if changes else api.config()
    for key, value in config.items():
        if key == "password_set":
            continue                              # shown after the username, as the password
        if key == "ui_settings":                  # each interface's own, one line a key
            for name, keys in value.items():
                for k, v in keys.items():
                    print(f"ui.{name}.{k} = {v if isinstance(v, str) else json.dumps(v)}")
            continue
        if key == "columns":
            value = ",".join(value)
        print(f"{key:12} {value}")
        if key == "username":
            print(f"{'password':12} {'(set)' if config.get('password_set') else '(not set)'}")
    return 0


def _add(api, args):
    entry = api.add_device(args.node, args.region, name=args.name, notes=" ".join(args.notes))
    print(f"added {entry['name']} ({entry['node'][:8]}) in {entry['region']}")
    return 0


def _devices(api, _args):
    rows = api.devices(sort="added")["devices"]
    if not rows:
        print("no devices yet: mccontrol add <name> <node-hex>")
        return 0
    for d in rows:
        print(f"{(d['name'] or '(unnamed)'):18} {d['node'][:8]}  {d.get('region', '?'):4}  last sent {d['last_sent'] or '-':20} "
              f"{d['last_result'] or '':12} {d['notes']}")
    return 0


def _remove(api, args):
    device = _device(api, args.device)
    api.forget(device["node"])
    print(f"forgot {device['name'] or device['node']}")
    return 0


def _device(api, ref):
    try:
        return api.device(ref)
    except ApiError as exc:
        if exc.status != 404:
            raise
        raise ApiError(exc.code, f"no device matches {ref!r}; see 'mccontrol devices'", exc.status) from None


def _send(api, args, command=None):
    command = command or " ".join(args.words)
    device = None if args.all else _device(api, args.device)
    region = args.device.upper() if args.all else None
    why = api.check_command(command)["caution"]
    if why and not args.dry_run and not args.yes:
        where = f"EVERY node in {region}" if args.all else (device["name"] or device["node"])
        print(f"'{command}' {why}\nSend it to {where}? [y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("not sent")
            return 2
    job = _job(api, api.start_send(command, devices=None if args.all else [device["node"]], region=region,
                                   dry_run=args.dry_run, confirm=True), quiet=args.dry_run)
    if job is None:
        return 130
    print("\n".join(job["result"]["lines"]))
    return 0


def _alt(api, args):
    slots = api.shortcuts()["shortcuts"]
    if not 1 <= args.n <= len(slots) or not slots[args.n - 1]:
        raise ApiError("store", f"shortcut {args.n} is not set; see 'mccontrol shortcuts'", 0)
    return _send(api, args, command=slots[args.n - 1])


def _run(api, args):
    steps = api.sets()["sets"].get(args.set.lower())
    if not steps:
        raise ApiError("not_found", f"no set named {args.set}; 'mccontrol sets' lists them", 0)
    device = None if args.all else _device(api, args.device)
    region = args.device.upper() if args.all else None
    arg = args.arg.strip()
    if not args.dry_run and not args.yes and (arg or not any("{arg}" in s for s in steps)):
        filled = [s.replace("{arg}", arg) for s in steps]
        cautions = [f"'{s}' {why}" for s, why in ((s, api.check_command(s)["caution"]) for s in filled) if why]
        if cautions:
            where = f"EVERY node in {region}" if args.all else (device["name"] or device["node"])
            print("\n".join(cautions) + f"\nRun the set on {where}? [y/N] ", end="", flush=True)
            if input().strip().lower() != "y":
                print("not sent")
                return 2
    job = _job(api, api.start_run_set(args.set.lower(), arg=args.arg, devices=None if args.all else [device["node"]],
                                      region=region, dry_run=args.dry_run, confirm=True), quiet=args.dry_run)
    if job is None:
        return 130
    lines = job["result"]["lines"]
    print("\n".join(lines if args.all else lines[1:]))       # one device: no "== name" heading
    return 0 if job["result"]["runs"][0]["stopped_at"] is None else 3


def _sets(api, args):
    if args.set:
        if len(args.set) < 2:
            raise ApiError("store", "--set takes the name and then at least one step", 0)
        api.save_set(args.set[0], args.set[1:])
    if args.remove:
        api.remove_set(args.remove.lower())
    for name, steps in sorted(api.sets()["sets"].items()):
        print(name)
        for i, s in enumerate(steps, 1):
            print(f"  {i}. {s}")
    return 0


def _stats(api, args):
    device = _device(api, args.device)
    try:
        job = _job(api, api.start_statistics(device["node"], range=args.range, interval=args.interval, live=args.live))
    except ApiError as exc:
        if exc.code != "beacon":
            raise
        print(f"no statistics: {exc.message}", file=sys.stderr)
        return 1
    if job is None:
        return 130
    print("\n".join(job["result"]["lines"]))
    return 0


def _fleet(api, args):
    devices = [_device(api, ref) for ref in args.device] if args.device else api.devices(sort="added")["devices"]
    updated = failed = 0
    if devices:
        job = _job(api, api.start_refresh_fleet(devices=[d["node"] for d in devices]))
        if job is None:
            return 130
        updated, failed = job["result"]["updated"], job["result"]["failed"]
    listing = api.devices(sort="name", width=100)
    print(listing["header"])
    for d in listing["devices"]:
        print(d["row"].ljust(99))
    print(f"refreshed {updated} of {len(devices)}" + (f"; {failed} without statistics" if failed else ""))
    return 0 if not failed else 1


def _refresh(api, args):
    devices = [_device(api, ref) for ref in args.device] if args.device else api.devices(sort="added")["devices"]
    named = versioned = 0
    lines = []
    if devices:
        job = _job(api, api.start_refresh_names(devices=[d["node"] for d in devices]))
        if job is None:
            return 130
        named, versioned, lines = job["result"]["named"], job["result"]["versioned"], job["result"]["lines"]
    print("\n".join(lines))
    print(f"{versioned} of {len(devices)} gave a version; {named} named")
    return 0 if versioned == len(devices) else 1


def _reads(api, args):
    rows = api.reads(_device(api, args.device)["node"])["reads"]
    if not rows:
        print("nothing read yet")
        return 0
    for r in rows:
        print(f"{r['age']:>5}  {r['command']:28} {_signed(r['reply'], r['verified'])}")
    return 0


def _signed(reply, verified):
    """A reply as the node's signature leaves it: a forged one says so, an unsigned one too."""
    if verified is False:
        return f"BAD SIGNATURE {reply}"
    return f"{reply} (unsigned)" if verified is None else reply


def _commands(api, args):
    if args.device:
        listing = api.device_commands(_device(api, args.device)["node"])
        version = listing["version"]
        print(f"{args.device}: firmware {listing['firmware']} ({version or 'version unknown; mccontrol refresh reads it'})")
    else:
        listing = api.commands()
    for group in listing["groups"]:
        print(f"{group['group']}:")
        for c in group["commands"]:
            if c.get("hidden"):
                continue                    # the image has no such feature
            how = {"none": "", "choice": " <" + "|".join(x or "-" for x in c["choices"]) + ">",
                   "multi": " <" + " ".join(c["choices"]) + " ...>",
                   "text": " <" + (c["examples"][0] if c["examples"] else "text") + ">"}[c["kind"]]
            mark = f"   -- needs {c['needs'] or 'a build that admits it'}" if c["admitted"] is False else ""
            print(f"  {c['text']}{how}" + ("   (kept)" if c["read"] else "") + mark)
    return 0


def _shortcuts(api, args):
    slots = api.shortcuts()["shortcuts"]
    if args.set:
        n, command = args.set
        try:
            n = int(n)
        except ValueError:
            raise ApiError("store", "N is 1 to 9", 0) from None
        if not 1 <= n <= len(slots):
            raise ApiError("store", f"a shortcut is Alt+1 to Alt+{len(slots)}", 0)
        slots[n - 1] = command
        slots = api.set_shortcuts(slots)["shortcuts"]
    for n, command in enumerate(slots, 1):
        print(f"Alt+{n}  {command or '-'}")
    return 0


def _regions(api, args):
    args.all, args.dry_run, args.yes = False, False, True
    return _send(api, args, command="region")


def _test(api, _args):
    job = _job(api, api.start_test())
    if job is None:
        return 130
    result = job["result"]
    for s in result["steps"]:
        print(f"{'ok  ' if s['ok'] else 'FAIL'} {s['step']:28} {s['detail']}")
    if result["key"]:
        print(f"ok   signing key                  public {result['key'][:16]}...")
        reg = result["registered"]
        print(f"{'ok  ' if reg else 'note'} registered with broker       "
              + (f"{reg['broker']} on {reg['at'][:10]}" if reg else "not yet: run 'mccontrol register'"))
    else:
        print("FAIL signing key                  none: run 'mccontrol keygen'")
    return 0 if result["ok"] else 1


def _discover(api, args):
    job = _job(api, api.start_discover(args.region, wait_secs=args.wait_secs, add=args.add))
    if job is None:
        return 130
    found = job["result"]["found"]
    if not found:
        print(f"no node in {args.region.upper()} answered a command signed with your key")
        return 0
    for e in found:
        mark = f"known as {e['known_as']}" if e["known_as"] else ("added" if args.add else "")
        print(f"{e['node'][:8]}  {e['region']:4}  {e['outcome'][:40]:40} {mark}")
    return 0


def _help(api, args):
    try:
        print(api.help_topic(args.topic)["text"])
    except ApiError as exc:
        if exc.status != 404:
            raise
        topics = [t["name"] for t in api.help_index()["topics"]]
        print(f"no help topic {args.topic!r}; topics: {', '.join(topics)}", file=sys.stderr)
        return 1
    return 0


def _history(api, args):
    for line in api.history(_device(api, args.device)["node"], limit=args.limit)["history"]:
        out = (_signed(line["reply"], line["verified"]) if line["code"] == 0 else
               f"refused {line['code_name']}" if line["code"] is not None else "no answer")
        print(f"{line['at']}  {line['command']:34} {out}")
    return 0


COMMANDS = {"keygen": _keygen, "key": _key, "config": _config, "add": _add, "devices": _devices,
            "remove": _remove, "send": _send, "regions": _regions, "history": _history,
            "discover": _discover, "test": _test, "help": _help, "alt": _alt, "shortcuts": _shortcuts,
            "run": _run, "sets": _sets, "stats": _stats, "register": _register, "fleet": _fleet, "refresh": _refresh, "commands": _commands, "reads": _reads}

if __name__ == "__main__":
    sys.exit(main())
