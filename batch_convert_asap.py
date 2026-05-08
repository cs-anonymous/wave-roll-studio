#!/usr/bin/env python3
"""
Batch convert ASAP dataset MIDI files to measure-based MIDI-TSV format.
"""

import sys
from pathlib import Path
from midi_tsv import midi_to_tsv

def find_annotation_for_midi(midi_path: Path) -> Path | None:
    """Find the corresponding annotation file for a MIDI file."""
    # Try exact match: filename_annotations.txt
    stem = midi_path.stem
    annotation_path = midi_path.parent / f"{stem}_annotations.txt"
    if annotation_path.exists():
        return annotation_path
    return None

def batch_convert(dataset_root: str, output_suffix: str = ".tsv"):
    """Convert all MIDI files in the dataset to MIDI-TSV format."""
    dataset_path = Path(dataset_root)

    if not dataset_path.exists():
        print(f"Error: Dataset path does not exist: {dataset_path}")
        return False

    # Find all MIDI files
    midi_files = list(dataset_path.rglob("*.mid"))
    print(f"Found {len(midi_files)} MIDI files")

    converted = 0
    skipped = 0
    errors = 0

    for i, midi_file in enumerate(midi_files, 1):
        # Find annotation file
        annotation_file = find_annotation_for_midi(midi_file)

        if not annotation_file:
            print(f"[{i}/{len(midi_files)}] SKIP: No annotation for {midi_file.name}")
            skipped += 1
            continue

        # Output path
        output_path = midi_file.with_suffix(midi_file.suffix + output_suffix)

        # Skip if already exists
        if output_path.exists():
            print(f"[{i}/{len(midi_files)}] EXISTS: {output_path.name}")
            skipped += 1
            continue

        try:
            # Convert
            midi_data = midi_file.read_bytes()
            tsv_content = midi_to_tsv(
                midi_data,
                source=midi_file.name,
                annotation_path=str(annotation_file)
            )

            # Write output
            output_path.write_text(tsv_content)

            print(f"[{i}/{len(midi_files)}] ✓ {midi_file.name} -> {output_path.name}")
            converted += 1

        except Exception as e:
            print(f"[{i}/{len(midi_files)}] ERROR: {midi_file.name}: {e}")
            errors += 1

    print("\n" + "=" * 60)
    print(f"Conversion complete!")
    print(f"  Converted: {converted}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print("=" * 60)

    return errors == 0

if __name__ == "__main__":
    dataset_root = sys.argv[1] if len(sys.argv) > 1 else "../data/asap-dataset"
    success = batch_convert(dataset_root)
    sys.exit(0 if success else 1)
