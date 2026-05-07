#!/usr/bin/env python3
"""
MIDI-TSV v0.2: bidirectional conversion between MIDI files and MIDI-TSV text.

Usage:
    python midi_tsv.py midi2tsv <input.mid>        # creates <input.mid>.tsv
    python midi_tsv.py tsv2midi <input.tsv>         # creates <input>.mid
    python midi_tsv.py midi2tsv <input.mid> --out <path>
    python midi_tsv.py tsv2midi <input.tsv> --out <path>
"""

import argparse
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

# ── Constants ──────────────────────────────────────────────────────────────

MIN_TICK_SCALE = 5
MAX_TICK_SCALE = 50
TARGET_TICK_SCALE_MS = 20  # 1 macro-tick ≈ 20ms at dominant tempo
DEFAULT_MICROSECONDS_PER_BEAT = 500_000
MIN_SLICE_SECONDS = 10
TARGET_SLICE_SECONDS = 15
MAX_SLICE_SECONDS = 20
MIN_GAP_SECONDS = 0.35
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


# ── Tick scale selection ───────────────────────────────────────────────────

def select_tick_scale(tpq: int, tempos: list[dict]) -> int:
    """Auto-select tick_scale so 1 macro-tick ≈ 20ms at the dominant tempo,
    rounded to nearest multiple of 5."""
    microseconds_per_beat = DEFAULT_MICROSECONDS_PER_BEAT
    for t in tempos:
        if t["tick"] == 0:
            microseconds_per_beat = t["microseconds_per_beat"]
            break
    if not tempos:
        pass  # use default
    ms_per_tick = (microseconds_per_beat / tpq) / 1000
    raw_scale = TARGET_TICK_SCALE_MS / ms_per_tick
    rounded = round(raw_scale / 5) * 5
    return max(MIN_TICK_SCALE, min(MAX_TICK_SCALE, rounded))


# ── Slicing ────────────────────────────────────────────────────────────────

def _to_macro(t: int, tick_scale: int) -> int:
    return round(t / tick_scale)


def create_slices(
    notes: list[dict], pedals: list[dict],
    end_tick: int, tpq: int, tempos: list[dict], tick_scale: int
) -> list[dict]:
    if end_tick <= 0:
        return [{"id": 1, "start": 0, "end": 0}]

    microseconds_per_beat = DEFAULT_MICROSECONDS_PER_BEAT
    for t in tempos:
        if t["tick"] == 0:
            microseconds_per_beat = t["microseconds_per_beat"]
            break
    ms_per_tick = (microseconds_per_beat / tpq) / 1000

    end_macro = _to_macro(end_tick, tick_scale)
    min_macro = max(1, round(MIN_SLICE_SECONDS / (tick_scale * ms_per_tick / 1000)))
    target_macro = max(min_macro, round(TARGET_SLICE_SECONDS / (tick_scale * ms_per_tick / 1000)))
    max_macro = max(target_macro, round(MAX_SLICE_SECONDS / (tick_scale * ms_per_tick / 1000)))
    min_gap_macro = max(1, round(MIN_GAP_SECONDS / (tick_scale * ms_per_tick / 1000)))

    cut_candidates = _find_weak_cut_candidates(notes, pedals, tick_scale, min_gap_macro)

    slices = []
    start_macro = 0
    while end_macro - start_macro > max_macro:
        min_cut = start_macro + min_macro
        max_cut = min(start_macro + max_macro, end_macro)
        target_cut = min(start_macro + target_macro, max_cut)
        candidates = [c for c in cut_candidates if min_cut < c < max_cut]
        cut = min(candidates, key=lambda c: abs(c - target_cut)) if candidates else target_cut
        slices.append({
            "id": len(slices) + 1,
            "start": start_macro * tick_scale,
            "end": cut * tick_scale,
        })
        start_macro = cut

    slices.append({
        "id": len(slices) + 1,
        "start": start_macro * tick_scale,
        "end": end_tick,
    })
    return slices


def _find_weak_cut_candidates(
    notes: list[dict], pedals: list[dict], tick_scale: int, min_gap_macro: int
) -> list[int]:
    intervals = []
    for n in notes:
        s = _to_macro(n["t"], tick_scale)
        e = _to_macro(n["t"] + n["dur"], tick_scale)
        if e >= s:
            intervals.append((s, e))

    pedal_down_tick = None
    for p in sorted(pedals, key=lambda x: x["t"]):
        mt = _to_macro(p["t"], tick_scale)
        if p["val"] >= 64 and pedal_down_tick is None:
            pedal_down_tick = mt
        elif p["val"] < 64 and pedal_down_tick is not None:
            intervals.append((pedal_down_tick, mt))
            pedal_down_tick = None

    # Merge intervals
    intervals.sort()
    merged = []
    for s, e in intervals:
        if not merged or s > merged[-1][1]:
            merged.append([s, e])
        else:
            merged[-1][1] = max(merged[-1][1], e)

    cuts = []
    for i in range(1, len(merged)):
        gap = merged[i][0] - merged[i-1][1]
        if gap >= min_gap_macro:
            cuts.append(round((merged[i-1][1] + merged[i][0]) / 2))

    # Also check event tick gaps
    event_ticks = sorted(set(
        _to_macro(n["t"], tick_scale) for n in notes
    ) | set(
        _to_macro(p["t"], tick_scale) for p in pedals
    ))
    for i in range(1, len(event_ticks)):
        if event_ticks[i] - event_ticks[i-1] >= min_gap_macro:
            cuts.append(round((event_ticks[i-1] + event_ticks[i]) / 2))

    return sorted(set(cuts))


