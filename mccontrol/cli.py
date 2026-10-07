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
"""

import argparse
import sys

from . import actions, envelope, store as _store, transport


def build_parser():
    p = argparse.ArgumentParser(prog="mccontrol", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command")
    sub.add_parser("keygen", help="create the signing key").add_argument(
        "--force", action="store_true", help="replace an existing key (silences provisioned nodes)")
    sub.add_parser("key", help="print the public key to provision with 'set mqtt.cmd.pubkey'")
    c = sub.add_parser("config", help="show or change the control broker")
    for name in ("broker", "username", "password", "beacon-url"):
        c.add_argument(f"--{name}")
    c.add_argument("--port", type=int)
    c.add_argument("--wait-secs", type=int)
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
    sc = sub.add_parser("shortcuts", help="list the Alt+1..9 shortcuts, or set one")
    sc.add_argument("--set", nargs=2, metavar=("N", "COMMAND"), help="put COMMAND on Alt+N ('' frees it)")
    sub.add_parser("test", help="test the control broker: connect, sign in, subscribe, publish")
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
    args = build_parser().parse_args(argv)
    store = _store.Store()
    try:
        if args.command is None:
            from . import app
            return app.run(store)
        return COMMANDS[args.command](store, args)
    except (envelope.EnvelopeError, transport.BrokerError, _store.StoreError) as exc:
        print(f"mccontrol: {exc}", file=sys.stderr)
        return 1


def _keygen(store, args):
    public = store.create_key(overwrite=args.force)
    print(f"key created at {store.key_path} (mode 600; keep it, never share it)")
    print("provision each repeater over serial with:")
    print(f"  set mqtt.cmd.pubkey {public}")
    print("  set mqtt.cmd on")
    return 0


def _key(store, _args):
    print(store.public_key())
    return 0


def _config(store, args):
    changes = {k: getattr(args, k) for k in ("broker", "username", "password", "port")}
    changes["wait_secs"] = args.wait_secs
    changes["beacon_url"] = args.beacon_url
    if any(v is not None for v in changes.values()):
        store.save_config(**changes)
    for key, value in store.config().items():
        if key in ("shortcuts", "sets"):
            continue                              # their own commands list them
        print(f"{key:12} {'(set)' if key == 'password' else value}")
    return 0


def _add(store, args):
    entry = store.add_device(args.name, args.node, args.region, " ".join(args.notes))
    print(f"added {entry['name']} ({entry['node'][:8]}) in {entry['region']}")
    return 0


def _devices(store, _args):
    rows = store.devices()
    if not rows:
        print("no devices yet: mccontrol add <name> <node-hex>")
        return 0
    for d in rows:
        print(f"{(d['name'] or '(unnamed)'):18} {d['node'][:8]}  {d.get('region', '?'):4}  last sent {d['last_sent'] or '-':20} "
              f"{d['last_result'] or '':12} {d['notes']}")
    return 0


def _remove(store, args):
    device = _device(store, args.device)
    store.remove_device(device["node"])
    print(f"forgot {device['name'] or device['node']}")
    return 0


def _device(store, ref):
    device = store.device(ref)
    if device is None:
        raise _store.StoreError(f"no device matches {ref!r}; see 'mccontrol devices'")
    return device


def _send(store, args, command=None):
    command = command or " ".join(args.words)
    device = None if args.all else _device(store, args.device)
    region = args.device.upper() if args.all else None
    why = actions.caution(command)
    if why and not args.dry_run and not args.yes:
        where = f"EVERY node in {region}" if args.all else (device["name"] or device["node"])
        print(f"'{command}' {why}\nSend it to {where}? [y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("not sent")
            return 2
    _topic, _payload, _answers, lines = actions.send(store, command, device=device, region=region,
                                                     dry_run=args.dry_run)
    print("\n".join(lines))
    return 0


def _alt(store, args):
    slots = store.shortcuts()
    if not 1 <= args.n <= len(slots) or not slots[args.n - 1]:
        raise _store.StoreError(f"shortcut {args.n} is not set; see 'mccontrol shortcuts'")
    return _send(store, args, command=slots[args.n - 1])


def _run(store, args):
    steps = store.sets().get(args.set.lower())
    if not steps:
        raise _store.StoreError(f"no set named {args.set}; 'mccontrol sets' lists them")
    steps = actions.fill(steps, args.arg)
    device = None if args.all else _device(store, args.device)
    region = args.device.upper() if args.all else None
    cautions = [f"'{s}' {actions.caution(s)}" for s in steps if actions.caution(s)]
    if cautions and not args.dry_run and not args.yes:
        where = f"EVERY node in {region}" if args.all else (device["name"] or device["node"])
        print("\n".join(cautions) + f"\nRun the set on {where}? [y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("not sent")
            return 2
    lines, stopped = actions.run_set(store, steps, device=device, region=region, dry_run=args.dry_run)
    print("\n".join(lines))
    return 0 if stopped is None else 3


def _sets(store, args):
    if args.set:
        if len(args.set) < 2:
            raise _store.StoreError("--set takes the name and then at least one step")
        store.save_set(args.set[0], args.set[1:])
    if args.remove:
        store.remove_set(args.remove.lower())
    for name, steps in sorted(store.sets().items()):
        print(name)
        for i, s in enumerate(steps, 1):
            print(f"  {i}. {s}")
    return 0


def _shortcuts(store, args):
    if args.set:
        n, command = args.set
        try:
            store.set_shortcut(int(n), command)
        except ValueError:
            raise _store.StoreError("N is 1 to 9") from None
    for n, command in enumerate(store.shortcuts(), 1):
        print(f"Alt+{n}  {command or '-'}")
    return 0


def _regions(store, args):
    args.all, args.dry_run, args.yes = False, False, True
    return _send(store, args, command="region")


def _test(store, _args):
    steps = transport.test_connection(store.config())
    for step, ok, detail in steps:
        print(f"{'ok  ' if ok else 'FAIL'} {step:28} {detail}")
    if store.has_key():
        print(f"ok   signing key                  public {store.public_key()[:16]}...")
    else:
        print("FAIL signing key                  none: run 'mccontrol keygen'")
    return 0 if all(ok for _s, ok, _d in steps) else 1


def _discover(store, args):
    found = actions.discover(store, args.region, wait_secs=args.wait_secs)
    if not found:
        print(f"no node in {args.region.upper()} answered a command signed with your key")
        return 0
    for e in found:
        mark = f"known as {e['known_as']}" if e["known_as"] else ("added" if args.add else "")
        if args.add and not e["known_as"]:
            actions.adopt(store, e)
        print(f"{e['node'][:8]}  {e['region']:4}  {e['outcome'][:40]:40} {mark}")
    return 0


def _help(_store, args):
    from . import helptext
    text = helptext.as_text(args.topic)
    if text is None:
        print(f"no help topic {args.topic!r}; topics: {', '.join(helptext.topics())}", file=sys.stderr)
        return 1
    print(text)
    return 0


def _history(store, args):
    device = _device(store, args.device)
    for line in store.history(device["node"], limit=args.limit):
        out = line["reply"] if line["code"] == 0 else (f"refused {line['code_name']}" if line["code"] is not None else "no answer")
        print(f"{line['at']}  {line['command']:34} {out}")
    return 0


COMMANDS = {"keygen": _keygen, "key": _key, "config": _config, "add": _add, "devices": _devices,
            "remove": _remove, "send": _send, "regions": _regions, "history": _history,
            "discover": _discover, "test": _test, "help": _help, "alt": _alt, "shortcuts": _shortcuts,
            "run": _run, "sets": _sets}

if __name__ == "__main__":
    sys.exit(main())
