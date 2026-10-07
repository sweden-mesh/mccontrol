"""mccontrol: the command line, and the door to the screens.

    mccontrol                      the green screen
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
    for name in ("broker", "username", "password"):
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
    sub.add_parser("test", help="test the control broker: connect, sign in, subscribe, publish")
    d = sub.add_parser("discover", help="find the nodes in a region that accept your key")
    d.add_argument("region", help="the IATA code, e.g. JKG")
    d.add_argument("--add", action="store_true", help="remember every node not yet in your list")
    d.add_argument("--wait-secs", type=float, default=None, help="how long to listen for answers")
    h = sub.add_parser("history", help="what was sent to a device and what came back")
    h.add_argument("device")
    h.add_argument("--limit", type=int, default=30)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = _store.Store()
    try:
        if args.command is None:
            from . import tui
            return tui.run(store)
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
    if any(v is not None for v in changes.values()):
        store.save_config(**changes)
    for key, value in store.config().items():
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
        print(f"{d['name']:18} {d['node'][:8]}  {d.get('region', '?'):4}  last sent {d['last_sent'] or '-':20} "
              f"{d['last_result'] or '':12} {d['notes']}")
    return 0


def _remove(store, args):
    device = _device(store, args.device)
    store.remove_device(device["node"])
    print(f"forgot {device['name']}")
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
        where = f"EVERY node in {region}" if args.all else device["name"]
        print(f"'{command}' {why}\nSend it to {where}? [y/N] ", end="", flush=True)
        if input().strip().lower() != "y":
            print("not sent")
            return 2
    _topic, _payload, _answers, lines = actions.send(store, command, device=device, region=region,
                                                     dry_run=args.dry_run)
    print("\n".join(lines))
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


def _history(store, args):
    device = _device(store, args.device)
    for line in store.history(device["node"], limit=args.limit):
        out = line["reply"] if line["code"] == 0 else (f"refused {line['code_name']}" if line["code"] is not None else "no answer")
        print(f"{line['at']}  {line['command']:34} {out}")
    return 0


COMMANDS = {"keygen": _keygen, "key": _key, "config": _config, "add": _add, "devices": _devices,
            "remove": _remove, "send": _send, "regions": _regions, "history": _history,
            "discover": _discover, "test": _test}

if __name__ == "__main__":
    sys.exit(main())