def _find_slice_local_start(notes, pedals, markers, slice_, slices) -> int:
    first = float("inf")
    is_last = slice_["id"] == len(slices)
    for item in [*notes, *pedals, *markers]:
        if item["t"] >= slice_["start"] and (item["t"] < slice_["end"] or is_last):
            first = min(first, item["t"])
    return int(first) if first != float("inf") else slice_["start"]


def quantize_pedal_events(pedals: list[dict], epsilon: int = PEDAL_VALUE_EPSILON) -> list[dict]:
    last_by_type: dict[str, dict] = {}
    result = []
    for pedal in pedals:
        previous = last_by_type.get(pedal["type"])
        if previous is not None and abs(pedal["val"] - previous["val"]) <= epsilon:
            continue
        result.append(pedal)
        last_by_type[pedal["type"]] = pedal
    return result


# ── Scale helpers ──────────────────────────────────────────────────────────

def scale_tick(tick: int, tick_scale: int) -> int:
    return round(tick / tick_scale)


def scale_duration(dur: int, tick_scale: int) -> int:
    return max(1, scale_tick(dur, tick_scale)) if dur > 0 else 0


# ── MIDI → TSV ─────────────────────────────────────────────────────────────

def midi_to_tsv(data: bytes, source: str = "unknown.mid") -> str:
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
    pedals = quantize_pedal_events(pedals)
    markers.sort(key=lambda m: m["t"])

    tick_scale = select_tick_scale(tpq, tempos)
    slices = create_slices(notes, pedals, end_tick, tpq, tempos, tick_scale)
    detected_key = detect_key_from_notes(notes)

    # Build output
    lines: list[str] = [
        "# midi-tsv v0.2",
        f"# source={source}",
        "# unit=tick",
        f"# tick_scale={tick_scale}",
        f"# tpq={tpq}",
        "# pitch=abc-absolute",
        f"# detected_key={detected_key}",
        f"# channel={default_channel}",
    ]

    for t in tempos:
        lines.append(f"# tempo={scale_tick(t['tick'], tick_scale)},{t['microseconds_per_beat']}")
    for sig in time_sigs:
        lines.append(
            f"# time_signature={scale_tick(sig['tick'], tick_scale)},"
            f"{sig['numerator']},{sig['denominator']},{sig['metronome']},{sig['thirtyseconds']}"
        )
    for ks in key_sigs:
        lines.append(f"# key_signature={scale_tick(ks['tick'], tick_scale)},{ks['key']},{ks['scale']}")

    lines.append("")

    for sl in slices:
        local_start = _find_slice_local_start(notes, pedals, markers, sl, slices)
        lines.append(f"S{sl['id']}\t{scale_tick(local_start, tick_scale)}\t{scale_tick(sl['end'], tick_scale)}")

        records = []
        is_last = sl["id"] == len(slices)
        slice_notes = [n for n in notes if n["t"] >= local_start and (n["t"] < sl["end"] or is_last)]
        slice_key = detect_key_from_notes(slice_notes) if slice_notes else detected_key
        for n in slice_notes:
            records.append({
                "t": n["t"],
                "order": 1,
                "line": f"{midi_pitch_to_abc_smart(n['pitch'], slice_key)}{scale_duration(n['dur'], tick_scale)}\t{scale_tick(n['t'] - local_start, tick_scale)}\t{n['vel']}",
            })
        for p in pedals:
            if p["t"] >= local_start and (p["t"] < sl["end"] or is_last):
                records.append({
                    "t": p["t"],
                    "order": 0,
                    "line": f"{p['type']}\t{scale_tick(p['t'] - local_start, tick_scale)}\t{p['val']}",
                })
        for m in markers:
            if m["t"] >= local_start and (m["t"] < sl["end"] or is_last):
                records.append({
                    "t": m["t"],
                    "order": 2,
                    "line": f"M\t{scale_tick(m['t'] - local_start, tick_scale)}\t{json.dumps(m['text'])}",
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

    for line_idx, raw_line in enumerate(tsv.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        fields = raw_line.split("\t")
        record_type = fields[0]

        if _is_slice_record(record_type):
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
    for t in meta["tempos"]:
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
        "version": "v0.2", "tpq": 480, "tick_scale": 1,
        "tempos": [], "time_signatures": [], "key_signatures": [],
        "track_channels": {}, "channel": 0,
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


def _is_slice_record(s: str) -> bool:
    return bool(re.match(r"^S\d+$", s))


def _is_note_record(s: str) -> bool:
    return bool(re.match(r"^[_^=]*[A-Ga-g]['|,]*\d+$", s))


def _parse_note_record(s: str, line_idx: int) -> tuple[str, int]:
    match = re.match(r"^([_^=]*[A-Ga-g]['|,]*)(\d+)$", s)
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

    t2m = sub.add_parser("tsv2midi", help="TSV → MIDI")
    t2m.add_argument("input", help="Input .tsv file")
    t2m.add_argument("--out", "-o", help="Output .mid file (default: input without '.tsv')")

    args = parser.parse_args()

    if args.command == "midi2tsv":
        in_path = Path(args.input)
        out_path = Path(args.out) if args.out else in_path.with_suffix(in_path.suffix + ".tsv")
        data = in_path.read_bytes()
        tsv = midi_to_tsv(data, source=in_path.name)
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
