# WaveRoll Piano - MIDI Editor & MIDI-TSV

VS Code extension for viewing, editing, and playing MIDI files with an interactive piano roll,
plus a Python tool for converting MIDI ↔ MIDI-TSV. MIDI → TSV defaults to
**measure-based** slicing: existing annotation files are used when supplied,
otherwise performance downbeats are predicted with Omnizart's beat module.

## Plugin

Install from VS Code Marketplace as **WaveRoll Piano** (`wave-roll-piano-0.5.0.vsix`).

### Features
- Piano roll visualization powered by [wave-roll](https://www.npmjs.com/package/wave-roll)
- Audio playback via Tone.js (Default / Salamander C5 piano sounds)
- MIDI-TSV text editing: preview, edit, save → writes back to MIDI
- Multi-file comparison
- Supported formats: `.mid`, `.midi`, `.mid.tsv`, `.midi.tsv`

### Commands
Right-click in explorer:
- **Export MIDI as TSV** — convert `.mid` to `.mid.tsv`
- **Export MIDI-TSV as MIDI** — convert `.mid.tsv` back to `.mid`

## MIDI-TSV Format

See [MIDI-TSV.md](MIDI-TSV.md) for the full format specification covering:
- Segment mode (`S`-prefix) vs measure mode (`M`-prefix) slicing
- Record types and field formats
- Annotation file format for downbeat/beat markers
- Pedal encoding and quantization

## Python Tools

### midi2tsv / tsv2midi

```bash
# MIDI → TSV (measure mode; auto-detect downbeats with Omnizart)
python midi_tsv.py midi2tsv input.mid

# MIDI → TSV (measure mode, annotation-driven; preferred when available)
python midi_tsv.py midi2tsv input.mid --annotation annotations.txt

# Debug/fallback only: old segment mode heuristic slicing
python midi_tsv.py midi2tsv input.mid --no-auto-downbeat

# TSV → MIDI (auto-detects slice_type)
python midi_tsv.py tsv2midi input.mid.tsv

# Custom output path
python midi_tsv.py midi2tsv input.mid --out output.tsv
```

Automatic downbeat detection requires Omnizart to be installed in the Python
environment used to run `midi_tsv.py`.

### Batch convert

```bash
python batch_convert_asap.py /path/to/dataset
```

Scans recursively for `.mid` files, uses matching `*_annotations.txt` files
when present, and otherwise predicts downbeats with Omnizart. All generated
`.mid.tsv` files are measure-based by default.

### Pedal quantization

Both modes apply smart pedal quantization: within each segment (between
consecutive note events or measure boundaries), each pedal type keeps at
most 5 points — first, last, and up to 3 representative peaks/valleys.
Redundant events (value change ≤ 3) are filtered first.

## License

MIT
