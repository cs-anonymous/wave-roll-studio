#!/usr/bin/env python3
"""
MIDI-TSV v0.2: bidirectional conversion between MIDI files and MIDI-TSV text.

Usage:
    python midi_tsv.py midi2tsv <input.mid>        # measure mode; auto-detects downbeats
    python midi_tsv.py tsv2midi <input.tsv>         # creates <input>.mid
    python midi_tsv.py midi2tsv <input.mid> --out <path>
    python midi_tsv.py tsv2midi <input.tsv> --out <path>
    python midi_tsv.py midi2tsv <input.mid> --annotation <annotation.txt>
    python midi_tsv.py midi2tsv <input.mid> --no-auto-downbeat  # segment fallback
"""

import argparse
import csv
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_MICROSECONDS_PER_BEAT = 500_000
STANDARD_TPQ = 50
STANDARD_TICK_SCALE = 1
STANDARD_TICK_MS = 10
STANDARD_TEMPO_MICROSECONDS_PER_BEAT = 500_000
MIN_SLICE_SECONDS = 10
TARGET_SLICE_SECONDS = 20
MAX_SLICE_SECONDS = 30
MIN_GAP_SECONDS = 0.5
PEDAL_VALUE_EPSILON = 3

PITCH_CLASSES = [
    "C", "^C", "D", "^D", "E", "F", "^F", "G", "^G", "A", "^A", "B"
]

NATURAL_PITCH_CLASS: dict[str, int] = {
    "C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11,
}


MAJOR_SCALES = {
    "C": [0,2,4,5,7,9,11], "G": [0,2,4,6,7,9,11], "D": [1,2,4,6,7,9,11],
    "A": [1,2,4,6,8,9,11], "E": [1,3,4,6,8,9,11], "B": [1,3,4,6,8,10,11],
    "F#": [1,3,5,6,8,10,11], "Db": [0,1,3,5,6,8,10], "Ab": [0,1,3,5,7,8,10],
    "Eb": [0,2,3,5,7,8,10], "Bb": [0,2,3,5,7,9,10], "F": [0,2,4,5,7,9,10],
}

KEY_ACCIDENTALS = {
    "C": {}, "G": {6:"^F"}, "D": {6:"^F",1:"^C"},
    "A": {6:"^F",1:"^C",8:"^G"}, "E": {6:"^F",1:"^C",8:"^G",3:"^D"},
    "B": {6:"^F",1:"^C",8:"^G",3:"^D",10:"^A"},
    "F#": {6:"^F",1:"^C",8:"^G",3:"^D",10:"^A",5:"^E"},
    "F": {10:"_B"}, "Bb": {10:"_B",3:"_E"},
    "Eb": {10:"_B",3:"_E",8:"_A"}, "Ab": {10:"_B",3:"_E",8:"_A",1:"_D"},
    "Db": {10:"_B",3:"_E",8:"_A",1:"_D",6:"_G"},
}


# ── MIDI helpers (minimal parser/writer, no external dependency) ───────────

def _read_variable_length(data: bytes, offset: int) -> tuple[int, int]:
    """Read a MIDI variable-length quantity. Returns (value, new_offset)."""
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value, offset


def _write_variable_length(value: int) -> bytes:
    """Write a MIDI variable-length quantity."""
    buf = bytearray([value & 0x7F])
    value >>= 7
    while value:
        buf.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(buf))


def parse_midi(data: bytes):
    """Parse a MIDI file into (tpq, tracks) where each track is a list of
    events: (delta_time, event_type, **kwargs)."""
    # Header
    if data[:4] != b"MThd":
        raise ValueError("Not a MIDI file (missing MThd)")
    header_len = struct.unpack(">I", data[4:8])[0]
    fmt, ntracks, tpq = struct.unpack(">HHH", data[8:14])
    offset = 8 + header_len

    tracks = []
    for _ in range(ntracks):
        if data[offset:offset+4] != b"MTrk":
            raise ValueError(f"Expected MTrk at offset {offset}")
        track_len = struct.unpack(">I", data[offset+4:offset+8])[0]
        offset += 8
        track_data = data[offset:offset+track_len]
        offset += track_len
        tracks.append(_parse_track(track_data))

    return tpq, tracks


def _parse_track(data: bytes) -> list[dict]:
    events = []
    offset = 0
    running_status = 0
    while offset < len(data):
        delta, offset = _read_variable_length(data, offset)
        if offset >= len(data):
            break
        byte = data[offset]
        if byte & 0x80:
            running_status = byte
            offset += 1
        else:
            offset += 0  # running status
        status = running_status
        hi = status >> 4
        lo = status & 0x0F

        if status == 0xFF:  # Meta event
            meta_type = data[offset]
            offset += 1
            length, offset = _read_variable_length(data, offset)
            payload = data[offset:offset+length]
            offset += length
            evt: dict = {"delta": delta, "type": "meta", "meta_type": meta_type}
            if meta_type == 0x51:  # Set tempo
                evt["microseconds_per_beat"] = struct.unpack(">I", b"\x00" + payload)[0]
            elif meta_type == 0x58:  # Time signature
                evt["numerator"] = payload[0]
                evt["denominator"] = 2 ** payload[1]
                evt["metronome"] = payload[2]
                evt["thirtyseconds"] = payload[3]
            elif meta_type == 0x59:  # Key signature
                evt["key"] = payload[0] if payload[0] < 128 else payload[0] - 256
                evt["scale"] = payload[1]
            elif meta_type in (0x06, 0x07):  # Marker / cue point
                evt["text"] = payload.decode("utf-8", errors="replace")
            events.append(evt)
        elif status == 0xF0 or status == 0xF7:  # SysEx
            length, offset = _read_variable_length(data, offset)
            offset += length
        elif hi == 0x9:  # Note on
            note = data[offset]; vel = data[offset+1]; offset += 2
            if vel == 0:
                events.append({"delta": delta, "type": "note_off", "channel": lo,
                               "note": note, "velocity": 0})
            else:
                events.append({"delta": delta, "type": "note_on", "channel": lo,
                               "note": note, "velocity": vel})
        elif hi == 0x8:  # Note off
            note = data[offset]; vel = data[offset+1]; offset += 2
            events.append({"delta": delta, "type": "note_off", "channel": lo,
                           "note": note, "velocity": vel})
        elif hi == 0xB:  # Control change
            cc = data[offset]; val = data[offset+1]; offset += 2
            events.append({"delta": delta, "type": "control_change", "channel": lo,
                           "controller": cc, "value": val})
        elif hi in (0xA, 0xC, 0xD, 0xE):
            if hi == 0xA:  # Aftertouch
                data[offset]; data[offset+1]; offset += 2
            elif hi == 0xC:  # Program change
                data[offset]; offset += 1
            elif hi == 0xD:  # Channel pressure
                data[offset]; offset += 1
            elif hi == 0xE:  # Pitch bend
                data[offset]; data[offset+1]; offset += 2
            # We don't store these, just skip
        else:
            # Unknown, skip
            pass
    return events


