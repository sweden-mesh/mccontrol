# mccontrol

Remote control for MeshCore repeaters running lou firmware. The owner signs a console command with an
Ed25519 key that never leaves their machine; the repeater checks the signature, runs the command
and answers. It works over MQTT for a repeater with Wi-Fi, and over the radio for one without:
through one of the owner's own Wi-Fi repeaters as a relay, or through their companion radio. The
first key is set by cable or by a remote admin login, and identity, radio and destructive commands
are refused remotely.

Try it now, with no node and no key, on an invented fleet:
**[mccontrol.meshcoresverige.se/?demo=1](https://mccontrol.meshcoresverige.se/?demo=1)**

## What it does (firmware lou-20)

- **Finds your repeaters.** Each Wi-Fi repeater keeps a sealed "I am alive" message on the command
  server that only your key opens. Enter your key and they appear, wherever they are, with
  firmware, battery, uptime, neighbours and a map.
- **Runs a command on one and shows the answer**: settings, statistics, sensors, regions, a
  restart. The command server cannot read the command or the answer, and cannot tell which
  repeaters share an owner.
- **Reaches a repeater without Wi-Fi.** One of your Wi-Fi repeaters sends the command on by radio
  and returns the answer, unread, on a fixed airtime budget. Or your companion radio does, over
  USB or Bluetooth.
- **Mends a repeater that fell off the network.** Over the radio it accepts new Wi-Fi settings, its
  command connection being switched back on, and a clock that fell behind being set.
- **Updates firmware** on a Wi-Fi repeater: it is asked whether an update fits, then fetches a
  signed image itself and goes back to the old one if the new one fails.
- **Replaces your key on every repeater** from afar, one repeater at a time, with the old key
  working until you finish.
- **Warns you** when a repeater has not been heard, its battery is low, its firmware is behind,
  or its last command went unanswered.

What it will not do, by design: change a repeater's identity or radio settings, erase it, send
to every repeater at once, or send anything on a timer over the radio.

This repository holds only this page. The tool is in four others:

| repository | what it is |
|---|---|
| [mccontrol-web](https://github.com/sweden-mesh/mccontrol-web) | the tool in the browser, nothing to install: [mccontrol.meshcoresverige.se](https://mccontrol.meshcoresverige.se). Two layouts, the key kept encrypted in the browser, USB and Bluetooth to a companion. |
| [mccontrol-cli](https://github.com/sweden-mesh/mccontrol-cli) | the same tool in a terminal (Python): a command line, three full-screen interfaces and a local API. The reference implementation. `pip install git+https://github.com/sweden-mesh/mccontrol-cli` |
| [mccontrol-bridge](https://github.com/sweden-mesh/mccontrol-bridge) | lets the web app reach a companion over your network: a small WebSocket bridge next to a Wi-Fi companion or a mux, passing only the five commands the mesh road needs. |
| [mccontrol-spec](https://github.com/sweden-mesh/mccontrol-spec) | the wire format, the command table, the deny list and the test vectors both apps are tested against. No code. |

Both apps read the same key backup file, so a key moves between them. The repeater firmware is
[MeshCore-lou](https://github.com/sweden-mesh/MeshCore-lou).

## In the browser

![mccontrol-web, the standard layout](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/standard-dark-1600.png)

![mccontrol-web, the retro board](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/retro-amber-1600.png)

| A repeater without Wi-Fi, through a relay | Replacing the key on the repeaters |
|---|---|
| ![through a relay](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/standard-dark-relay.png) | ![replacing the key](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/standard-dark-rekey.png) |

Two layouts, the same tool: a list with one repeater's detail (above, first), or a board of tiles
with a command palette (second). More pictures are in
[mccontrol-web's README](https://github.com/sweden-mesh/mccontrol-web#readme).

## In a terminal

![mccontrol-cli, Monolith](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/monolith-board.png)

| MeshcoreCommander | IBMi |
|---|---|
| ![MeshcoreCommander](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/meshcorecommander-board.png) | ![IBMi](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/ibmi-devices.png) |

The nodes and keys in the pictures are invented; the browser pictures are of the demo.

Issues: [mccontrol-web](https://github.com/sweden-mesh/mccontrol-web/issues) for the browser app,
[mccontrol-cli](https://github.com/sweden-mesh/mccontrol-cli/issues) for the terminal tool.
