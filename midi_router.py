#!/usr/bin/env python3
"""TR MIDI Router & Arpeggiator

* Listens on a virtual input "TR Router In" (default channel defined in
  ``config.json`` – channel **1** if omitted).
* Captures up to 8 simultaneously-held notes (chord) on the input channel.
  Notes are stored from lowest to highest and indexed **1…8**.
* Generates two arpeggio streams clock-synchronised to incoming MIDI clock
  (24 PPQN):
    • Pattern 1  → ascending 1 → 8 → …  (output channel configured as "Pattern 1")
    • Pattern 2  → descending 8 → 1 → … (output channel configured as "Pattern 2")
* Arpeggiator uruchamia się automatycznie, gdy tylko co najmniej jedna nuta
  akordu jest przytrzymana i docierają impulsy MIDI Clock (24 PPQN).
  Nie wymaga komunikatów Start/Stop – choć nadal je obsługuje do opcjonalnego
  resetu transportu.

Requires: ``mido`` + a backend such as ``python-rtmidi`` (declared in
``requirements.txt``).
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Any

import mido
import os, signal, time
LOCK_PATH = Path.home() / ".tr_router.lock"

# ---------------------------------------------------------------------------
# Single-instance enforcement
# ---------------------------------------------------------------------------

def ensure_single_instance():
    """Terminate previous running instance (if any) and create a lock file."""
    if LOCK_PATH.exists():
        try:
            old_pid = int(LOCK_PATH.read_text())
            if old_pid != os.getpid():
                # Check if process is alive
                try:
                    os.kill(old_pid, 0)
                except ProcessLookupError:
                    pass  # not running
                else:
                    print(f"Found previous instance (PID {old_pid}), terminating…")
                    try:
                        os.kill(old_pid, signal.SIGTERM)
                    except PermissionError:
                        print("  Warning: insufficient permission to terminate old process.")
                    # Wait a bit for graceful shutdown
                    for _ in range(10):
                        time.sleep(0.3)
                        try:
                            os.kill(old_pid, 0)
                        except ProcessLookupError:
                            break
                    else:
                        # force kill
                        try:
                            os.kill(old_pid, signal.SIGKILL)
                        except Exception:
                            pass
        except Exception as e:
            print(f"Error handling existing lock file: {e}")
        # always remove stale lock
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except Exception:
            pass

    # Create new lock with current pid
    try:
        LOCK_PATH.write_text(str(os.getpid()))
    except Exception as e:
        print(f"Warning: could not create lock file: {e}")


def cleanup_lock():
    try:
        if LOCK_PATH.exists():
            if LOCK_PATH.read_text().strip() == str(os.getpid()):
                LOCK_PATH.unlink()
    except Exception:
        pass


CONFIG_PATH = Path(__file__).with_name("config.json")
PPQN = 24                     # MIDI clock pulses per quarter note
TICKS_PER_STEP = PPQN // 4    # 16-th note → 6 pulses
MAX_NOTES = 8                 # maximum chord size


def load_config() -> Tuple[int, Dict[str, int], Dict[str, Dict[str, Any]]]:
    """Read ``config.json`` and return (input_channel, output_mapping, patterns_cfg)."""
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config file not found at {CONFIG_PATH}")

    raw = CONFIG_PATH.read_text()
    # Allow "//" style comments in JSON for convenience.
    filtered_lines = []
    for line in raw.splitlines():
        # Remove everything after // but keep http:// etc. by checking for quotes
        if "//" in line:
            parts = line.split("//", 1)
            before = parts[0]
            # if // occurs inside quotes, keep it
            if before.count("\"") % 2 == 0:
                line = before
        filtered_lines.append(line)
    data = json.loads("\n".join(filtered_lines))
    in_ch = int(data.get("input_channel", 1)) - 1  # convert to 0-based
    out_map = {name: int(ch) - 1 for name, ch in data.get("output_channels", {}).items()}
    if not out_map:
        raise ValueError("No output_channels defined in config.json")

    # pattern definitions: { "Pattern 1": {"length": 16, "steps": [...]} }
    patterns_raw = data.get("patterns", {})

    def default_pattern(name: str):
        # Fallback: ascending for Pattern 1, descending for Pattern 2
        if "1" in name:
            steps = list(range(1, 9))
        else:
            steps = list(range(8, 0, -1))
        return {"length": len(steps), "steps": steps}

    patterns_cfg: Dict[str, Dict[str, Any]] = {}
    for pname in out_map.keys():
        pconf = patterns_raw.get(pname) or patterns_raw.get(pname.lower().replace(" ", ""))
        if not pconf:
            pconf = default_pattern(pname)
        length = int(pconf.get("length", len(pconf.get("steps", []))))
        steps_list = pconf.get("steps", [])[:16]
        if not steps_list:
            steps_list = default_pattern(pname)["steps"]
            length = len(steps_list)
        patterns_cfg[pname] = {"length": max(1, min(16, length)), "steps": steps_list}

    return in_ch, out_map, patterns_cfg


def create_output_ports(out_map: Dict[str, int]):
    """Return dict {name: (mido.Output, channel)} for each pattern output."""
    ports = {}
    for name, ch in out_map.items():
        port = mido.open_output(name, virtual=True)  # type: ignore[attr-defined]
        ports[name] = (port, ch)
        print(f"Opened virtual output '{name}' on channel {ch + 1}")
    return ports


# ---------------------------------------------------------------------------
# Runtime helpers
# ---------------------------------------------------------------------------

def add_note(chord: List[int], note: int):
    """Insert *note* into *chord* keeping ascending order and size ≤ MAX_NOTES."""
    if note in chord:
        return
    chord.append(note)
    chord.sort()
    if len(chord) > MAX_NOTES:
        # Keep the *lowest* MAX_NOTES notes (spec: ignore extra >8)
        del chord[MAX_NOTES:]


def remove_note(chord: List[int], note: int):
    """Remove note from chord list if present."""
    try:
        chord.remove(note)
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    ensure_single_instance()
    in_channel, out_map, pattern_cfgs = load_config()
    outputs = create_output_ports(out_map)

    # Map pattern names → behaviour functions
    pattern_order = {
        "Pattern 1": lambda idx, ln: idx,                   # ascending 1→8
        "Pattern 2": lambda idx, ln: ln - 1 - idx           # descending 8→1
    }

    # Runtime state
    chord_notes: List[int] = []             # current chord (sorted)
    # Arp is considered "armed" whenever `chord_notes` is non-empty.
    tick_counter = 0                        # MIDI clock ticks since start
    step_index = 0                          # 16-th step counter
    last_played: Dict[str, Optional[int]] = {name: None for name in outputs}

    input_name = "TR Router In"
    print(
        f"Creating virtual input '{input_name}' listening on MIDI channel {in_channel + 1}\n"
        f"• Send START/STOP and CLOCK from your DAW to this port to drive the arpeggiator.\n"
        f"• Play chords (≤8 notes) on the same channel to generate arpeggios."
    )

    with mido.open_input(input_name, virtual=True) as in_port:  # type: ignore[attr-defined]
        try:
            for msg in in_port:
                # ----------------------------- Clock & transport handling ----
                if msg.type == "clock":
                    if not chord_notes:
                        # no notes held – ensure any lingering notes are off
                        for name, last in last_played.items():
                            if last is not None:
                                port, ch = outputs[name]
                                port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
                                last_played[name] = None
                        continue  # nothing to arpeggiate

                    tick_counter += 1
                    if tick_counter % TICKS_PER_STEP != 0:
                        continue  # wait until next 16-th note

                    if not chord_notes:
                        continue  # nothing to play

                    # send note-offs for previous step
                    for name, last in last_played.items():
                        if last is not None:
                            port, ch = outputs[name]
                            port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
                            last_played[name] = None

                    ln = len(chord_notes)
                    if ln == 0:
                        continue

                    # determine notes for each pattern and send note-ons based on config
                    for name, cfg in pattern_cfgs.items():
                        plen = cfg.get("length", 0) or 0
                        steps = cfg.get("steps", [])
                        if plen == 0 or not steps:
                            continue
                        step_pos = step_index % plen
                        # guard index range
                        if step_pos >= len(steps):
                            continue
                        idx = steps[step_pos]
                        if not (1 <= idx <= ln):
                            # index out of current chord range → silent step
                            continue
                        note = chord_notes[idx - 1]
                        port, ch = outputs[name]
                        port.send(mido.Message("note_on", note=note, velocity=100, channel=ch))
                        last_played[name] = note

                    step_index += 1
                    continue  # handled clock

                if msg.type == "start":
                    tick_counter = 0
                    step_index = 0
                    print("[Transport] START message received – counter reset (optional)")
                    # don't change behaviour
                    continue

                if msg.type == "stop":
                    # Stop only resets counters and shuts off notes; arp will re-arm automatically when notes are held.
                    for name, last in last_played.items():
                        if last is not None:
                            port, ch = outputs[name]
                            port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
                            last_played[name] = None
                    tick_counter = 0
                    step_index = 0
                    print("[Transport] STOP message received – counters cleared")
                    continue

                # ----------------------------- Note handling ----------------
                if msg.type in ("note_on", "note_off") and msg.channel == in_channel:
                    prev_len = len(chord_notes)
                    note = msg.note
                    if msg.type == "note_on" and msg.velocity > 0:
                        add_note(chord_notes, note)
                    else:  # note_off OR note_on with velocity 0
                        remove_note(chord_notes, note)

                    new_len = len(chord_notes)

                    # When chord becomes empty → stop all currently sounding notes
                    if new_len == 0 and prev_len > 0:
                        for name, last in last_played.items():
                            if last is not None:
                                port, ch = outputs[name]
                                port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
                                last_played[name] = None
                        step_index = 0  # reset for next chord
                        continue

                    # When chord starts (was empty) → reset sequence to start on index 0
                    if prev_len == 0 and new_len > 0:
                        step_index = 0

                    # If chord size changed but not empty, keep current step_index so arpeggio continues seamlessly.
                    continue

                # We ignore all other message types.

        except KeyboardInterrupt:
            print("Stopping router…")
        finally:
            cleanup_lock()
            # make sure we send note_off on exit
            for name, last in last_played.items():
                if last is not None:
                    port, ch = outputs[name]
                    port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
            for port, _ in outputs.values():
                port.close()


if __name__ == "__main__":
    main() 