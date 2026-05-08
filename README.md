# WaveRoll Piano - MIDI Editor & MIDI-TSV

VS Code extension for viewing, editing, and playing MIDI files with an interactive piano roll,
plus a Python tool for converting MIDI ↔ MIDI-TSV with support for both **segment-based** and **measure-based** slicing.

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
# MIDI → TSV (segment mode, heuristic slicing)
python midi_tsv.py midi2tsv input.mid

# MIDI → TSV (measure mode, annotation-driven)
python midi_tsv.py midi2tsv input.mid --annotation annotations.txt

# TSV → MIDI (auto-detects slice_type)
python midi_tsv.py tsv2midi input.mid.tsv

# Custom output path
python midi_tsv.py midi2tsv input.mid --annotation annotations.txt --out output.tsv
```

### Batch convert

```bash
python batch_convert_asap.py /path/to/dataset
```

Scans recursively for `.mid` files, finds matching `*_annotations.txt`,
and generates measure-based `.mid.tsv` files.

### Pedal quantization

Both modes apply smart pedal quantization: within each segment (between
consecutive note events or measure boundaries), each pedal type keeps at
most 5 points — first, last, and up to 3 representative peaks/valleys.
Redundant events (value change ≤ 3) are filtered first.

## License

MIT
