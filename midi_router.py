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
import random
import os, signal, time

# Lock file location (used for single instance + external control)
LOCK_PATH = Path.home() / ".tr_router.lock"

# ---------------------------------------------------------------------------
# Live-reload support via SIGUSR1
# ---------------------------------------------------------------------------

RELOAD_SIGNAL = signal.SIGUSR1 if hasattr(signal, "SIGUSR1") else signal.SIGHUP

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

# ---------------------------------------------------------------------------
# Rhythm division helpers
# ---------------------------------------------------------------------------

DIVISION_BASE = {
    "1": 4 * PPQN,   # whole note
    "1/2": 2 * PPQN, # half
    "1/4": PPQN,     # quarter
    "1/8": PPQN // 2,
    "1/16": PPQN // 4,
    "1/32": PPQN // 8,
}


def parse_division(s: str) -> int:
    """Convert division string like '1/8', '1/4d', '1/16t' into pulses per step."""
    s = s.strip().lower()
    dotted = s.endswith("d")
    triplet = s.endswith("t")
    quint = s.endswith("q")
    if dotted or triplet or quint:
        s_base = s[:-1]
    else:
        s_base = s
    pulses = DIVISION_BASE.get(s_base, PPQN // 4)  # default 1/16
    if dotted:
        pulses = int(pulses * 1.5)
    elif triplet:
        pulses = int(pulses * 2 / 3)
    elif quint:
        pulses = int(pulses * 4 / 5)
    return max(1, pulses)


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
        octave_shift = int(pconf.get("oktawa", 0))  # -2..2
        velocity_list_raw = pconf.get("velocity", pconf.get("velocities", []))
        if not steps_list:
            steps_list = default_pattern(pname)["steps"]
            length = len(steps_list)
        # Prepare velocity list
        if not velocity_list_raw:
            velocity_list = [100] * length
        else:
            velocity_list = []
            for v in velocity_list_raw[:length]:
                if isinstance(v, str) and v.upper() == "R":
                    velocity_list.append("R")
                else:
                    try:
                        num = int(v)
                    except ValueError:
                        num = 100
                    velocity_list.append(max(1, min(127, num)))
            if len(velocity_list) < length:
                velocity_list += [100] * (length - len(velocity_list))

        # Prepare vrandom list (0-100)
        vrandom_raw = pconf.get("v-random", pconf.get("vrandom", []))
        if not vrandom_raw:
            vrandom_list = [0] * length
        else:
            vrandom_list = []
            for v in vrandom_raw[:length]:
                try:
                    val = int(v)
                except (ValueError, TypeError):
                    val = 0
                vrandom_list.append(max(0, min(100, val)))
            if len(vrandom_list) < length:
                vrandom_list += [0] * (length - len(vrandom_list))

        # Prepare s-prob list (0-100)
        sprob_raw = pconf.get("s-prob", pconf.get("sprob", []))
        if not sprob_raw:
            sprob_list = [100] * length
        else:
            sprob_list = []
            for v in sprob_raw[:length]:
                try:
                    val = int(v)
                except (ValueError, TypeError):
                    val = 100
                sprob_list.append(max(0, min(100, val)))
            if len(sprob_list) < length:
                sprob_list += [100] * (length - len(sprob_list))

        # Prepare s-oct list (-2..2)
        soct_raw = pconf.get("s-oct", pconf.get("soct", []))
        if not soct_raw:
            soct_list = [0] * length
        else:
            soct_list = []
            for v in soct_raw[:length]:
                try:
                    val = int(v)
                except (ValueError, TypeError):
                    val = 0
                soct_list.append(max(-2, min(2, val)))
            if len(soct_list) < length:
                soct_list += [0] * (length - len(soct_list))

        division_str = str(pconf.get("division", "1/16"))
        pulses_val = parse_division(division_str)

        # ---------------- Gate list (1-100 %) or 'T' for tie ----------------
        gate_raw = pconf.get("gate", [])
        if not gate_raw:
            gate_list = [100] * length
        else:
            gate_list: List[Any] = []  # may contain int or 'T'
            for v in gate_raw[:length]:
                if isinstance(v, str) and v.upper() == "T":
                    gate_list.append("T")
                    continue
                try:
                    g_val = int(v)
                except (ValueError, TypeError):
                    g_val = 100
                gate_list.append(max(1, min(100, g_val)))
            if len(gate_list) < length:
                gate_list += [100] * (length - len(gate_list))

        patterns_cfg[pname] = {
            "length": max(1, min(16, length)),
            "steps": steps_list,
            "octave": max(-5, min(5, octave_shift)),  # clamp defensively
            "velocity": velocity_list,
            "vrandom": vrandom_list,
            "sprob": sprob_list,
            "soct": soct_list,
            "gate": gate_list,
            "pulses": float(pulses_val),
        }

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

    # Live-reload flag set by signal handler
    reload_requested = False

    def _handle_reload(signum, frame):  # type: ignore[unused-arg]
        nonlocal reload_requested
        reload_requested = True

    try:
        signal.signal(RELOAD_SIGNAL, _handle_reload)
    except Exception as e:
        print(f"Warning: cannot set reload signal handler: {e}")

    # Map pattern names → behaviour functions
    pattern_order = {
        "Pattern 1": lambda idx, ln: idx,                   # ascending 1→8
        "Pattern 2": lambda idx, ln: ln - 1 - idx           # descending 8→1
    }

    # Runtime state
    chord_notes: List[int] = []  # current chord (sorted)
    last_played: Dict[str, Optional[int]] = {name: None for name in outputs}

    # Per-pattern runtime (independent clocks)
    pattern_state: Dict[str, Dict[str, Any]] = {}
    for name, cfg in pattern_cfgs.items():
        pattern_state[name] = {
            "tick": 0.0,
            "step": 0,
            "rand": [],
            "rand_vel": [],
            "note_on": None,
            "gate_left": 0.0,
            "tie_prev": False,
            "pending_off": None,
            "pending_left": 0.0,
        }

    # -------------------------------------------------------------------
    # Helper to play one step immediately (used on START and chord enter)
    # -------------------------------------------------------------------

    def play_pattern_step(pattern_name: str):
        """Send note for current step of pattern immediately."""
        cfg = pattern_cfgs[pattern_name]
        rt = pattern_state[pattern_name]
        plen = cfg["length"]
        steps = cfg["steps"]
        velocities = cfg["velocity"]
        vrands = cfg["vrandom"]
        gates = cfg["gate"]

        if plen == 0 or not steps or not chord_notes:
            return

        step_pos = rt["step"] % plen

        # reset caches if step 0 (fresh loop)
        if step_pos == 0:
            rt["rand"] = [None] * len(steps)
            rt["rand_vel"] = [None] * len(velocities)

        ln = len(chord_notes)
        step_val = steps[step_pos]

        # note index resolution
        if isinstance(step_val, str):
            if step_val.upper() == "X":
                return  # rest – do nothing
            if step_val.upper() == "R":
                if len(rt["rand"]) < len(steps):
                    rt["rand"] += [None]*(len(steps)-len(rt["rand"]))
                if rt["rand"][step_pos] is None:  # type: ignore[index]
                    rt["rand"][step_pos] = random.randint(1, ln)  # type: ignore[index,assignment]
                idx = rt["rand"][step_pos]
            else:
                idx = int(step_val)
        else:
            idx = int(step_val)

        if idx is None or not (1 <= idx <= ln):
            return

        # velocity resolution
        vel_val = velocities[step_pos % len(velocities)] if velocities else 100
        vrand_val = vrands[step_pos % len(vrands)] if vrands else 0

        if isinstance(vel_val, str) and vel_val.upper() == "R":
            base_vel = random.randint(1, 127)
        else:
            base_vel = int(vel_val)

        if vrand_val >= 100:
            vel = random.randint(1, 127)
        elif vrand_val > 0:
            span = int(vrand_val * 127 / 100)
            half = span // 2
            vel = random.randint(max(1, base_vel - half), min(127, base_vel + half))
        else:
            vel = base_vel

        # octave shift
        note_num = chord_notes[idx - 1] + cfg["octave"] * 12
        if not (0 <= note_num <= 127):
            return

        port, ch = outputs[pattern_name]
        port.send(mido.Message("note_on", note=note_num, velocity=vel, channel=ch))

        # Register playing note & gate so countdown can turn it off correctly
        rt["note_on"] = note_num
        gate_val = gates[step_pos % len(gates)] if gates else 100
        if isinstance(gate_val, str) and str(gate_val).upper() == "T":
            rt["gate_left"] = -1.0  # sustain until non-tie
            rt["tie_prev"] = True
        else:
            try:
                gate_pct = int(gate_val)
            except Exception:
                gate_pct = 100
            rt["gate_left"] = cfg["pulses"] * gate_pct / 100.0
            rt["tie_prev"] = False

        last_played[pattern_name] = note_num

        # advance step for next cycle counting
        rt["step"] = (rt["step"] + 1) % plen

    input_name = "TR Router In"
    print(
        f"Creating virtual input '{input_name}' listening on MIDI channel {in_channel + 1}\n"
        f"• Send START/STOP and CLOCK from your DAW to this port to drive the arpeggiator.\n"
        f"• Play chords (≤8 notes) on the same channel to generate arpeggios."
    )

    with mido.open_input(input_name, virtual=True) as in_port:  # type: ignore[attr-defined]
        try:
            for msg in in_port:
                # -------------------------------------------------- reload --
                if reload_requested:
                    reload_requested = False
                    try:
                        _in, new_out_map, new_cfgs = load_config()

                        # Recreate output ports if mapping changed
                        if new_out_map != out_map:
                            # Close existing ports
                            for port, _ch in outputs.values():
                                try:
                                    port.close()
                                except Exception:
                                    pass
                            outputs = create_output_ports(new_out_map)  # type: ignore[assignment]
                            out_map.clear()
                            out_map.update(new_out_map)
                            # Reset last_played to match new outputs
                            last_played.clear()
                            last_played.update({name: None for name in outputs})

                        pattern_cfgs = new_cfgs  # type: ignore[assignment]

                        # Recreate/refresh pattern_state dict
                        pattern_state.clear()
                        for name, cfg in pattern_cfgs.items():
                            pattern_state[name] = {
                                "tick": 0.0,
                                "step": 0,
                                "rand": [],
                                "rand_vel": [],
                                "note_on": None,
                                "gate_left": 0.0,
                                "tie_prev": False,
                                "pending_off": None,
                                "pending_left": 0.0,
                            }

                        print("Configuration reloaded from config.json (ports and patterns updated)")
                    except Exception as err:
                        print(f"Failed to reload configuration: {err}")
                # ----------------------------- Clock & transport handling ----
                if msg.type == "clock":
                    if not chord_notes:
                        # stop any sustained notes if chord empty
                        for name, last in last_played.items():
                            if last is not None:
                                port, ch = outputs[name]
                                port.send(mido.Message("note_off", note=last, velocity=0, channel=ch))
                                last_played[name] = None
                        continue

                    ln = len(chord_notes)
                    if ln == 0:
                        continue

                    for name, cfg in pattern_cfgs.items():
                        rt = pattern_state[name]
                        rt["tick"] += 1.0
                        if rt["tick"] + 1e-9 < cfg["pulses"]:
                            continue  # wait until pulses reached

                        rt["tick"] -= cfg["pulses"]

                        plen = cfg["length"]
                        steps = cfg["steps"]
                        velocities = cfg["velocity"]
                        vrands = cfg["vrandom"]
                        gates = cfg["gate"]
                        sprobs = cfg.get("sprob", [100]*len(steps))
                        socts = cfg.get("soct", [0]*len(steps))

                        step_pos = rt["step"] % plen
                        # Reset random cache at start of cycle
                        if step_pos == 0:
                            rt["rand"] = [None] * len(steps)
                            rt["rand_vel"] = [None] * len(velocities)

                        # Probability check
                        if random.randint(1,100) > sprobs[step_pos % len(sprobs)]:
                            rt["step"] = (rt["step"] + 1) % plen
                            continue

                        step_val = steps[step_pos]

                        # note index resolution
                        if isinstance(step_val, str):
                            if step_val.upper() == "X":
                                # rest – advance step and skip note generation
                                rt["step"] = (rt["step"] + 1) % plen
                                continue
                            if step_val.upper() == "R":
                                if len(rt["rand"]) < len(steps):
                                    rt["rand"] += [None]*(len(steps)-len(rt["rand"]))
                                if rt["rand"][step_pos] is None:  # type: ignore[index]
                                    rt["rand"][step_pos] = random.randint(1, ln)  # type: ignore[index,assignment]
                            idx = rt["rand"][step_pos]
                        else:
                            idx = int(step_val)

                        if idx is None or not (1 <= idx <= ln):
                            idx = random.randint(1, ln)
                        note = chord_notes[idx - 1] + (cfg["octave"] + socts[step_pos % len(socts)]) * 12
                        # choose velocity for this step
                        vel_val = velocities[step_pos % len(velocities)] if velocities else 100
                        vrand_val = vrands[step_pos % len(vrands)] if vrands else 0
                        # handle random velocity
                        if isinstance(vel_val, str) and vel_val.upper() == "R":
                            if len(rt["rand_vel"]) < len(velocities):
                                rt["rand_vel"] += [None]*(len(velocities)-len(rt["rand_vel"]))
                            if rt["rand_vel"][step_pos] is None:  # type: ignore[index,assignment]
                                rt["rand_vel"][step_pos] = random.randint(1, 127)  # type: ignore[index,assignment]
                            base_vel = rt["rand_vel"][step_pos]
                        else:
                            base_vel = int(vel_val)

                        if base_vel is None:
                            base_vel = 64

                        # apply vrandom percentage to get final vel
                        if vrand_val >= 100:
                            vel = random.randint(1, 127)
                        elif vrand_val > 0:
                            span = int(vrand_val * 127 / 100)
                            half = span // 2
                            vel = random.randint(max(1, base_vel - half), min(127, base_vel + half))
                        else:
                            vel = base_vel

                        # gate handling
                        gate_val = gates[step_pos % len(gates)] if gates else 100
                        tie_flag = isinstance(gate_val, str) and str(gate_val).upper() == "T"

                        # note already includes global + step octave shift
                        if not (0 <= note <= 127):
                            continue  # skip if out of MIDI range
                        port, ch = outputs[name]
                        # note-on/note-off logic with tie
                        if tie_flag:
                            # Tie step: sustain or overlap
                            if rt["note_on"] is None:
                                # Nothing playing, just start note
                                port.send(mido.Message("note_on", note=note, velocity=vel, channel=ch))
                                rt["note_on"] = note
                            else:
                                if rt["note_on"] != note:
                                    # Different note – overlap for glide
                                    # overlap 1 tick: schedule previous note off after next tick
                                    rt["pending_off"] = rt["note_on"]
                                    rt["pending_left"] = 1.0
                                    port.send(mido.Message("note_on", note=note, velocity=vel, channel=ch))
                                    rt["note_on"] = note
                                # same note: keep sustaining (no retrigger)
                            rt["gate_left"] = -1.0  # sustain until next non-tie gate
                            rt["tie_prev"] = True
                        else:
                            # Regular gate step
                            same_note = (rt["note_on"] == note)
                            if rt["note_on"] is None:
                                port.send(mido.Message("note_on", note=note, velocity=vel, channel=ch))
                                rt["note_on"] = note
                            else:
                                if same_note:
                                    # Note already held, just update gate length
                                    pass
                                else:
                                    if rt["tie_prev"]:
                                        # From tie: glide overlap with 1 tick
                                        rt["pending_off"] = rt["note_on"]
                                        rt["pending_left"] = 1.0
                                        port.send(mido.Message("note_on", note=note, velocity=vel, channel=ch))
                                        rt["note_on"] = note
                                    else:
                                        # normal retrigger
                                        port.send(mido.Message("note_off", note=rt["note_on"], velocity=0, channel=ch))
                                        port.send(mido.Message("note_on", note=note, velocity=vel, channel=ch))
                                    rt["note_on"] = note

                            gate_percent = int(gate_val) if not isinstance(gate_val, str) else 100
                            rt["gate_left"] = cfg["pulses"] * gate_percent / 100.0
                            rt["tie_prev"] = False

                        # advance step
                        rt["step"] = (rt["step"] + 1) % plen

                    # -------- handle gate countdown & note_off ----------
                    for pname, rt in pattern_state.items():
                        if rt["note_on"] is not None and rt["gate_left"] > 0:
                            rt["gate_left"] -= 1.0
                            if rt["gate_left"] <= 0:
                                port, ch = outputs[pname]
                                port.send(mido.Message("note_off", note=rt["note_on"], velocity=0, channel=ch))
                                rt["note_on"] = None
                                rt["gate_left"] = 0.0
                        # if gate_left == -1 (tie) keep sustaining
                        # pending off overlap handling
                        if rt["pending_off"] is not None:
                            rt["pending_left"] -= 1.0
                            if rt["pending_left"] <= 0:
                                port, ch = outputs[pname]
                                port.send(mido.Message("note_off", note=rt["pending_off"], velocity=0, channel=ch))
                                rt["pending_off"] = None

                    continue  # handled clock

                if msg.type == "start":
                    print("[Transport] START received – immediate first step")
                    for rt in pattern_state.values():
                        rt["tick"] = 0.0
                        rt["step"] = 0
                        rt["rand"] = []
                        rt["rand_vel"] = []

                    # Send first step immediately if chord held
                    if chord_notes:
                        for pname in pattern_cfgs.keys():
                            play_pattern_step(pname)
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
                        for rt in pattern_state.values():
                            rt["tick"] = 0.0
                            rt["step"] = 0
                            rt["rand"] = []
                            rt["rand_vel"] = []
                        continue

                    # When chord starts (was empty) → reset sequence to start on index 0
                    if prev_len == 0 and new_len > 0:
                        for rt in pattern_state.values():
                            rt["tick"] = 0.0
                            rt["step"] = 0
                            rt["rand"] = []
                            rt["rand_vel"] = []
                        # play first step instantly
                        for pname in pattern_cfgs.keys():
                            play_pattern_step(pname)

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
    main() 