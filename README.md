# mccontrol

For the people who run MeshCore Observer repeaters: sign a command with a key kept on your
machine, send it to one repeater, to the ones you tagged, or to every repeater in a region,
see what came back, and keep a history per device. One backend, three interfaces on it, and a
command line.

## The backend and the API

Everything the tool does -- the key, the devices, signing, the broker, Beacon, history --
sits behind a JSON API on `127.0.0.1` (`mccontrol/api/`, standard library only). Each
interface and every subcommand starts it in-process on a free port under a secret path and
talks to it through `mccontrol/apiclient.py`; nothing else of the backend is imported by them,
and `tests/test_isolation.py` fails if it is. The contract is `docs/api/openapi.json`, served
at `<base>/openapi.json`, with a Swagger page over it at `<base>/docs`. `--api <url>` sends a
subcommand to a server already running instead of starting one of its own.

## The interfaces

`mccontrol --ui <name>` starts one (`ibmi` and `IBMi` are the same name). The name is kept in the configuration, so plain
`mccontrol` starts the last one used; with none used yet, or a name that is not there, it
lists them and exits. `--api <url>` runs it on a server already running instead of starting
one of its own, and that server's configuration keeps the name:

```
interfaces (mccontrol --ui <name>):
  IBMi               IBMi: the 5250 green screen -- numbered menus, Opt columns, ===> and F3/F5/F12
  MeshcoreCommander  Norton Commander panels in Turbo Pascal colours: the directory, history and every verb on a function key
  Monolith           Monolith: a retro-futuristic board of live tiles, a command palette, phosphor on black
```

Each is a package under `mccontrol/ui/<name>/` with `run(client)` and a one-line
`DESCRIPTION`; a fourth one is a directory more. Without a terminal each says so and exits 2.

### MeshcoreCommander

One screen, every verb on a function key.

```
 Devices  Send  Keys  Options  Help
╔══════════════ Devices ═══════════════╗╔═══════════ History: Gisebo ═══════════╗
║ Name            Node     Reg  Sent   ║║ 10-07 11:00 region        > * F       ║
║▸Gisebo          4AC61A7F JKG  2 h  ok║║ 10-06 21:14 get autoreply > on ...    ║
║▸(unnamed)       65C1D12C JKG  -      ║║                                       ║
║ Far             FD700369 GOT  1 d    ║║                                       ║
╚═ 2 of 3 tagged ══════════════════════╝╚═══════════════════════════════════════╝
  1Help  2Send  3Hist  4Edit  5Disco  6Regns  7All  8Del  9Menu  10Quit
```

The colours are Turbo Pascal's: a black menu bar and key bar with the hot letters and key
numbers in red, a blue work area, grey dialogs, green for the open menu. A terminal without
colour gets bold and reverse video instead.

A name ending in ` ?` is a device nothing has been heard from for seven days after a send — over
the control plane or by Beacon — and is a device to look at, not a verdict. Nothing refreshes by
itself.

The left panel is your device directory; the upper right panel follows the cursor (history), or
shows a send's results, or what a discovery found; the lower right keeps what the cursor device
last answered to each read, with its age. Ins tags rows, `*` inverts, `+`/`-` tag a
region. Dialogs take the one or two values a verb needs; Esc always cancels.

| key | does |
|---|---|
| F1 | help for where you are; Shift+F1 the contents; links are followed with Enter, Backspace or Alt+F1 goes back |
| F2 | send a command to the tagged devices, or the cursor device; Up recalls earlier commands |
| Enter | the command list for the cursor device, by group, type to filter; a command's parameter is asked for as radio buttons, checkboxes or a text field with examples, and the reply opens when it arrives. Commands the device's firmware does not admit are grey and skipped (`mccontrol/firmware.py` knows what each build admits; an unknown version counts as the oldest) |
| F3 | the cursor device's history as a window; on the right panel, Enter opens a reply |
| F4 | edit the device: name, region, notes |
| F5 | discover: the repeaters in a region that accept your key (`JKG, GOT` for several, `*` for every region you have); on the found list, F5 adds the tagged and F7 adds all |
| F6 | read the cursor device's region map |
| F7 | send a command to every node in a region |
| F8 | forget the cursor device |
| F9 / F10 | the menu / quit |
| Options > Broker | the control broker: host, port, TLS on or off, credentials |
| Alt+1..9 | a command per key to the tagged or cursor device, no dialog: `region`, `ver`, `get name`, `get autoreply`, `get radio`, `get repeat` as shipped; Options > Shortcuts edits them |
| Ins, Space, `*`, `+`, `-` | tag a row, invert, tag or untag a region |
| Tab | cycle the three panels: devices, history, last answers (Enter there reads it again) |
| `n` | read `get name` and `ver` from the tagged or cursor devices: an unnamed device takes the fleet's name, the Ver column fills (sortable) |
| `r` | refresh the directory's Batt and Seen columns from Beacon for the tagged devices, or all; never automatic |
| `s` | the cursor device's statistics from Beacon (battery, uptime, noise floor, airtime, packets, errors, with the sample's age, and 24 h of history); View menu for 48 h and 7 d |
| mouse | optional: click a row, a key-bar label or a menu title |

