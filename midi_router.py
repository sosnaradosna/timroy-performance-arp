#!/usr/bin/env python3
"""Midi Router
Routes incoming MIDI messages from one channel to two separate output channels
named "Pattern 1" and "Pattern 2" (or as configured in config.json).
"""
import json
import sys
from pathlib import Path

import mido

CONFIG_PATH = Path(__file__).with_name("config.json")
FORWARD_TYPES = {
    "note_on",
    "note_off",
    "control_change",
    "program_change",
    "pitchwheel",
    "aftertouch",
    "polytouch",
}

def load_config():
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")
    with CONFIG_PATH.open() as fp:
        data = json.load(fp)
    in_ch = int(data.get("input_channel", 1)) - 1
    out_map = {name: int(ch) - 1 for name, ch in data.get("output_channels", {}).items()}
    if not out_map:
        raise ValueError("No output_channels defined in config.json")
    return in_ch, out_map

def create_output_ports(out_map):
    ports = {}
    for name, ch in out_map.items():
        port = mido.open_output(name, virtual=True)  # type: ignore[attr-defined]
        ports[name] = (port, ch)
        print(f"Opened virtual output '{name}' on channel {ch + 1}")
    return ports

def pick_input_port():
    ins = mido.get_input_names()  # type: ignore[attr-defined]
    if not ins:
        raise RuntimeError("No MIDI input ports found.")
    for name in ins:
        if "through" not in name.lower() and "virtual" not in name.lower():
            return name
    return ins[0]

def main():
    in_channel, out_map = load_config()
    outputs = create_output_ports(out_map)

    # Create a dedicated virtual input port so that the DAW can route MIDI
    input_name = "TR Router In"
    print(f"Creating virtual input '{input_name}' listening on channel {in_channel + 1}")

    # Open the virtual input port (other applications / DAWs can now send to it)
    with mido.open_input(input_name, virtual=True) as in_port:  # type: ignore[attr-defined]
        try:
            for msg in in_port:
                if msg.type not in FORWARD_TYPES or msg.channel != in_channel:
                    continue
                for port, ch in outputs.values():
                    port.send(msg.copy(channel=ch))
        except KeyboardInterrupt:
            print("Stopping router…")
        finally:
            for port, _ in outputs.values():
                port.close()

if __name__ == "__main__":
    main() 