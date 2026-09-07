#!/usr/bin/env python3
"""Rank existing putter candidates by normalized pose-trajectory similarity.

This is an experiment beside the Vision featureprint ranker. It keeps the
candidate pool and human labels fixed, then compares the shape of the hand
relative-to-core trajectory with labeled putter and negative templates.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path

import apply_putter_engine


def grouped_rows(pose):
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["time"])
    return grouped


def trajectory(rows, center, radius=0.8, points=9):
    if not rows:
        return None
    times = [row["time"] for row in rows]
    lo = bisect.bisect_left(times, center - radius)
    hi = bisect.bisect_right(times, center + radius)
    window = rows[lo:hi]
    if len(window) < 5:
        return None
    start = center - radius
    step = (2.0 * radius) / (points - 1)
    values = []
    for index in range(points):
        target = start + index * step
        nearest = min(window, key=lambda row: abs(row["time"] - target))
        scale = max(float(nearest.get("bodyScale", 0.04)), 0.04)
        values.append((
            (float(nearest["handX"]) - float(nearest["coreX"])) / scale,
            (float(nearest["handY"]) - float(nearest["coreY"])) / scale,
        ))
    return values


def dtw(lhs, rhs):
    if not lhs or not rhs:
        return math.inf
    grid = [[math.inf] * (len(rhs) + 1) for _ in range(len(lhs) + 1)]
    grid[0][0] = 0.0
    for i, left in enumerate(lhs, start=1):
        for j, right in enumerate(rhs, start=1):
            cost = math.hypot(left[0] - right[0], left[1] - right[1])
            grid[i][j] = cost + min(grid[i - 1][j], grid[i][j - 1], grid[i - 1][j - 1])
    return grid[-1][-1] / (len(lhs) + len(rhs))


def load_ranked(work: Path, name: str):
    data = json.loads((work / name).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("selected", [])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--ranked", default="putter-feature-temporal-action-quiet-mat-replay-current.json")
    parser.add_argument("--output", default="putter-pose-dtw-replay-experiment.json")
    parser.add_argument("--topk", type=int, default=3)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--allow-near-labels", action="store_true")
    args = parser.parse_args()

    pose = grouped_rows(json.loads((args.work / args.pose).read_text(encoding="utf-8")))
    labels = apply_putter_engine.latest_human_labels(args.work)
    references = []
    for (source, center), label in labels.items():
        sequence = trajectory(pose.get(int(source), []), float(center))
        if sequence:
            references.append((int(source), float(center), label, sequence))
    positives = [item for item in references if item[2] == "putter"]
    negatives = [item for item in references if item[2] in {"non-shot", "nonPutter", "driver", "wood", "iron", "wedge"}]
    ranked = []
    for candidate in load_ranked(args.work, args.ranked):
        source = int(candidate["sourceIndex"])
        center = float(candidate["timeSeconds"])
        sequence = trajectory(pose.get(source, []), center)
        if not sequence:
            continue
        positive_distances = [
            dtw(sequence, reference[3])
            for reference in positives
            if args.allow_near_labels or reference[0] != source or abs(reference[1] - center) > 6.0
        ]
        negative_distances = [
            dtw(sequence, reference[3])
            for reference in negatives
            if args.allow_near_labels or reference[0] != source or abs(reference[1] - center) > 6.0
        ]
        if not positive_distances or not negative_distances:
            continue
        positive_distances.sort()
        negative_distances.sort()
        positive = sum(positive_distances[:args.topk]) / min(args.topk, len(positive_distances))
        negative = sum(negative_distances[:args.topk]) / min(args.topk, len(negative_distances))
        ranked.append({
            "sourceIndex": source,
            "timeSeconds": center,
            "score": negative - positive,
            "positiveDistance": positive,
            "negativeDistance": negative,
            "visionScore": candidate.get("score"),
            "roiMotion": candidate.get("roiMotion"),
            "matMotion": candidate.get("matMotion"),
            "audioPeakDb": candidate.get("audioPeakDb"),
        })
    ranked.sort(key=lambda row: row["score"], reverse=True)
    selected = []
    for candidate in ranked:
        if any(candidate["sourceIndex"] == other["sourceIndex"] and abs(candidate["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= args.limit:
            break
    output = {
        "schemaVersion": 1,
        "method": "DTW normalized hand-relative-to-core pose trajectory",
        "references": {"positive": len(positives), "negative": len(negatives)},
        "nearLabelsAllowed": args.allow_near_labels,
        "ranked": ranked,
        "selected": selected,
    }
    path = args.work / args.output
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["references"], ensure_ascii=False))
    print(f"ranked={len(ranked)} selected={len(selected)} output={path}")


if __name__ == "__main__":
    main()