def write_midi(tpq: int, tracks: list[list[dict]]) -> bytes:
    """Write a MIDI file from tpq and tracks."""
    buf = bytearray()
    # Header
    buf.extend(b"MThd")
    buf.extend(struct.pack(">I", 6))
    buf.extend(struct.pack(">HHH", 1 if len(tracks) > 1 else 0, len(tracks), tpq))
    # Tracks
    for events in tracks:
        track_data = _encode_track(events)
        buf.extend(b"MTrk")
        buf.extend(struct.pack(">I", len(track_data)))
        buf.extend(track_data)
    return bytes(buf)


def _encode_track(events: list[dict]) -> bytes:
    buf = bytearray()
    for evt in events:
        buf.extend(_write_variable_length(evt["delta"]))
        if evt["type"] == "meta":
            buf.append(0xFF)
            buf.append(evt["meta_type"])
            payload = evt.get("payload", b"")
            buf.extend(_write_variable_length(len(payload)))
            buf.extend(payload)
        elif evt["type"] == "note_on":
            status = 0x90 | evt["channel"]
            buf.append(status)
            buf.append(evt["note"])
            buf.append(evt["velocity"])
        elif evt["type"] == "note_off":
            status = 0x80 | evt["channel"]
            buf.append(status)
            buf.append(evt["note"])
            buf.append(evt["velocity"])
        elif evt["type"] == "control_change":
            status = 0xB0 | evt["channel"]
            buf.append(status)
            buf.append(evt["controller"])
            buf.append(evt["value"])
        else:
            # Skip unknown
            pass
    # End of track
    buf.append(0x00)  # delta=0
    buf.append(0xFF)
    buf.append(0x2F)
    buf.append(0x00)  # length=0
    return bytes(buf)


# ── Pitch conversion ───────────────────────────────────────────────────────

def midi_pitch_to_abc(pitch: int) -> str:
    pitch_class = pitch % 12
    octave = pitch // 12 - 5
    spelled = PITCH_CLASSES[pitch_class]
    accidental = "^" if spelled.startswith("^") else ""
    letter = spelled[1:] if accidental else spelled

    if octave > 0:
        return f"{accidental}{letter.lower()}{'\'' * (octave - 1)}"
    elif octave < 0:
        return f"{accidental}{letter}{',' * (-octave)}"
    return f"{accidental}{letter}"


def detect_key_from_notes(notes: list[dict]) -> str:
    pitch_classes = {n["pitch"] % 12 for n in notes}
    best_key = "C"
    best_score = -1
    for key, scale in MAJOR_SCALES.items():
        score = len(pitch_classes & set(scale))
        if score > best_score:
            best_key = key
            best_score = score
    return best_key


def midi_pitch_to_abc_smart(pitch: int, key: str) -> str:
    pitch_class = pitch % 12
    octave = pitch // 12 - 5
    natural_notes = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
    spelled = KEY_ACCIDENTALS.get(key, {}).get(
        pitch_class,
        natural_notes.get(pitch_class, PITCH_CLASSES[pitch_class]),
    )
    accidental_match = re.match(r"^[_^=]+", spelled)
    accidental = accidental_match.group(0) if accidental_match else ""
    letter = spelled[len(accidental):]

    if octave > 0:
        return f"{accidental}{letter.lower()}{'\'' * (octave - 1)}"
    if octave < 0:
        return f"{accidental}{letter}{',' * (-octave)}"
    return f"{accidental}{letter}"


_ABC_PITCH_RE = re.compile(r"^[_^=]*([A-Ga-g])([',]*)$")


def abc_pitch_to_midi(pitch: str) -> int:
    match = _ABC_PITCH_RE.match(pitch)
    if not match:
        raise ValueError(f"Invalid ABC pitch: {pitch!r}")
    letter_raw = match.group(1)
    suffix = match.group(2)
    accidentals = pitch[:pitch.index(letter_raw)]

    letter = letter_raw.upper()
    midi = (60 if letter_raw == letter else 72) + NATURAL_PITCH_CLASS[letter]

    for ch in accidentals:
        if ch == "^":
            midi += 1
        elif ch == "_":
            midi -= 1

    for ch in suffix:
        midi += 12 if ch == "'" else -12

    if not (0 <= midi <= 127):
        raise ValueError(f"Pitch out of range: {pitch!r} -> {midi}")
    return midi


# ── Annotation parsing ─────────────────────────────────────────────────────

class DownbeatDetectionError(RuntimeError):
    """Raised when automatic performance-MIDI downbeat detection fails."""


