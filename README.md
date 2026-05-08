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

## Default MIDI Opening Behavior

When the extension opens a `.mid` or `.midi` file, it first converts the MIDI to
MIDI-TSV for the side panel. The default target is **Measure mode**:

```tsv
# slice_type=measure
# annotation_source=annotation
M1	0	1200
M2	1200	2400
```

If an annotation file is available next to the MIDI file, it is used first. The
extension currently looks for:

- `<midi_stem>_annotations.txt`
- `annotations.txt`

If no annotation file is found, the extension calls Omnizart's beat module to
predict downbeats and uses those downbeats as measure starts:

```tsv
# slice_type=measure
# annotation_source=omnizart
M1	...
M2	...
```

If the configured Python environment is missing or Omnizart fails, the MIDI still
opens, but the TSV panel falls back to the old heuristic Slice mode:

```tsv
# slice_type=segment
S1	...
S2	...
```

## Python / Omnizart Setup

The piano-roll UI runs inside VS Code, but Measure-mode MIDI-TSV conversion uses
Python because Omnizart is a Python package. For normal MIDI playback/visual
display, Python is not needed. For default Measure-mode TSV generation, Python
and Omnizart must be installed and configured.

Omnizart's current GitHub version requires Python `<3.9`. Do not downgrade an
existing modern `base` environment just for Omnizart. Create a dedicated conda
environment instead.

### 1. Install System Packages

Ubuntu / Debian:

```bash
sudo apt-get update
sudo apt-get install -y libsndfile-dev fluidsynth ffmpeg
```

### 2. Create A Dedicated Conda Environment

```bash
conda create -n omnizart38 -y python=3.8 pip
conda activate omnizart38
```

### 3. Install Omnizart

Install Cython/Numpy first, then build `madmom`, then install Omnizart from
GitHub. The GitHub version is required because the PyPI `omnizart==0.1.0` package
does not include the `omnizart.beat` module used for downbeat prediction.

```bash
python -m pip install "numpy<1.24" "Cython<3"
python -m pip install --no-build-isolation madmom==0.16.1
python -m pip install --pre keras-nightly~=2.5.0.dev -i https://pypi.org/simple
python -m pip install --pre "git+https://github.com/Music-and-Culture-Technology-Lab/omnizart.git"
```

### 4. Download Checkpoints

```bash
omnizart download-checkpoints
```

This downloads several model checkpoints, including the beat model used by
Measure mode. The beat checkpoint is relatively large, so the command can take a
while.

### 5. Verify The Beat Module

```bash
python - <<'PY'
import omnizart
from omnizart.beat.app import BeatTranscription
print("omnizart", omnizart.__version__)
print("BeatTranscription OK")
PY

omnizart beat transcribe --help
```

Optional smoke test:

```bash
mkdir -p /tmp/omnizart-beat-test
omnizart beat transcribe -o /tmp/omnizart-beat-test path/to/input.mid
ls /tmp/omnizart-beat-test
```

Expected output includes:

```text
<name>_beat.csv
<name>_down_beat.csv
```

## VS Code Configuration

Set the extension's Python path to the environment that contains Omnizart.

Workspace `.vscode/settings.json` example:

```json
{
  "waveRollPiano.pythonPath": "/home/sy/anaconda3/envs/omnizart38/bin/python"
}
```

The extension tries Python executables in this order:

1. `waveRollPiano.pythonPath`
2. `python3`
3. `python`

For a packaged install, configure this before relying on Measure mode. If it is
not configured and the default `python3` does not contain Omnizart, the extension
falls back to Slice mode and shows a warning.

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
