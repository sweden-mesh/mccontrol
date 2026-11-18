# mccontrol

Remote control for MeshCore Observer repeaters. The owner signs a console command with an
Ed25519 key that never leaves their machine; the repeater checks the signature, runs the command
and answers, over MQTT or over the mesh through the owner's own companion radio. The key is set
on the repeater by cable, and identity, radio and destructive commands are refused remotely.

This repository holds only this page. The tool is in three others:

| repository | what it is |
|---|---|
| [mccontrol-web](https://github.com/sweden-mesh/mccontrol-web) | the tool in the browser, nothing to install: [mccontrol.meshcoresverige.se](https://mccontrol.meshcoresverige.se). Two layouts, the key kept encrypted in the browser, USB and Bluetooth to a companion. |
| [mccontrol-cli](https://github.com/sweden-mesh/mccontrol-cli) | the same tool in a terminal (Python): a command line, three full-screen interfaces and a local API. The reference implementation. `pip install git+https://github.com/sweden-mesh/mccontrol-cli` |
| [mccontrol-spec](https://github.com/sweden-mesh/mccontrol-spec) | the wire format, the command table, the deny list and the test vectors both apps are tested against. No code. |

Both apps read the same key backup file, so a key moves between them. The repeater firmware is
[MeshCore-lou](https://github.com/sweden-mesh/MeshCore-lou).

## In the browser

![mccontrol-web, the standard layout](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/standard-dark-1600.png)

![mccontrol-web, the retro board](https://raw.githubusercontent.com/sweden-mesh/mccontrol-web/main/docs/screenshots/retro-amber-1600.png)

## In a terminal

![mccontrol-cli, Monolith](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/monolith-board.png)

| MeshcoreCommander | IBMi |
|---|---|
| ![MeshcoreCommander](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/meshcorecommander-board.png) | ![IBMi](https://raw.githubusercontent.com/sweden-mesh/mccontrol-cli/main/docs/screenshots/ibmi-devices.png) |

The nodes and keys in the pictures are invented.

Issues: [mccontrol-web](https://github.com/sweden-mesh/mccontrol-web/issues) for the browser app,
[mccontrol-cli](https://github.com/sweden-mesh/mccontrol-cli/issues) for the terminal tool.
