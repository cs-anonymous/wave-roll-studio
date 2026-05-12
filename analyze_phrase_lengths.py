#!/usr/bin/env python3
"""
Analyze phrase length statistics in Omnizart-generated MIDI-TSV files.
Reports distribution of phrase lengths (in measures), with special focus
on long phrases (>8 measures) that may indicate insufficient phrase boundaries
for pieces without clear gaps.
"""

import sys
import re
from pathlib import Path
from collections import defaultdict, Counter
from typing import Optional

PHRASE_RE = re.compile(r"^(H\d+)\t(\d+)\t(\d+)")
MEASURE_RE = re.compile(r"^(M\d+)\t(\d+)(?:\t(\d+))?")


def analyze_tsv(tsv_path: Path) -> Optional[dict]:
    """Parse a TSV file and return phrase statistics."""
    try:
        content = tsv_path.read_text()
    except Exception as e:
        return None

    phrases = []  # list of (id, start_tick, end_tick)
    measures = []  # list of (id, start_tick, end_tick)

    for line in content.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = PHRASE_RE.match(line)
        if m:
            phrase_id = int(m.group(1)[1:])  # strip 'H'
            phrases.append((phrase_id, int(m.group(2)), int(m.group(3))))
            continue
        m = MEASURE_RE.match(line)
        if m:
            mid = int(m.group(1)[1:])  # strip 'M'
            start = int(m.group(2))
            end = int(m.group(3)) if m.group(3) else start
            measures.append((mid, start, end))

    if not phrases or not measures:
        return None

    # Build measure ID -> tick range mapping
    measure_by_range = {}
    for mid, start, end in measures:
        measure_by_range[(start, end)] = mid

    # Count measures per phrase
    phrase_measure_counts = []
    for phrase_id, p_start, p_end in phrases:
        count = 0
        for mid, m_start, m_end in measures:
            if m_start >= p_start and m_end <= p_end:
                count += 1
            elif m_start >= p_end:
                break
        phrase_measure_counts.append({
            "phrase_id": phrase_id,
            "measure_count": count,
            "start_tick": p_start,
            "end_tick": p_end,
        })

    total_measures = len(measures)
    return {
        "file": tsv_path.name,
        "total_measures": total_measures,
        "total_phrases": len(phrases),
        "phrase_lengths": phrase_measure_counts,
    }


def main(output_root: str):
    root = Path(output_root)
    if not root.exists():
        print(f"Error: {root} does not exist")
        sys.exit(1)

    tsv_files = sorted(root.rglob("*.mid.tsv"))
    print(f"Found {len(tsv_files)} TSV files")

    all_results = []
    parse_errors = 0
    no_phrases = 0

    for tsv in tsv_files:
        result = analyze_tsv(tsv)
        if result is None:
            parse_errors += 1
            continue
        if result["total_phrases"] == 0:
            no_phrases += 1
            continue
        all_results.append(result)

    print(f"Parsed: {len(all_results)}")
    print(f"No phrases: {no_phrases}")
    print(f"Parse errors: {parse_errors}")

    # --- Global phrase length distribution ---
    all_lengths = []
    for r in all_results:
        for ph in r["phrase_lengths"]:
            all_lengths.append(ph["measure_count"])

    if not all_lengths:
        print("\nNo phrase data to analyze.")
        return

    all_lengths.sort()
    n = len(all_lengths)

    print(f"\n{'='*60}")
    print(f"PHRASE LENGTH STATISTICS (in measures)")
    print(f"{'='*60}")
    print(f"Total phrases: {n}")
    print(f"Mean:          {sum(all_lengths)/n:.1f}")
    print(f"Median:        {all_lengths[n//2]}")
    print(f"Min:           {all_lengths[0]}")
    print(f"Max:           {all_lengths[-1]}")

    # Percentiles
    for p in [25, 50, 75, 90, 95, 99]:
        idx = int(p / 100 * n)
        print(f"P{p:2d}:          {all_lengths[min(idx, n-1)]}")

    # --- Distribution histogram ---
    from collections import Counter
    bins = Counter()
    for l in all_lengths:
        if l <= 3:
            bins["1-3"] += 1
        elif l == 4:
            bins["4"] += 1
        elif l == 5:
            bins["5"] += 1
        elif l == 6:
            bins["6"] += 1
        elif l == 7:
            bins["7"] += 1
        elif l == 8:
            bins["8"] += 1
        elif l <= 12:
            bins["9-12"] += 1
        elif l <= 16:
            bins["13-16"] += 1
        elif l <= 24:
            bins["17-24"] += 1
        else:
            bins["25+"] += 1

    print(f"\n{'='*60}")
    print(f"DISTRIBUTION")
    print(f"{'='*60}")
    max_count = max(bins.values()) if bins else 1
    bar_max = 50
    for label in ["1-3", "4", "5", "6", "7", "8", "9-12", "13-16", "17-24", "25+"]:
        count = bins.get(label, 0)
        if count == 0:
            continue
        bar = "#" * int(count / max_count * bar_max)
        pct = count / n * 100
        print(f"  {label:>6} measures: {count:>6} ({pct:5.1f}%) {bar}")

    # --- Files with very long phrases (>8 measures) ---
    print(f"\n{'='*60}")
    print(f"FILES WITH PHRASES > 8 MEASURES")
    print(f"{'='*60}")

    long_phrase_files = []
    for r in all_results:
        long_phrases = [ph for ph in r["phrase_lengths"] if ph["measure_count"] > 8]
        if long_phrases:
            long_phrase_files.append((r, long_phrases))

    long_phrase_files.sort(key=lambda x: max(p["measure_count"] for p in x[1]), reverse=True)

    for r, long_phrases in long_phrase_files:
        rel_path = r["file"]
        max_len = max(p["measure_count"] for p in long_phrases)
        print(f"  {rel_path}: {len(long_phrases)} long phrases, max {max_len} measures")
        # Show details for top 3 longest
        for ph in sorted(long_phrases, key=lambda x: x["measure_count"], reverse=True)[:3]:
            print(f"    Phrase H{ph['phrase_id']}: {ph['measure_count']} measures (ticks {ph['start_tick']}-{ph['end_tick']})")

    print(f"\n{'='*60}")
    print(f"PER-FILE SUMMARY STATS")
    print(f"{'='*60}")
    file_avg_lengths = []
    for r in all_results:
        lengths = [ph["measure_count"] for ph in r["phrase_lengths"]]
        avg = sum(lengths) / len(lengths)
        file_avg_lengths.append(avg)

    file_avg_lengths.sort()
    nf = len(file_avg_lengths)
    print(f"Per-file mean phrase length:")
    print(f"  Mean of means: {sum(file_avg_lengths)/nf:.1f}")
    print(f"  Median:        {file_avg_lengths[nf//2]:.1f}")
    print(f"  Min:           {file_avg_lengths[0]:.1f}")
    print(f"  Max:           {file_avg_lengths[-1]:.1f}")

    # Files where ALL phrases are > 8 measures
    print(f"\n{'='*60}")
    print(f"FILES WHERE ALL PHRASES EXCEED 8 MEASURES")
    print(f"{'='*60}")
    for r in all_results:
        lengths = [ph["measure_count"] for ph in r["phrase_lengths"]]
        if lengths and all(l > 8 for l in lengths):
            print(f"  {r['file']}: {len(lengths)} phrases, all >8 measures (lengths: {lengths})")


if __name__ == "__main__":
    output_root = sys.argv[1] if len(sys.argv) > 1 else "../data/asap-dataset-omnizart"
    main(output_root)
