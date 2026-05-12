#!/usr/bin/env python3
"""
Batch convert ASAP dataset MIDI files to measure-based MIDI-TSV format using
ONLY Omnizart auto-downbeat detection, then summarize heuristic phrase lengths.

Output mirrors the ASAP directory tree under a separate root and never
overwrites existing TSV files.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

# Ensure the wave-roll directory is on the path.
sys.path.insert(0, str(Path(__file__).parent))
from midi_tsv import midi_to_tsv  # noqa: E402


def convert_one(args: tuple[str, str, str]) -> dict:
    dataset_root, output_root, midi_path_str = args
    src = Path(dataset_root)
    dst = Path(output_root)
    midi_path = Path(midi_path_str)
    rel = midi_path.relative_to(src)
    output_path = dst / rel.parent / (midi_path.name + ".tsv")

    if output_path.exists():
        lengths = extract_phrase_lengths(output_path)
        return {
            "status": "skipped",
            "source": rel.as_posix(),
            "output": output_path.relative_to(dst).as_posix(),
            "phrase_lengths": lengths,
        }

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tsv_content = midi_to_tsv(
            midi_path.read_bytes(),
            source=midi_path.name,
            annotation_path=None,
            auto_downbeat=True,
            midi_path=midi_path,
        )
        output_path.write_text(tsv_content, encoding="utf-8")
        return {
            "status": "converted",
            "source": rel.as_posix(),
            "output": output_path.relative_to(dst).as_posix(),
            "phrase_lengths": phrase_lengths_from_tsv(tsv_content),
        }
    except Exception as exc:
        return {
            "status": "error",
            "source": rel.as_posix(),
            "output": output_path.relative_to(dst).as_posix(),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=8),
        }


def extract_phrase_lengths(path: Path) -> list[int]:
    try:
        return phrase_lengths_from_tsv(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def phrase_lengths_from_tsv(tsv: str) -> list[int]:
    measures: list[tuple[int, int, int]] = []
    phrases: list[tuple[int, int]] = []

    for raw_line in tsv.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = raw_line.split("\t")
        if len(fields) < 3:
            continue
        record_type = fields[0]
        if record_type.startswith("M") and record_type[1:].isdigit():
            measures.append((int(record_type[1:]), int(fields[1]), int(fields[2])))
        elif record_type.startswith("H") and record_type[1:].isdigit():
            phrases.append((int(fields[1]), int(fields[2])))

    lengths = []
    for phrase_start, phrase_end in phrases:
        count = sum(
            1
            for _, measure_start, measure_end in measures
            if measure_start >= phrase_start and measure_end <= phrase_end
        )
        if count:
            lengths.append(count)
    return lengths


def summarize_phrase_lengths(results: list[dict]) -> dict:
    file_lengths = {
        item["source"]: item.get("phrase_lengths", [])
        for item in results
        if item["status"] in ("converted", "skipped")
    }
    lengths = [length for values in file_lengths.values() for length in values]
    if not lengths:
        return {
            "total_phrases": 0,
            "files_with_phrases": 0,
        }

    sorted_lengths = sorted(lengths)
    distribution = {
        "1-3": sum(1 for x in lengths if 1 <= x <= 3),
        "4": sum(1 for x in lengths if x == 4),
        "5": sum(1 for x in lengths if x == 5),
        "6": sum(1 for x in lengths if x == 6),
        "7": sum(1 for x in lengths if x == 7),
        "8": sum(1 for x in lengths if x == 8),
        "9-12": sum(1 for x in lengths if 9 <= x <= 12),
        ">12": sum(1 for x in lengths if x > 12),
    }
    files_with_long = sum(1 for values in file_lengths.values() if any(x > 8 for x in values))
    files_all_4_8 = sum(
        1
        for values in file_lengths.values()
        if values and all(4 <= x <= 8 for x in values)
    )
    total = len(lengths)
    return {
        "total_phrases": total,
        "files_with_phrases": sum(1 for values in file_lengths.values() if values),
        "mean": sum(lengths) / total,
        "median": statistics.median(lengths),
        "max": max(lengths),
        "p25": percentile(sorted_lengths, 25),
        "p50": percentile(sorted_lengths, 50),
        "p75": percentile(sorted_lengths, 75),
        "p90": percentile(sorted_lengths, 90),
        "p95": percentile(sorted_lengths, 95),
        "p99": percentile(sorted_lengths, 99),
        "distribution": distribution,
        "distribution_percent": {
            key: (value / total * 100.0)
            for key, value in distribution.items()
        },
        "within_4_8_count": sum(1 for x in lengths if 4 <= x <= 8),
        "within_4_8_percent": sum(1 for x in lengths if 4 <= x <= 8) / total * 100.0,
        "over_8_count": sum(1 for x in lengths if x > 8),
        "over_8_percent": sum(1 for x in lengths if x > 8) / total * 100.0,
        "files_with_over_8": files_with_long,
        "files_all_4_8": files_all_4_8,
    }


def percentile(sorted_values: list[int], pct: int) -> int:
    if not sorted_values:
        return 0
    idx = round((len(sorted_values) - 1) * pct / 100)
    return sorted_values[idx]


def write_reports(output_root: Path, results: list[dict], summary: dict) -> None:
    report = {
        "output_root": str(output_root),
        "counts": {
            "converted": sum(1 for item in results if item["status"] == "converted"),
            "skipped": sum(1 for item in results if item["status"] == "skipped"),
            "errors": sum(1 for item in results if item["status"] == "error"),
            "total": len(results),
        },
        "phrase_length_summary": summary,
        "failures": [
            {
                "source": item["source"],
                "output": item["output"],
                "error": item.get("error", ""),
            }
            for item in results
            if item["status"] == "error"
        ],
    }
    (output_root / "conversion_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failures = report["failures"]
    (output_root / "failures.txt").write_text(
        "\n".join(f"{item['source']}\t{item['error']}" for item in failures) + ("\n" if failures else ""),
        encoding="utf-8",
    )


def print_summary(results: list[dict], summary: dict) -> None:
    converted = sum(1 for item in results if item["status"] == "converted")
    skipped = sum(1 for item in results if item["status"] == "skipped")
    errors = sum(1 for item in results if item["status"] == "error")
    total = len(results)

    print("\n" + "=" * 72)
    print("Conversion complete")
    print(f"  Converted: {converted}")
    print(f"  Skipped:   {skipped}")
    print(f"  Errors:    {errors}")
    print(f"  Total:     {total}")
    print("=" * 72)

    if not summary.get("total_phrases"):
        print("No phrase statistics available.")
        return

    print("Phrase length statistics")
    print(f"  Total phrases: {summary['total_phrases']}")
    print(f"  Mean / median / max: {summary['mean']:.2f} / {summary['median']} / {summary['max']}")
    print(
        "  P25 / P50 / P75: "
        f"{summary['p25']} / {summary['p50']} / {summary['p75']}"
    )
    print(
        "  P90 / P95 / P99: "
        f"{summary['p90']} / {summary['p95']} / {summary['p99']}"
    )
    print("  Distribution:")
    for key, value in summary["distribution"].items():
        pct = summary["distribution_percent"][key]
        print(f"    {key:>4}: {value:5d}  {pct:5.1f}%")
    print(f"  Within 4-8: {summary['within_4_8_percent']:.1f}%")
    print(f"  Over 8:     {summary['over_8_percent']:.1f}%")
    print(f"  Files with >8: {summary['files_with_over_8']}")
    print(f"  Files all 4-8: {summary['files_all_4_8']}")


def batch_convert_omnizart(dataset_root: str, output_root: str, jobs: int) -> bool:
    src = Path(dataset_root).expanduser().resolve()
    dst = Path(output_root).expanduser().resolve()
    dst.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        print(f"Error: Dataset path does not exist: {src}")
        return False

    midi_files = sorted(src.rglob("*.mid"))
    print(f"Found {len(midi_files)} MIDI files")
    print(f"Output root: {dst}")
    print(f"Workers: {jobs}")

    tasks = [(str(src), str(dst), str(path)) for path in midi_files]
    results = []
    with ProcessPoolExecutor(max_workers=jobs) as executor:
        futures = [executor.submit(convert_one, task) for task in tasks]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            status = result["status"].upper()
            if result["status"] == "error":
                print(f"[{i}/{len(futures)}] {status:9} {result['source']}: {result.get('error', '')}", flush=True)
            else:
                phrase_count = len(result.get("phrase_lengths", []))
                print(f"[{i}/{len(futures)}] {status:9} {result['source']} ({phrase_count} phrases)", flush=True)

    results.sort(key=lambda item: item["source"])
    summary = summarize_phrase_lengths(results)
    write_reports(dst, results, summary)
    print_summary(results, summary)
    return not any(item["status"] == "error" for item in results)


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch convert ASAP MIDI files with Omnizart-only downbeats.")
    parser.add_argument("dataset_root", nargs="?", default="../data/asap-dataset")
    parser.add_argument("output_root", nargs="?", default="../data/asap-dataset-omnizart")
    parser.add_argument("--jobs", type=int, default=1, help="Number of parallel worker processes")
    args = parser.parse_args()

    success = batch_convert_omnizart(args.dataset_root, args.output_root, max(1, args.jobs))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