Commands that take something away (`region denyf *`, `region remove`, `set repeat off`, ...)
say what they do and ask first; a command that transmits is refused as a broadcast, as the
firmware refuses it.

### IBMi

The IBM i (AS/400, 5250) green screen: a numbered main menu, lists with an Opt column, a
`===>` command line, the message line above the function keys.

```
 MCCONTROL                    MCCONTROL Main Menu            09/10/26  00:15:58

 Select one of the following:

       1. Work with devices
       2. Send a command                            Broker       mqtt.louie.se
       3. Send a command to every node in a region  Signing key  present
       4. Run a command set                         Registered   no - select 8
       5. Discover devices                          Devices      3 devices
       6. Work with command sets
       7. Work with shortcuts
       8. Work with signing key
       9. Change configuration
      10. Test the broker connection
      11. Change directory columns
      12. Display broadcast history
      13. Help contents
      90. Sign off
 Selection or command
 ===>



 F1=Help   F3=Exit   F5=Refresh   F12=Cancel
```

- **Selection.** A number on `===>` opens that screen; `90`, `signoff` or `quit` signs off.
- **Opt.** On Work with Devices, type an option on a row and press Enter: `1` tag, `2` change,
  `4` forget, `5` display, `6` statistics, `7` commands, `8` last answers, `9` send, `10`
  regions, `11` name and version. Other lists show their own options above the header.
- **F-keys.** F1 help, F3 exit, F5 refresh, F12 cancel everywhere; on Work with Devices F6
  discover, F7 Batt/Seen from Beacon, F9 send, F10 names, F11 sort, F13-F21 the shortcuts.
- **`===>` verbs.** `send <device> <command>`, `all <region> <command>`, `discover <region>`,
  `run <set> [arg]`, `alt <n>`, `names`, `fleet`, `tag <region>`, `untag`, `invert`, `sort <by>`,
  `devices`, `help [topic]`; on Work with Devices they act on the tagged rows or the cursor
  row, and `stats [24h|48h|168h]` is added.

### Monolith

The fleet as a board of tiles in phosphor on black (amber or green): the directory, the device
under the cursor with its reads and a statistics sparkline, a feed of results, the broker and
key, a clock. Jobs are drawn as bars. At 80x24:

```
 ▞▚ M O N O L I T H ▚▞  mccontrol  amber                              00:15:57
╭─ ◉ DEVICES ──────────────────────────────╮╭─ ● STATUS ───────────────────────╮
│   Name               Node     Reg  Sent  ││ BROKER  mqtt.louie.se:8883       │
│   Far                FD700369 GOT  -     ││ KEY     2A6A1C1E…                │
│   Gisebo             4AC61A7F JKG  -     ││ REG     no  :register            │
│   (unnamed)          65C1D12C JKG  -     ││ LINK    untested  :test          │
│                                          │╰──────────────────────────────────╯
│                                          │╭─ ● DEVICE FAR ───────────────────╮
│                                          ││ FD700369  GOT  ver ?             │
│                                          ││ no telemetry yet: s reads the st │
│                                          ││ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│                                          ││                                  │
│                                          ││                                  │
│                                          ││                                  │
│                                          │╰───────────── s stats  h history ─╯
│                                          │╭─ ● RESULTS ──────────────────────╮
│                                          ││ Nothing run yet. : opens the pal │
│                                          ││                                  │
│                                          ││                                  │
│                                          ││                                  │
│                                          ││                                  │
│                                          ││                                  │
╰───────────────────── 3 devices  by name ─╯╰──────────────────────────────────╯
 : menu ↵ cmds ␣ tag c send a all s stat d find k keys Del forget ? help q quit
```

