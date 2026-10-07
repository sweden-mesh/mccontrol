# mccontrol

For the people who run MeshCore Observer repeaters: sign a command with a key kept on your
machine, send it to one repeater, to the ones you tagged, or to every repeater in a region,
see what came back, and keep a history per device. One screen, every verb on a function key.

```
 Devices  Send  Keys  Options  Help
╔══════════════ Devices ═══════════════╗╔═══════════ History: Gisebo ═══════════╗
║ Name            Node     Reg  Sent   ║║ 10-07 11:00 region        > * F       ║
║▸Gisebo          4AC61A7F JKG  2 h  ok║║ 10-06 21:14 get autoreply > on ...    ║
║▸(unnamed)       65C1D12C JKG  -      ║║                                       ║
║ Far             FD700369 GOT  1 d    ║║                                       ║
╚═ 2 of 3 tagged ══════════════════════╝╚═══════════════════════════════════════╝
 2 of 2 answered.
 1Help  2Send  3Hist  4Edit  5Disco  6Regns  7All  8Del  9Menu  10Quit
```

The colours are Turbo Pascal's: a black menu bar and key bar with the hot letters and key
numbers in red, a blue work area, grey dialogs, green for the open menu. A terminal without
colour gets bold and reverse video instead.

The left panel is your device directory; the right panel follows the cursor (history), or
shows a send's results, or what a discovery found. Ins tags rows, `*` inverts, `+`/`-` tag a
region. Dialogs take the one or two values a verb needs; Esc always cancels.

## Install

```
pip install .
mccontrol keygen                                  # once; prints the two serial commands
mccontrol config --username <user> --password <pass>   # the control broker's credentials
mccontrol test                                    # connect, sign in, subscribe, publish, receive
mccontrol discover JKG --add                      # every node in JKG that accepts your key
mccontrol                                         # the screens
```

Python 3.10 or newer; `pycryptodome` (Ed25519) and `paho-mqtt`; `curses` from the standard
library. 80x24 is the minimum; wider terminals get wider panels.

## Keys

| key | does |
|---|---|
| F1 | help for where you are; Shift+F1 the contents; links are followed with Enter, Backspace or Alt+F1 goes back |
| F2 | send a command to the tagged devices, or the cursor device; Up recalls earlier commands |
| F3 / Enter | the cursor device's history as a window; on the right panel, Enter opens a reply |
| F4 | edit the device: name, region, notes |
| F5 | discover: the repeaters in a region that accept your key (`JKG, GOT` for several, `*` for every region you have); on the found list, F5 adds the tagged and F7 adds all |
| F6 | read the cursor device's region map |
| F7 | send a command to every node in a region |
| F8 | forget the cursor device |
| F9 / F10 | the menu / quit |
| Alt+1..9 | a command per key to the tagged or cursor device, no dialog: `region`, `ver`, `get name`, `get autoreply`, `get radio`, `get repeat` as shipped; Options > Shortcuts edits them |
| Ins, Space, `*`, `+`, `-` | tag a row, invert, tag or untag a region |
| Tab | switch panels |
| `s` | the cursor device's statistics from Beacon (battery, uptime, noise floor, airtime, packets, errors, with the sample's age, and 24 h of history); View menu for 48 h and 7 d |
| mouse | optional: click a row, a key-bar label or a menu title |

Commands that take something away (`region denyf *`, `region remove`, `set repeat off`, ...)
say what they do and ask first; a command that transmits is refused as a broadcast, as the
firmware refuses it.

## What it does

- **Keys.** An Ed25519 seed under `~/.config/mccontrol/key.seed`, mode 600, never shown and
  never leaving the machine inside anything but a signature. The Keys menu shows the two serial
  commands that provision a repeater, on one line, and refuses to replace a key by accident.
- **Devices.** Name, node id (the first eight hex characters of the public key, as the firmware
  names it on its topics), region (the IATA code its observers use; a person may run repeaters
  in several), notes, last sent, last result.
- **Discovery.** One harmless read signed with your key, broadcast to a region on the control
  broker. Only a node that verified the signature publishes a result, so every answer is a node
  of yours; a second read fills in names on firmware that admits `get name`.
- **Sending.** One signed publish per tagged device to its own topic, or one broadcast to a
  region; results per device in the right panel, counted on the message line, kept in history.
- **Command sets.** A named list of steps (`region put`, `region allowf`, `region save`, `region`
  is the shipped example) sent in order to the targets, stopping at the first refusal or silence;
  Send > Command set runs one, Options > Command sets defines one.
- **Statistics.** What the fleet knows about a device, from a Beacon server (read-only HTTP, no
  credentials): the node's status block with the sample's age, and its telemetry history. The
  node's own counters are serial-only in the firmware, so nothing here goes over the control plane.
- **Help.** Topics with cross-links, a command reference, and the same text on the command line:
  `mccontrol help commands`.

## The command line

```
mccontrol send loutest region put se-jkp '*'
mccontrol send JKG region allowf se-jkp --all
mccontrol send loutest region --dry-run
mccontrol regions loutest
mccontrol alt 2 loutest                  # shortcut 2 (ver); mccontrol shortcuts lists and --set changes them
mccontrol run add-scope loutest se-jkp   # a command set, step by step, stopping at the first refusal; mccontrol sets lists them
mccontrol stats loutest --range 48h      # from Beacon (beacon.meshat.se by default; mccontrol config --beacon-url for another)
mccontrol history loutest
mccontrol devices | add | remove | discover | key | keygen | config | test | help
```

## The configuration is the control broker

The only thing configured is the control broker: host (default `mqtt.louie.se`, the broker a
node's `mqtt.cmd.slot` points at), port, username, password and how long to wait for answers.
A region belongs to a device, not to the configuration.

```
~/.config/mccontrol/
    key.seed            your private seed, mode 600
    config.json         the control broker and how long to wait
    devices.json        your devices, each with its region
    history/<node>.jsonl
```

`MCCONTROL_HOME` keeps them elsewhere.

## The protocol

The envelope is the one the Observer auto-reply firmware verifies (`src/helpers/MqttControlLogic.h`
there): `v1|counter|expiry|command|signature`, signed over the topic and the body together, so a
command captured from one node cannot be replayed at another. The node checks the signature,
that the counter is higher than the last it accepted, the expiry, its rate limit, and an
allowlist of commands; a result is published only when the signature verified. A region tree
line (`region def a|* b|*`) cannot travel: `|` is the field separator; `region put` one name at
a time does the same job. `mccontrol help protocol` has the rest.

What that buys, and what it does not: no one on the broker can alter a field, move a command
to another node or region, turn it into a broadcast, or replay it at a node that has seen it;
a captured envelope can run once at most, and only within its life. The broker can still
drop or delay a command, read everything, and publish a forged *result* -- results are not
signed, so confirm a change with a read rather than with the `ok`. Each property is a test
in `tests/test_envelope.py`; `mccontrol help assurance` is the argument in full.

## Tests

`pytest` runs the unit tests and the pseudo-terminal walks: every function key is pressed once
against a scripted transport (`MCCONTROL_FAKE_RESULTS`), on an 80x24 and a 100-column terminal.
