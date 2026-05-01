# WaveRoll Piano - MIDI Editor & Viewer

> **WaveRoll Piano** is a [VS Code extension](https://marketplace.visualstudio.com/items?itemName=crescent-stdio.wave-roll-piano) for viewing, editing, and playing MIDI files with an interactive piano roll visualization. Supports conversion between MIDI and MIDI-TSV formats.

![License](https://img.shields.io/badge/license-MIT-blue.svg)

Built on top of [**WaveRoll**](https://github.com/crescent-stdio/wave-roll), an interactive JavaScript library for MIDI piano roll visualization.

## Features

- **Piano Roll Visualization**: View MIDI files as an interactive piano roll display powered by the [wave-roll](https://www.npmjs.com/package/wave-roll) library
- **Audio Playback**: Play MIDI files directly in VS Code using Tone.js synthesis with multiple piano sounds (Default / Salamander C5)
- **MIDI-TSV Conversion**: View and edit MIDI as a text-based TSV representation — switch between Preview and Edit mode, modify TSV text, then save to write back to MIDI
- **Multi-File Comparison**: Load multiple MIDI files for side-by-side visualization
- **Audio Reference Import**: Add a reference audio track (`.wav`, `.mp3`, `.m4a`, `.ogg`)
- **MIDI Export**: Export modified MIDI files to disk
- **Format Support**: `.mid`, `.midi`, `.mid.tsv`, `.midi.tsv`

## Installation

1. Open VS Code
2. Go to Extensions
3. Search for **"WaveRoll Piano"**
4. Click **Install**

Or install directly from the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=crescent-stdio.wave-roll-piano)
or [Open VSX](https://open-vsx.org/extension/crescent-stdio/wave-roll-piano).

## Usage

1. Open any `.mid`, `.midi`, `.mid.tsv`, or `.midi.tsv` file in VS Code
2. The file automatically opens in the WaveRoll Piano viewer
3. Use the player controls to interact with the MIDI file
4. Click **Add MIDI Files** to layer additional MIDI files for comparison
5. Click **Add Audio File** to load a reference audio track

## TSV Editing

The right panel displays a MIDI-TSV text representation of the MIDI file:

- **Preview mode**: Click on TSV rows to seek in the playback timeline
- **Edit mode**: Click the ✎ icon in the panel header to switch to a text editor. Modify the TSV text, then press **Cmd+S** / **Ctrl+S** to save. The TSV is converted back to MIDI and written to disk.

## MIDI-TSV Commands

Right-click a MIDI file in the explorer to:
- **Export MIDI as TSV**: Convert `.mid`/`.midi` to `.mid.tsv`
- **Export MIDI-TSV as MIDI**: Convert `.mid.tsv`/`.midi.tsv` to `.mid`/`.midi`

## Controls

- **Play/Pause**: Start or pause MIDI playback
- **Stop**: Stop playback and reset to beginning
- **Tempo**: Click the BPM badge to adjust playback tempo
- **Export**: Export MIDI with the current tempo setting
- **Piano Sound**: Switch between Default and Salamander C5 piano samples

## Related Projects

- **WaveRoll Library**: [GitHub](https://github.com/crescent-stdio/wave-roll) | [NPM](https://www.npmjs.com/package/wave-roll)
- **Web Demo**: [https://crescent-stdio.github.io/wave-roll/](https://crescent-stdio.github.io/wave-roll/)
- **Standalone Demo**: [https://crescent-stdio.github.io/wave-roll/standalone.html](https://crescent-stdio.github.io/wave-roll/standalone.html)

## Tech Stack

- **[wave-roll](https://www.npmjs.com/package/wave-roll)**: Interactive piano roll rendering engine
- **[Tone.js](https://tonejs.github.io/)**: Web Audio synthesis framework
- **[@tonejs/midi](https://github.com/Tonejs/Midi)**: MIDI file parsing
- **[midi-file](https://www.npmjs.com/package/midi-file)**: Binary MIDI parsing and serialization
- **[esbuild](https://esbuild.github.io/)**: Fast JavaScript bundler

## License

MIT
