#!/usr/bin/env python3
"""Rank putter candidates by phase order, independent of absolute thresholds.

This experiment scores whether a candidate has the temporal grammar of a putt:
quiet address, a compact lateral excursion, a return through the address region,
and a quiet follow-through.  It is intentionally separate from the canonical
Vision ranker until holdout evidence supports promotion.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path


def grouped_rows(data):
    grouped = {}
    for row in data["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["time"])
    return grouped


def nearest(rows, target):
    if not rows:
        return None
    times = [row["time"] for row in rows]
    index = bisect.bisect_left(times, target)
    choices = []
    if index < len(rows):
        choices.append(rows[index])
    if index:
        choices.append(rows[index - 1])
    return min(choices, key=lambda row: abs(row["time"] - target))


def phase_features(rows, center):
    offsets = [-0.9, -0.7, -0.5, -0.3, -0.1, 0.1, 0.3, 0.5, 0.7, 0.9]
    samples = [nearest(rows, center + offset) for offset in offsets]
    if any(sample is None for sample in samples):
        return None
    values = []
    for sample in samples:
        scale = max(float(sample.get("bodyScale", 0.04)), 0.04)
        values.append((float(sample["handX"]) - float(sample["coreX"])) / scale)

    # Address should be quiet before and after the compact stroke.
    pre = values[:3]
    post = values[-3:]
    pre_motion = sum(abs(pre[i + 1] - pre[i]) for i in range(len(pre) - 1))
    post_motion = sum(abs(post[i + 1] - post[i]) for i in range(len(post) - 1))
    baseline = (sum(pre) / len(pre) + sum(post) / len(post)) / 2.0
    middle = values[3:7]
    excursion = max(abs(value - baseline) for value in middle)
    middle_index = max(range(len(middle)), key=lambda i: abs(middle[i] - baseline))
    peak = middle[middle_index]
    # A putt should move away from address and then come back toward it.
    return_distance = abs(peak - values[6])
    crossing = min(abs(value - baseline) for value in values[4:8])
    compactness = math.exp(-0.8 * max(0.0, max(values) - min(values) - 1.2))
    quietness = math.exp(-0.7 * (pre_motion + post_motion))
    phase = min(1.0, excursion / 0.18) * min(1.0, return_distance / 0.08)
    phase *= min(1.0, (0.12 - crossing + 0.12) / 0.24)
    score = quietness * compactness * phase
    return {
        "phaseScore": score,
        "preMotion": pre_motion,
        "postMotion": post_motion,
        "excursion": excursion,
        "returnDistance": return_distance,
        "crossingDistance": crossing,
    }


def load_ranked(work, name):
    data = json.loads((work / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("selected", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--ranked", default="putter-feature-temporal-action-quiet-mat-current.json")
    parser.add_argument("--output", default="putter-phase-holdout-experiment.json")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    grouped = grouped_rows(json.loads((args.work / args.pose).read_text(encoding="utf-8")))
    ranked = []
    for item in load_ranked(args.work, args.ranked):
        features = phase_features(grouped.get(int(item["sourceIndex"]), []), float(item["timeSeconds"]))
        if not features:
            continue
        ranked.append({**item, **features})
    ranked.sort(key=lambda item: item["phaseScore"], reverse=True)
    selected = []
    for item in ranked:
        if any(item["sourceIndex"] == other["sourceIndex"] and abs(item["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(item)
        if len(selected) >= args.limit:
            break
    output = {"schemaVersion": 1, "method": "temporal phase-order score", "ranked": ranked, "selected": selected}
    path = args.work / args.output
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ranked={len(ranked)} selected={len(selected)} output={path}")


if __name__ == "__main__":
    main()
