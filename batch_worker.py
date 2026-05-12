#!/usr/bin/env python3
"""
Worker script for parallel batch conversion of ASAP dataset.
Each worker processes a subset of MIDI files assigned by index.
Usage: python batch_worker.py <dataset_root> <output_root> <worker_id> <num_workers>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from midi_tsv import midi_to_tsv


def worker_convert(dataset_root: str, output_root: str, worker_id: int, num_workers: int):
    """Convert a slice of MIDI files assigned to this worker."""
    src = Path(dataset_root)
    dst = Path(output_root)
    dst.mkdir(parents=True, exist_ok=True)

    midi_files = sorted(src.rglob("*.mid"))

    # Split by index modulo num_workers
    my_files = [f for i, f in enumerate(midi_files) if i % num_workers == worker_id]

    print(f"[Worker {worker_id}/{num_workers}] Assigned {len(my_files)} files")

    converted = 0
    skipped = 0
    errors = 0

    for i, midi_file in enumerate(my_files, 1):
        rel = midi_file.relative_to(src)
        output_path = dst / rel.parent / (midi_file.name + ".tsv")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            skipped += 1
            continue

        try:
            midi_data = midi_file.read_bytes()
            tsv_content = midi_to_tsv(
                midi_data,
                source=midi_file.name,
                annotation_path=None,
                auto_downbeat=True,
                midi_path=midi_file,
            )
            output_path.write_text(tsv_content)
            converted += 1

            if i % 10 == 0:
                print(f"[Worker {worker_id}] {i}/{len(my_files)} done ({converted} converted, {skipped} skipped, {errors} errors)")

        except Exception as e:
            errors += 1
            print(f"[Worker {worker_id}] ERR {midi_file.relative_to(src)}: {e}")

    print(f"[Worker {worker_id}] DONE: {converted} converted, {skipped} skipped, {errors} errors")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python batch_worker.py <dataset_root> <output_root> <worker_id> <num_workers>")
        sys.exit(1)

    worker_convert(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
