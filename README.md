# mccontrol

A green screen for the people who run MeshCore Observer repeaters: sign a command, send it to
one repeater or to every repeater in your region, see what came back, and keep a history per
device. Keys stay on your machine.

```
 MCCONTROL                    MCCONTROL Main Menu                 07/10/26  21:14:02

 Select one of the following:

       1. Work with devices
       2. Send a command to a device
       3. Send a command to all devices
       4. Work with signing key
       5. Change configuration
       6. Display command history
      90. Sign off

 Selection or command
 ===> ______________________________________________________________

 F3=Exit   F5=Refresh
```

## Install

```
pip install .
mccontrol keygen        # once; prints the two serial commands to provision each repeater
mccontrol config --region JKG
mccontrol add LOUTEST <64-hex public key> "the bench repeater"
mccontrol               # the screens
```

Python 3.10 or newer; depends on `pycryptodome` (Ed25519) and `paho-mqtt`. The interface uses
`curses`, which is in the standard library on Linux and macOS.

## What it does

- **Keys.** `mccontrol keygen` makes an Ed25519 seed, stores it with owner-only permissions under
  `~/.config/mccontrol/key.seed`, and prints `set mqtt.cmd.pubkey <public>` and `set mqtt.cmd on`
  to type into each repeater's serial console once. It refuses to replace a key by accident,
  because every repeater provisioned with the old one would stop listening. The seed is never
  shown and never leaves the machine inside anything but a signature.
- **Devices.** The repeaters you manage, by name, with notes, when you last sent to each and
  what it answered. Tens of them fit on one screen. **F6 discovers them**: every node publishing
  a status in your region is listed with its name, model and firmware, and `1` on a row
  remembers it. The command line does the same with `mccontrol discover` and `--add`.
- **Sending.** A command is signed for exactly one topic -- one node, or `all` for every node
  in the region -- and published; the tool waits for the node's reply on its result topic and
  shows it in words (`refused: not-allowed -- The command is not on the node's allowlist`).
  Commands that take something away (`region denyf *`, `region remove`, `set repeat off`) say
  what they do and ask first; a command that transmits is refused as a broadcast, as the
  firmware refuses it.
- **History.** Every command sent to a device and what came back, kept per device.

The command line does the same without the screens: `mccontrol send loutest region put se-jkp '*'`,
`mccontrol send all region allowf se-jkp --all`, `mccontrol regions loutest`,
`mccontrol history loutest`, `--dry-run` to sign and print without sending.

## The protocol

The envelope is the one the Observer auto-reply firmware verifies (`src/helpers/MqttControlLogic.h`
there): `v1|counter|expiry|command|signature`, signed over the topic and the body together, so
a command captured from one node cannot be replayed at another. The node checks the signature,
that the counter is higher than the last it accepted, the expiry, its rate limit, and an
allowlist of commands; everything else is refused before it reaches the command line. What the
allowlist admits is the firmware's decision, not this tool's.

A region tree line (`region def a|* b|*`) cannot travel: `|` is the envelope's field separator.
`region put <name> *` one name at a time does the same job and leaves existing names alone.

## Where things live

```
~/.config/mccontrol/
    key.seed            your private seed, mode 600
    config.json         broker, region, how long to wait for an answer
    devices.json        your devices
    history/<node>.jsonl
```

Set `MCCONTROL_HOME` to keep them elsewhere.