def parse_annotation_file(annotation_path: str) -> dict:
    """Parse annotation file and return downbeats, beats, and time signature info.

    Returns:
        {
            "downbeats": [(time_seconds, time_signature), ...],
            "beats": [time_seconds, ...],
        }
    """
    downbeats = []
    beats = []

    with open(annotation_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('\t')

            # Handle both formats:
            # Format 1: line_num \t time \t time \t label
            # Format 2: time \t time \t label
            if len(parts) >= 3:
                # Try to determine format by checking if first field is a number (line number)
                try:
                    int(parts[0])
                    # Format 1: has line number
                    if len(parts) >= 4:
                        time_sec = float(parts[1])
                        label = parts[3]
                    else:
                        continue
                except ValueError:
                    # Format 2: no line number, first field is time
                    time_sec = float(parts[0])
                    label = parts[2]

                if label.startswith('db'):
                    # Parse time signature if present (e.g., "db,2/4,0")
                    time_sig = None
                    if ',' in label:
                        sig_parts = label.split(',')
                        if len(sig_parts) >= 2:
                            time_sig = sig_parts[1]  # e.g., "2/4"
                    downbeats.append((time_sec, time_sig))
                elif label == 'b':
                    beats.append(time_sec)

    return {
        "downbeats": downbeats,
        "beats": beats,
    }


def detect_annotation_with_omnizart(
    midi_data: bytes,
    source: str = "unknown.mid",
    midi_path: str | Path | None = None,
) -> dict:
    """Predict beat/downbeat annotations from a performance MIDI with Omnizart.

    Omnizart's beat module writes *_beat.csv and *_down_beat.csv files whose
    values are in seconds. We convert those into the same in-memory annotation
    shape as parse_annotation_file(), so downstream measure slicing stays
    identical for human and predicted annotations.
    """
    with tempfile.TemporaryDirectory(prefix="midi_tsv_omnizart_") as tmp:
        tmp_dir = Path(tmp)
        input_path = Path(midi_path) if midi_path else tmp_dir / _safe_midi_filename(source)
        if not midi_path:
            input_path.write_bytes(midi_data)

        output_dir = tmp_dir / "out"
        output_dir.mkdir()

        _run_omnizart(input_path, output_dir)

        downbeat_file = _find_omnizart_csv(output_dir, downbeat=True)
        beat_file = _find_omnizart_csv(output_dir, downbeat=False)

        downbeats = _read_time_csv(downbeat_file) if downbeat_file else []
        beats = _read_time_csv(beat_file) if beat_file else []

    if not downbeats:
        raise DownbeatDetectionError(
            "Omnizart did not produce any downbeats; cannot create measure-mode MIDI-TSV"
        )

    return {
        "downbeats": [(t, None) for t in downbeats],
        "beats": beats,
    }


def _run_omnizart(input_path: Path, output_dir: Path) -> None:
    executable = shutil.which("omnizart")
    errors = []

    if executable:
        cmd = [executable, "beat", "transcribe", "-o", str(output_dir), str(input_path)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return
        except subprocess.CalledProcessError as exc:
            errors.append(_format_subprocess_error(cmd, exc))

    try:
        from omnizart.beat.app import BeatTranscription
    except Exception as exc:
        errors.append(f"Python import failed: {type(exc).__name__}: {exc}")
    else:
        try:
            BeatTranscription().transcribe(str(input_path), output=str(output_dir))
            return
        except Exception as exc:
            errors.append(f"Python API failed: {type(exc).__name__}: {exc}")

    detail = "\n".join(errors) if errors else "omnizart executable/module not found"
    raise DownbeatDetectionError(
        "Automatic downbeat detection requires Omnizart beat module.\n"
        "Install it in the Python environment used by midi_tsv.py, then retry.\n"
        f"{detail}"
    )


def _format_subprocess_error(cmd: list[str], exc: subprocess.CalledProcessError) -> str:
    stderr = (exc.stderr or "").strip()
    stdout = (exc.stdout or "").strip()
    output = stderr or stdout or "(no output)"
    return f"Command failed ({' '.join(cmd)}): {output}"


def _find_omnizart_csv(output_dir: Path, downbeat: bool) -> Path | None:
    csv_files = sorted(output_dir.rglob("*.csv"))
    if downbeat:
        matches = [
            p for p in csv_files
            if "down" in p.stem.lower() and "beat" in p.stem.lower()
        ]
    else:
        matches = [
            p for p in csv_files
            if "beat" in p.stem.lower() and "down" not in p.stem.lower()
        ]
    return matches[0] if matches else None


def _read_time_csv(path: Path) -> list[float]:
    times = []
    with path.open(newline="") as f:
        for row in csv.reader(f):
            for cell in row:
                try:
                    value = float(cell.strip())
                except ValueError:
                    continue
                if value >= 0:
                    times.append(value)
                    break
    return sorted(set(times))


def _safe_midi_filename(source: str) -> str:
    name = Path(source).name or "input.mid"
    if not name.lower().endswith((".mid", ".midi")):
        name += ".mid"
    return name


def create_measures_from_annotation(annotation_data: dict, tempo_map: list[dict]) -> list[dict]:
    """Create measure slices from annotation downbeats.

    Returns list of measures: [{"id": 1, "start": tick, "end": tick, "time_sig": "4/4"}, ...]
    """
    downbeats = annotation_data["downbeats"]
    if not downbeats:
        return []

    measures = []
    for i, (time_sec, time_sig) in enumerate(downbeats):
        start_tick = _seconds_to_standard_tick(time_sec, tempo_map)

        # End tick is the start of next measure, or we'll set it later
        if i + 1 < len(downbeats):
            end_tick = _seconds_to_standard_tick(downbeats[i + 1][0], tempo_map)
        else:
            end_tick = None  # Will be set to end of piece

        measures.append({
            "id": i + 1,
            "start": start_tick,
            "end": end_tick,
            "time_sig": time_sig,
        })

    return measures


def _seconds_to_standard_tick(seconds: float, tempo_map: list[dict]) -> int:
    """Convert seconds to standard ticks using tempo map."""
    # Find the tempo point that applies at this time
    selected = tempo_map[0]
    for point in tempo_map:
        if point["seconds"] <= seconds:
            selected = point
        else:
            break

    # Calculate ticks from the selected tempo point
    delta_seconds = seconds - selected["seconds"]
    delta_ticks_original = (delta_seconds * 1_000_000 * selected["tpq"]) / selected["microseconds_per_beat"]
    tick_original = selected["tick"] + delta_ticks_original

    # Convert to standard ticks
    return original_tick_to_standard_tick(int(tick_original), tempo_map)


# ── Slicing ────────────────────────────────────────────────────────────────

def create_slices(notes: list[dict], pedals: list[dict], end_tick: int) -> list[dict]:
    if end_tick <= 0:
        return [{"id": 1, "start": 0, "end": 0}]

    tick_seconds = STANDARD_TICK_MS / 1000
    min_ticks = max(1, round(MIN_SLICE_SECONDS / tick_seconds))
    target_ticks = max(min_ticks, round(TARGET_SLICE_SECONDS / tick_seconds))
    max_ticks = max(target_ticks, round(MAX_SLICE_SECONDS / tick_seconds))
    min_gap_ticks = max(1, round(MIN_GAP_SECONDS / tick_seconds))

    cut_candidates = _find_weak_cut_candidates(notes, pedals, min_gap_ticks)

    slices = []
    start_tick = 0
    while end_tick - start_tick > max_ticks:
        min_cut = start_tick + min_ticks
        max_cut = min(start_tick + max_ticks, end_tick)
        target_cut = min(start_tick + target_ticks, max_cut)
        candidates = [c for c in cut_candidates if min_cut < c < max_cut]
        cut = min(candidates, key=lambda c: abs(c - target_cut)) if candidates else target_cut
        slices.append({
            "id": len(slices) + 1,
            "start": start_tick,
            "end": cut,
        })
        start_tick = cut

    slices.append({
        "id": len(slices) + 1,
        "start": start_tick,
        "end": end_tick,
    })
    return slices


def _find_weak_cut_candidates(notes: list[dict], pedals: list[dict], min_gap_ticks: int) -> list[int]:
    """Find candidate cut points where there's a strong sense of ending.

    A good cut point should have:
    1. No notes currently sounding (all notes have ended)
    2. A gap before the next note starts
    3. Preferably after pedal release
    """
    # Build intervals for when notes are sounding
    note_intervals = []
    for n in notes:
        s = n["t"]
        e = n["t"] + n["dur"]
        if e > s:
            note_intervals.append((s, e))

    # Build intervals for when pedal is down (sustaining sound)
    pedal_intervals = []
    pedal_down_tick = None
    for p in sorted(pedals, key=lambda x: x["t"]):
        if p["val"] >= 64 and pedal_down_tick is None:
            pedal_down_tick = p["t"]
        elif p["val"] < 64 and pedal_down_tick is not None:
            pedal_intervals.append((pedal_down_tick, p["t"]))
            pedal_down_tick = None

    # Combine note and pedal intervals to find when sound is active
    all_intervals = note_intervals + pedal_intervals
    all_intervals.sort()

    # Merge overlapping intervals
    merged = []
    for s, e in all_intervals:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)

    # Find gaps between merged intervals (silence periods)
    cuts = []
    for i in range(1, len(merged)):
        gap_start = merged[i-1][1]
        gap_end = merged[i][0]
        gap = gap_end - gap_start

        if gap >= min_gap_ticks:
            # Place cut in the middle of the silence
            cuts.append(round((gap_start + gap_end) / 2))

    # Also consider note ending points as potential cuts
    # These are points where all currently sounding notes have ended
    note_end_times = sorted(set(n["t"] + n["dur"] for n in notes if n["dur"] > 0))
    note_start_times = sorted(set(n["t"] for n in notes))

    for end_time in note_end_times:
        # Check if there's a gap after this note ends
        next_starts = [t for t in note_start_times if t > end_time]
        if next_starts:
            next_start = next_starts[0]
            if next_start - end_time >= min_gap_ticks:
                # Good cut point: notes ended and there's a gap
                cuts.append(round((end_time + next_start) / 2))

    return sorted(set(cuts))


def _find_slice_local_start(notes, pedals, markers, slice_, slices) -> int:
    """Find the local start for a slice.

    For measure-based slicing (M), always start from the slice start (measure beginning).
    For segment-based slicing (S), find the first event.
    """
    # For the first slice, always start from 0 to include pre-measure events
    if slice_["id"] == 1:
        return slice_["start"]

    first = float("inf")
    is_last = slice_["id"] == len(slices)
    for item in [*notes, *pedals, *markers]:
        if item["t"] >= slice_["start"] and (item["t"] < slice_["end"] or is_last):
            first = min(first, item["t"])
    return int(first) if first != float("inf") else slice_["start"]


def quantize_pedal_events(pedals: list[dict], epsilon: int = PEDAL_VALUE_EPSILON) -> list[dict]:
    """First pass: remove pedal events with small value changes."""
    last_by_type: dict[str, dict] = {}
    result = []
    for pedal in pedals:
        previous = last_by_type.get(pedal["type"])
        if previous is not None and abs(pedal["val"] - previous["val"]) <= epsilon:
            continue
        result.append(pedal)
        last_by_type[pedal["type"]] = pedal
    return result


def smart_quantize_pedals_between_notes(pedals: list[dict], notes: list[dict], measures: list[dict] = None) -> list[dict]:
    """Smart pedal quantization per segment within measures.

    Within each measure, divide by note events:
    - Measure start → first note: up to 5 pedals per type
    - Note i → Note i+1: up to 5 pedals per type
    - Last note → measure end: up to 5 pedals per type

    Each segment: keep first, last, and up to 3 representative peaks/valleys.

    If no measures provided, falls back to global quantization.
    """
    if not pedals:
        return []

    # First pass: epsilon filtering
    filtered = quantize_pedal_events(pedals, PEDAL_VALUE_EPSILON)

    # Group pedals by type
    pedals_by_type: dict[str, list[dict]] = {}
    for pedal in filtered:
        pedal_type = pedal["type"]
        if pedal_type not in pedals_by_type:
            pedals_by_type[pedal_type] = []
        pedals_by_type[pedal_type].append(pedal)

    result = []

    if not measures:
        # Fallback: global quantization
        for pedal_type, type_pedals in pedals_by_type.items():
            type_pedals = sorted(type_pedals, key=lambda p: p["t"])
            if len(type_pedals) <= 5:
                result.extend(type_pedals)
                continue
            quantized = [type_pedals[0]]
            middle = type_pedals[1:-1]
            if middle:
                peaks = _find_pedal_peaks(middle, max_peaks=3)
                quantized.extend(peaks)
            quantized.append(type_pedals[-1])
            result.extend(quantized)
        return sorted(result, key=lambda p: p["t"])

    # Per-measure, per-segment quantization
    sorted_notes = sorted(notes, key=lambda n: n["t"])

    for measure in measures:
        m_start = measure["start"]
        m_end = measure["end"]

        # Get notes within this measure
        measure_notes = [n for n in sorted_notes if m_start <= n["t"] < m_end]
        note_times = [n["t"] for n in measure_notes]

        # Build segment boundaries:
        # For first measure: 0 → measure_start → note_times → measure_end
        # For others: measure_start → note_times → measure_end
        if measure["id"] == 1:
            boundaries = [0, m_start] + note_times + [m_end]
        else:
            boundaries = [m_start] + note_times + [m_end]

        for pedal_type, type_pedals in pedals_by_type.items():
            type_pedals_sorted = sorted(type_pedals, key=lambda p: p["t"])

            # Process each segment
            for seg_idx in range(len(boundaries) - 1):
                seg_start = boundaries[seg_idx]
                seg_end = boundaries[seg_idx + 1]

                # Find pedals in this segment
                segment_pedals = [p for p in type_pedals_sorted if seg_start <= p["t"] < seg_end]

                # Quantize if more than 5
                if len(segment_pedals) <= 5:
                    result.extend(segment_pedals)
                else:
                    quantized = [segment_pedals[0]]
                    middle = segment_pedals[1:-1]
                    if middle:
                        peaks = _find_pedal_peaks(middle, max_peaks=3)
                        quantized.extend(peaks)
                    quantized.append(segment_pedals[-1])
                    result.extend(quantized)

    # Deduplicate
    seen = set()
    deduped = []
    for p in result:
        key = (p["t"], p["type"])
        if key not in seen:
            seen.add(key)
            deduped.append(p)

    return sorted(deduped, key=lambda p: p["t"])



def _find_pedal_peaks(pedals: list[dict], max_peaks: int = 3) -> list[dict]:
    """Find representative peaks/valleys in a pedal curve.

    Returns at most max_peaks points that best represent the curve shape.
    """
    if len(pedals) <= max_peaks:
        return pedals

    # Calculate importance score for each point based on local extrema
    scores = []
    for i, pedal in enumerate(pedals):
        if i == 0 or i == len(pedals) - 1:
            scores.append((0, pedal))  # Endpoints handled separately
            continue

        prev_val = pedals[i - 1]["val"]
        curr_val = pedal["val"]
        next_val = pedals[i + 1]["val"]

        # Score based on how much this point deviates from linear interpolation
        expected_val = (prev_val + next_val) / 2
        deviation = abs(curr_val - expected_val)

        # Also consider if it's a local extremum
        is_peak = (curr_val > prev_val and curr_val > next_val)
        is_valley = (curr_val < prev_val and curr_val < next_val)
        extremum_bonus = 20 if (is_peak or is_valley) else 0

        score = deviation + extremum_bonus
        scores.append((score, pedal))

    # Sort by score and take top max_peaks
    scores.sort(key=lambda x: x[0], reverse=True)
    selected = [pedal for _, pedal in scores[:max_peaks]]

    # Return in time order
    return sorted(selected, key=lambda p: p["t"])


# ── Scale helpers ──────────────────────────────────────────────────────────

def scale_tick(tick: int, tick_scale: int) -> int:
    return round(tick / tick_scale)


def scale_duration(dur: int, tick_scale: int) -> int:
    return max(1, scale_tick(dur, tick_scale)) if dur > 0 else 0


def build_original_tempo_map(tpq: int, tempos: list[dict]) -> list[dict]:
    sorted_tempos = sorted(tempos, key=lambda t: t["tick"])
    deduped = []
    for tempo in sorted_tempos:
        if not deduped or tempo["tick"] != deduped[-1]["tick"]:
            deduped.append(tempo)
    if not deduped or deduped[0]["tick"] != 0:
        deduped.insert(0, {"tick": 0, "microseconds_per_beat": DEFAULT_MICROSECONDS_PER_BEAT})

    seconds = 0.0
    previous = deduped[0]
    points = [{
        "tick": previous["tick"],
        "seconds": 0.0,
        "microseconds_per_beat": previous["microseconds_per_beat"],
        "tpq": tpq,
    }]
    for tempo in deduped[1:]:
        seconds += ((tempo["tick"] - previous["tick"]) * previous["microseconds_per_beat"]) / tpq / 1_000_000
        points.append({
            "tick": tempo["tick"],
            "seconds": seconds,
            "microseconds_per_beat": tempo["microseconds_per_beat"],
            "tpq": tpq,
        })
        previous = tempo
    return points


def original_tick_to_standard_tick(tick: int, tempo_map: list[dict]) -> int:
    selected = tempo_map[0]
    for point in tempo_map:
        if point["tick"] <= tick:
            selected = point
        else:
            break
    seconds = selected["seconds"] + (
        (tick - selected["tick"]) * selected["microseconds_per_beat"]
    ) / selected["tpq"] / 1_000_000
    return round((seconds * 1000) / STANDARD_TICK_MS)


def bake_notes_to_standard_ticks(notes: list[dict], tempo_map: list[dict]) -> list[dict]:
    baked = []
    for note in notes:
        start = original_tick_to_standard_tick(note["t"], tempo_map)
        end = original_tick_to_standard_tick(note["t"] + note["dur"], tempo_map)
        item = dict(note)
        item["t"] = start
        item["dur"] = max(1, end - start) if note["dur"] > 0 else 0
        baked.append(item)
    return baked


def bake_timed_records_to_standard_ticks(records: list[dict], tempo_map: list[dict]) -> list[dict]:
    baked = []
    for record in records:
        item = dict(record)
        item["t"] = original_tick_to_standard_tick(record["t"], tempo_map)
        baked.append(item)
    return baked


# ── MIDI → TSV ─────────────────────────────────────────────────────────────

def midi_to_tsv(
    data: bytes,
    source: str = "unknown.mid",
    annotation_path: str | None = None,
    auto_downbeat: bool = True,
    midi_path: str | Path | None = None,
) -> str:
    tpq, raw_tracks = parse_midi(data)
    if not tpq:
        raise ValueError("Only tick-based MIDI files are supported")

    notes: list[dict] = []
    pedals: list[dict] = []
    tempos: list[dict] = []
    time_sigs: list[dict] = []
    key_sigs: list[dict] = []
    markers: list[dict] = []
    default_channel = 0
    end_tick = 0

    for events in raw_tracks:
        tick = 0
        open_notes: dict[str, list[dict]] = defaultdict(list)

        for evt in events:
            tick += evt["delta"]
            end_tick = max(end_tick, tick)

            etype = evt["type"]
            if etype == "meta":
                mt = evt.get("meta_type", 0)
                if mt == 0x51:
                    tempos.append({"tick": tick, "microseconds_per_beat": evt["microseconds_per_beat"]})
                elif mt == 0x58:
                    time_sigs.append({"tick": tick, "numerator": evt["numerator"],
                                      "denominator": evt["denominator"],
                                      "metronome": evt["metronome"],
                                      "thirtyseconds": evt["thirtyseconds"]})
                elif mt == 0x59:
                    key_sigs.append({"tick": tick, "key": evt["key"], "scale": evt["scale"]})
                elif mt in (0x06, 0x07):
                    markers.append({"t": tick, "text": evt.get("text", "")})
            elif etype == "note_on":
                ch = evt["channel"]
                default_channel = ch
                key = f"{ch}:{evt['note']}"
                open_notes[key].append({
                    "t": tick, "dur": 0, "pitch": evt["note"], "vel": evt["velocity"], "channel": ch,
                })
            elif etype == "note_off":
                ch = evt["channel"]
                default_channel = ch
                key = f"{ch}:{evt['note']}"
                if open_notes[key]:
                    note = open_notes[key].pop(0)
                    note["dur"] = max(0, tick - note["t"])
                    notes.append(note)
                if not open_notes[key]:
                    del open_notes[key]
            elif etype == "control_change" and evt["controller"] in (64, 67, 66, 11):
                ch = evt["channel"]
                default_channel = ch
                cc_map = {64: "P", 67: "P1", 66: "P2", 11: "P3"}
                pedals.append({"type": cc_map[evt["controller"]], "t": tick, "val": evt["value"], "channel": ch})

        # Close unclosed notes
        for queue in open_notes.values():
            for note in queue:
                note["dur"] = max(0, end_tick - note["t"])
                notes.append(note)

    for note in notes:
        end_tick = max(end_tick, note["t"] + note["dur"])

    # Sort
    notes.sort(key=lambda n: n["t"])
    pedals.sort(key=lambda p: p["t"])
    markers.sort(key=lambda m: m["t"])

    tempo_map = build_original_tempo_map(tpq, tempos)
    notes = bake_notes_to_standard_ticks(notes, tempo_map)
    baked_pedals = bake_timed_records_to_standard_ticks(pedals, tempo_map)
    markers = bake_timed_records_to_standard_ticks(markers, tempo_map)
    time_sigs = [
        {**sig, "tick": original_tick_to_standard_tick(sig["tick"], tempo_map)}
        for sig in time_sigs
    ]
    key_sigs = [
        {**ks, "tick": original_tick_to_standard_tick(ks["tick"], tempo_map)}
        for ks in key_sigs
    ]
    end_tick = max(
        [original_tick_to_standard_tick(end_tick, tempo_map)]
        + [n["t"] + n["dur"] for n in notes]
        + [p["t"] for p in baked_pedals]
        + [m["t"] for m in markers]
    )

    # Determine slice type and create slices/measures.
    # Default MIDI->TSV conversion is measure mode: use human annotations when
    # provided, otherwise predict performance downbeats with Omnizart.
    annotation_source = None
    if annotation_path:
        annotation_data = parse_annotation_file(annotation_path)
        annotation_source = "annotation"
    elif auto_downbeat:
        annotation_data = detect_annotation_with_omnizart(data, source=source, midi_path=midi_path)
        annotation_source = "omnizart"
    else:
        annotation_data = None

    if annotation_data:
        slices = create_measures_from_annotation(annotation_data, tempo_map)
        if not slices:
            raise ValueError("No downbeats found; cannot create measure-mode MIDI-TSV")
        # Set end tick for last measure
        slices[-1]["end"] = end_tick
        slice_type = "measure"
        slice_prefix = "M"
    else:
        slices = create_slices(notes, baked_pedals, end_tick)
        slice_type = "segment"
        slice_prefix = "S"

    # Apply smart quantization per measure/segment
    pedals = smart_quantize_pedals_between_notes(baked_pedals, notes, slices)

    detected_key = detect_key_from_notes(notes)

    # Build output
    lines: list[str] = [
        "# midi-tsv v0.2",
        f"# source={source}",
        f"# slice_type={slice_type}",
        *([f"# annotation_source={annotation_source}"] if annotation_source else []),
        "# unit=tick",
        f"# tick_scale={STANDARD_TICK_SCALE}",
        f"# tpq={STANDARD_TPQ}",
        f"# tick_ms={STANDARD_TICK_MS}",
        "# pitch=abc-absolute",
        f"# detected_key={detected_key}",
        f"# channel={default_channel}",
        f"# tempo=0,{STANDARD_TEMPO_MICROSECONDS_PER_BEAT}",
    ]

    for sig in time_sigs:
        lines.append(
            f"# time_signature={sig['tick']},"
            f"{sig['numerator']},{sig['denominator']},{sig['metronome']},{sig['thirtyseconds']}"
        )
    for ks in key_sigs:
        lines.append(f"# key_signature={ks['tick']},{ks['key']},{ks['scale']}")

    lines.append("")

    for sl in slices:
        local_start = _find_slice_local_start(notes, pedals, markers, sl, slices)

        lines.append(f"{slice_prefix}{sl['id']}\t{local_start}\t{sl['end']}")

        records = []
        is_last = sl["id"] == len(slices)

        # For notes, use local_start as boundary
        slice_notes = [n for n in notes if n["t"] >= local_start and (n["t"] < sl["end"] or is_last)]
        slice_key = detect_key_from_notes(slice_notes) if slice_notes else detected_key
        for n in slice_notes:
            offset = max(0, n["t"] - local_start)
            records.append({
                "t": n["t"],
                "order": 1,
                "line": f"{midi_pitch_to_abc_smart(n['pitch'], slice_key)}:{n['dur']}\t{offset}\t{n['vel']}",
            })

        # For pedals and markers within the slice (include pre-measure events for first slice)
        for p in pedals:
            if p["t"] < sl["start"] and sl["id"] == 1:
                # Pre-measure pedal: show at offset 0
                records.append({
                    "t": p["t"],
                    "order": 0,
                    "line": f"{p['type']}\t0\t{p['val']}",
                })
            elif p["t"] >= local_start and (p["t"] < sl["end"] or is_last):
                records.append({
                    "t": p["t"],
                    "order": 0,
                    "line": f"{p['type']}\t{p['t'] - local_start}\t{p['val']}",
                })

        for m in markers:
            if m["t"] < sl["start"] and sl["id"] == 1:
                records.append({
                    "t": m["t"],
                    "order": 2,
                    "line": f"M\t0\t{json.dumps(m['text'])}",
                })
            elif m["t"] >= local_start and (m["t"] < sl["end"] or is_last):
                records.append({
                    "t": m["t"],
                    "order": 2,
                    "line": f"M\t{m['t'] - local_start}\t{json.dumps(m['text'])}",
                })

        records.sort(key=lambda r: (r["t"], r["order"]))
        for r in records:
            lines.append(r["line"])
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ── TSV → MIDI ─────────────────────────────────────────────────────────────

def tsv_to_midi(tsv: str) -> bytes:
    meta = _parse_tsv_meta(tsv)
    if meta.get("version") == "v0.2":
        return _tsv_v2_to_midi(tsv, meta)
    return _tsv_v1_to_midi(tsv, meta)


def _tsv_v2_to_midi(tsv: str, meta: dict) -> bytes:
    events: list[dict] = []
    current_slice_start = 0
    channel = meta.get("channel", 0)
    slice_type = meta.get("slice_type", "segment")
    slice_prefix = "M" if slice_type == "measure" else "S"

    for line_idx, raw_line in enumerate(tsv.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        fields = raw_line.split("\t")
        record_type = fields[0]

        if _is_slice_record(record_type, slice_prefix):
            _require(fields, 3, line_idx)
            current_slice_start = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
        elif _is_note_record(record_type):
            _require(fields, 3, line_idx)
            pitch_abc, dur_macro = _parse_note_record(record_type, line_idx)
            local_t = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
            dur = dur_macro * meta["tick_scale"]
            pitch = abc_pitch_to_midi(pitch_abc)
            vel = _parse_midi_value(fields[2], line_idx)
            on_tick = current_slice_start + local_t
            events.append({
                "tick": on_tick, "order": 3,
                "type": "note_on", "channel": channel, "note": pitch, "velocity": vel,
            })
            events.append({
                "tick": on_tick + dur, "order": 2,
                "type": "note_off", "channel": channel, "note": pitch, "velocity": 0,
            })
        elif record_type in ("P", "P1", "P2", "P3"):
            _require(fields, 3, line_idx)
            local_t = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
            val = _parse_midi_value(fields[2], line_idx)
            cc_map = {"P": 64, "P1": 67, "P2": 66, "P3": 11}
            events.append({
                "tick": current_slice_start + local_t, "order": 1,
                "type": "control_change", "channel": channel,
                "controller": cc_map[record_type], "value": val,
            })
        elif record_type == "M":
            _require(fields, 3, line_idx)
            local_t = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
            text = _parse_marker_text(fields[2])
            events.append({
                "tick": current_slice_start + local_t, "order": 0,
                "type": "meta", "meta_type": 0x06,
                "payload": text.encode("utf-8"),
            })
        else:
            raise ValueError(f"Line {line_idx+1}: unknown record type {record_type!r}")

    _append_meta_events(events, meta)
    events = sorted(events, key=lambda e: (e["tick"], e["order"]))
    return write_midi(meta["tpq"], [_to_delta_events(events)])


def _tsv_v1_to_midi(tsv: str, meta: dict) -> bytes:
    track_events: dict[int, list[dict]] = defaultdict(list)
    current_slice_start = 0
    current_track_id: int | None = None

    for line_idx, raw_line in enumerate(tsv.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        fields = raw_line.split("\t")
        record_type = fields[0]

        if record_type == "S":
            _require(fields, 4, line_idx)
            current_slice_start = _parse_non_negative(fields[2], line_idx) * meta["tick_scale"]
        elif record_type == "T":
            _require(fields, 2, line_idx)
            current_track_id = _parse_positive(fields[1], line_idx)
        elif _is_abc_pitch(record_type):
            if current_track_id is None:
                raise ValueError(f"Line {line_idx+1}: note before T record")
            _require(fields, 4, line_idx)
            local_t = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
            dur = _parse_non_negative(fields[2], line_idx) * meta["tick_scale"]
            pitch = abc_pitch_to_midi(fields[0])
            vel = _parse_midi_value(fields[3], line_idx)
            channel = meta["track_channels"].get(current_track_id, 0)
            on_tick = current_slice_start + local_t
            track_events[current_track_id].append({
                "tick": on_tick, "order": 3,
                "type": "note_on", "channel": channel, "note": pitch, "velocity": vel,
            })
            track_events[current_track_id].append({
                "tick": on_tick + dur, "order": 2,
                "type": "note_off", "channel": channel, "note": pitch, "velocity": 0,
            })
        elif record_type == "P":
            if current_track_id is None:
                raise ValueError(f"Line {line_idx+1}: P before T record")
            _require(fields, 3, line_idx)
            local_t = _parse_non_negative(fields[1], line_idx) * meta["tick_scale"]
            val = _parse_midi_value(fields[2], line_idx)
            channel = meta["track_channels"].get(current_track_id, 0)
            track_events[current_track_id].append({
                "tick": current_slice_start + local_t, "order": 1,
                "type": "control_change", "channel": channel, "controller": 64, "value": val,
            })
        else:
            raise ValueError(f"Line {line_idx+1}: unknown record type {record_type!r}")

    # Inject meta events into track 1
    _append_meta_events(track_events[1], meta)

    # Build tracks
    max_track_id = max(1, *track_events.keys())
    midi_tracks = []
    for tid in range(1, max_track_id + 1):
        events = sorted(track_events.get(tid, []), key=lambda e: (e["tick"], e["order"]))
        delta_events = _to_delta_events(events)
        midi_tracks.append(delta_events)

    return write_midi(meta["tpq"], midi_tracks)


def _append_meta_events(events: list[dict], meta: dict) -> None:
    tempos = meta["tempos"] or [{
        "tick": 0,
        "microseconds_per_beat": STANDARD_TEMPO_MICROSECONDS_PER_BEAT,
    }]
    for t in tempos:
        payload = struct.pack(">I", t["microseconds_per_beat"])[1:]  # 3 bytes
        events.append({
            "tick": t["tick"], "order": 0,
            "type": "meta", "meta_type": 0x51,
            "payload": payload,
        })
    for sig in meta["time_signatures"]:
        payload = struct.pack("BBBB", sig["numerator"],
                              {1:0, 2:1, 4:2, 8:3, 16:4, 32:5}.get(sig["denominator"], 2),
                              sig["metronome"], sig["thirtyseconds"])
        events.append({
            "tick": sig["tick"], "order": 0,
            "type": "meta", "meta_type": 0x58,
            "payload": payload,
        })
    for ks in meta["key_signatures"]:
        key_byte = ks["key"] if ks["key"] >= 0 else ks["key"] + 256
        payload = struct.pack("BB", key_byte, ks["scale"])
        events.append({
            "tick": ks["tick"], "order": 0,
            "type": "meta", "meta_type": 0x59,
            "payload": payload,
        })


def _to_delta_events(events: list[dict]) -> list[dict]:
    result = []
    prev_tick = 0
    for e in events:
        delta = e["tick"] - prev_tick
        prev_tick = e["tick"]
        result.append({"delta": delta, **{k: v for k, v in e.items() if k != "tick" and k != "order"}})
    return result


def _parse_tsv_meta(tsv: str) -> dict:
    meta = {
        "version": "v0.2", "tpq": STANDARD_TPQ, "tick_scale": 1,
        "tempos": [], "time_signatures": [], "key_signatures": [],
        "track_channels": {}, "channel": 0, "slice_type": "segment",
    }
    for raw_line in tsv.splitlines():
        line = raw_line.strip()
        if not line.startswith("#"):
            continue
        version_match = re.match(r"#\s*midi-tsv\s+(v\d+\.\d+)", line)
        if version_match:
            meta["version"] = version_match.group(1)
            continue
        body = line[1:].strip()
        idx = body.find("=")
        if idx == -1:
            continue
        key = body[:idx].strip()
        val = body[idx+1:].strip()

        if key == "tpq":
            meta["tpq"] = int(val)
        elif key == "tick_scale":
            meta["tick_scale"] = int(val)
        elif key == "channel":
            meta["channel"] = int(val)
        elif key == "slice_type":
            meta["slice_type"] = val
        elif key == "tempo":
            parts = val.split(",")
            tick = int(parts[0]) * meta["tick_scale"]
            mpb = int(parts[1])
            meta["tempos"].append({"tick": tick, "microseconds_per_beat": mpb})
        elif key == "time_signature":
            parts = val.split(",")
            meta["time_signatures"].append({
                "tick": int(parts[0]) * meta["tick_scale"],
                "numerator": int(parts[1]), "denominator": int(parts[2]),
                "metronome": int(parts[3]), "thirtyseconds": int(parts[4]),
            })
        elif key == "key_signature":
            parts = val.split(",")
            meta["key_signatures"].append({
                "tick": int(parts[0]) * meta["tick_scale"],
                "key": int(parts[1]), "scale": int(parts[2]),
            })
        elif key == "track_channel":
            for item in val.split(","):
                m = re.match(r"T(\d+):(\d+)$", item)
                if m:
                    meta["track_channels"][int(m.group(1))] = int(m.group(2))
    return meta


def _is_abc_pitch(s: str) -> bool:
    return bool(re.match(r"^[_^=]*[A-Ga-g]['|,]*$", s))


def _is_slice_record(s: str, prefix: str = None) -> bool:
    if prefix:
        return bool(re.match(rf"^{prefix}\d+$", s))
    return bool(re.match(r"^[SM]\d+$", s))


def _is_note_record(s: str) -> bool:
    return bool(re.match(r"^[_^=]*[A-Ga-g]['|,]*:?\d+$", s))


def _parse_note_record(s: str, line_idx: int) -> tuple[str, int]:
    match = re.match(r"^([_^=]*[A-Ga-g]['|,]*):?(\d+)$", s)
    if not match:
        raise ValueError(f"Line {line_idx+1}: invalid note record {s!r}")
    return match.group(1), _parse_non_negative(match.group(2), line_idx)


def _parse_marker_text(s: str) -> str:
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, str) else str(parsed)
    except json.JSONDecodeError:
        return s


def _require(fields: list[str], expected: int, line_idx: int):
    if len(fields) != expected:
        raise ValueError(f"Line {line_idx+1}: expected {expected} fields, got {len(fields)}")


def _parse_non_negative(s: str, line_idx: int) -> int:
    v = int(s)
    if v < 0:
        raise ValueError(f"Line {line_idx+1}: must be non-negative, got {v}")
    return v


def _parse_positive(s: str, line_idx: int) -> int:
    v = int(s)
    if v <= 0:
        raise ValueError(f"Line {line_idx+1}: must be positive, got {v}")
    return v


def _parse_midi_value(s: str, line_idx: int) -> int:
    v = int(s)
    if not (0 <= v <= 127):
        raise ValueError(f"Line {line_idx+1}: must be 0-127, got {v}")
    return v


# ── CLI ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MIDI-TSV converter")
    sub = parser.add_subparsers(dest="command", required=True)

    p2t = sub.add_parser("midi2tsv", help="MIDI → TSV")
    p2t.add_argument("input", help="Input .mid file")
    p2t.add_argument("--out", "-o", help="Output .tsv file (default: input + '.tsv')")
    p2t.add_argument("--annotation", "-a", help="Annotation file for measure-based slicing")
    p2t.add_argument(
        "--no-auto-downbeat",
        action="store_true",
        help="Disable Omnizart downbeat detection when --annotation is not provided",
    )

    t2m = sub.add_parser("tsv2midi", help="TSV → MIDI")
    t2m.add_argument("input", help="Input .tsv file")
    t2m.add_argument("--out", "-o", help="Output .mid file (default: input without '.tsv')")

    args = parser.parse_args()

    if args.command == "midi2tsv":
        in_path = Path(args.input)
        out_path = Path(args.out) if args.out else in_path.with_suffix(in_path.suffix + ".tsv")
        data = in_path.read_bytes()
        annotation_path = args.annotation if hasattr(args, 'annotation') else None
        try:
            tsv = midi_to_tsv(
                data,
                source=in_path.name,
                annotation_path=annotation_path,
                auto_downbeat=not args.no_auto_downbeat,
                midi_path=in_path,
            )
        except DownbeatDetectionError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)
        out_path.write_text(tsv)
        print(f"Written: {out_path}")
    elif args.command == "tsv2midi":
        in_path = Path(args.input)
        out_path = Path(args.out) if args.out else in_path.with_suffix("")
        tsv = in_path.read_text()
        midi = tsv_to_midi(tsv)
        out_path.write_bytes(midi)
        print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