- **`:` or Ctrl+P** opens the palette: every verb and every command, type to filter.
- **Single keys.** Enter the command list (or read again, or open a result, by tile), Space or
  Ins tag, `*` invert, `+`/`-` tag or untag a region, `c` send, `a` every node in a region,
  `s` statistics, `n` names and versions, `r` Batt/Seen from Beacon, `d` discover, `h` history,
  `e` edit, `k` keys, `o` the next sort, `p` amber or green, Del forget, Alt+1..9 the
  shortcuts, Tab the next tile, `?` or F1 help, `q` or F10 quit. The mouse picks a tile, a row or a key-bar label; the wheel scrolls.

## Install

```
pip install .
mccontrol keygen                                  # once; prints the two serial commands
mccontrol config --username <user> --password <pass>   # the control broker's credentials
mccontrol test                                    # connect, sign in, subscribe, publish, receive
mccontrol discover JKG --add                      # every node in JKG that accepts your key
mccontrol --ui MeshcoreCommander                  # the screens; plain mccontrol is the last used
```

Python 3.10 or newer; `pycryptodome` (Ed25519) and `paho-mqtt`; `curses` from the standard
library. 80x24 is the minimum; wider terminals get wider panels, and on a narrow one
Options > Directory columns (or `mccontrol config --columns ver,result`) picks which columns
the directory shows.

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
- **Statistics.** The node's own counters first, by one signed `get stats` (firmware of
  2026-10-08 or later; older firmware answers not-allowed and the view says so), then what a
  Beacon server holds (read-only HTTP, no credentials): the status block with the sample's age
  and the telemetry history.
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
mccontrol fleet                          # the directory with Batt and Seen refreshed from Beacon
mccontrol refresh [device...]            # get name and ver over the control plane; names the unnamed
mccontrol reads loutest                  # what it last answered to each read, with the age
mccontrol commands [device]              # the command table; with a device, what its firmware lacks is marked
mccontrol history loutest
mccontrol devices | add | remove | discover | key | keygen | config | test | help
```

## Setting up a repeater

Two lines over its serial console, on the firmware of 2026-10-08 or later (ar-11):

```
set mqtt.cmd.pubkey <your 64-hex public key>     # mccontrol key prints it
set mqtt.cmd on                                  # takes a free slot, points it at mqtt.louie.se; reboot
```

The node answers `OK - slot 3, mccontrol (mqtts://mqtt.louie.se:8883), packets off; reboot to
apply`. A board without PSRAM runs two MQTT slots at once; with both analyzer uplinks up it
says so and asks you to turn one off (`set mqtt2.preset none`) rather than doing it for you.
Then `mccontrol register` once per key, so the broker checks your commands before they fan out.

## Signing in

With no username configured, the tool signs in to the broker the way a node does: the
username is `v1_` plus its public key, the password a token signed by its key
(`mccontrol/token.py`, the firmware's own JWT shape byte for byte), renewed each session.
Nothing is handed out or typed; the key is the identity, and that is how the default broker
`mqtt.louie.se` knows who publishes. A region running its own broker with a username and
password sets them, and those are used instead.

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
there): the node's topic is named by its whole 64-hex key from firmware ar-11 and by the first
8 hex before that -- the tool keeps the key per device and addresses each by the form its
firmware uses, showing the 8-hex id as the name; `v1|counter|expiry|command|signature`, signed over the topic and the body together, so a
command captured from one node cannot be replayed at another. The node checks the signature,
that the counter is higher than the last it accepted, the expiry, its rate limit, and a deny
list of commands (since 2026-10-08 everything the console answers travels except identity, the
control plane's own settings, credentials, the destructive group, position and role, the radio,
transmissions and the clock step; `mccontrol help commands` has it, and `mccontrol/denylist.py`
mirrors it so a refusal is explained before signing); a result is published only when the
signature verified. A region tree
line (`region def a|* b|*`) cannot travel: `|` is the field separator; `region put` one name at
a time does the same job. `mccontrol help protocol` has the rest.

What that buys, and what it does not: no one on the broker can alter a field, move a command
to another node or region, turn it into a broadcast, or replay it at a node that has seen it;
a captured envelope can run once at most, and only within its life. The broker can still
drop or delay a command and read everything. A result from firmware ar-11 or later is signed by
the node and shown `[signed]`; a forged one shows as BAD SIGNATURE and is ignored; an unsigned
`r1` from older firmware is shown as is -- confirm those with a read rather than with the `ok`. Each property is a test
in `tests/test_envelope.py`; `mccontrol help assurance` is the argument in full.

## Tests

`pytest` runs the unit tests, the API against its OpenAPI document, the isolation test, and
pseudo-terminal walks of each interface against a scripted transport (`MCCONTROL_FAKE_RESULTS`):
every function key, option and verb pressed once, on an 80x24 and a wider terminal.
