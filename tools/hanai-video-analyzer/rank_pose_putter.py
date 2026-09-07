#!/usr/bin/env python3
"""Rank putter-like windows from a full-session Vision pose scan.

This is the offline validation/application companion to GolfPutterDetector.
It deliberately emits ranked candidates rather than silently converting labels.
Known human labels are used only to measure precision/recall and tune the
window, not to manufacture new ground truth.
"""

from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left, bisect_right
from pathlib import Path
from statistics import median


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def features(rows, start, end):
    xs = rows[start:end]
    if len(xs) < 5:
        return None
    hx = [r["handX"] for r in xs]
    hy = [r["handY"] for r in xs]
    cx = [r["coreX"] for r in xs]
    cy = [r["coreY"] for r in xs]
    x_range = max(hx) - min(hx)
    y_range = max(hy) - min(hy)
    core_range = math.hypot(max(cx) - min(cx), max(cy) - min(cy))
    path = 0.0
    peak = 0.0
    reversals = 0
    previous_dx = None
    for a, b in zip(xs, xs[1:]):
        dt = max(0.05, b["time"] - a["time"])
        dx = b["handX"] - a["handX"]
        dy = b["handY"] - a["handY"]
        path += math.hypot(dx, dy)
        peak = max(peak, math.hypot(dx, dy) / dt)
        if previous_dx is not None and abs(previous_dx) >= 0.004 and abs(dx) >= 0.004:
            if (previous_dx < 0) != (dx < 0):
                reversals += 1
        previous_dx = dx
    return {
        "sampleCount": len(xs),
        "handRangeX": x_range,
        "handRangeY": y_range,
        "handPath": path,
        "coreRange": core_range,
        "peakHandSpeed": peak,
        "reversalCount": reversals,
        "verticalRatio": y_range / max(x_range, 0.01),
    }


def score(f):
    # Compact lateral stroke: enough horizontal travel, little vertical/body motion,
    # and at least one reversal. Smooth clipped terms keep ranking stable.
    lateral = min(1.0, max(0.0, (f["handRangeX"] - 0.035) / 0.20))
    not_large = min(1.0, max(0.0, (0.34 - f["handRangeX"]) / 0.25))
    body = min(1.0, max(0.0, (0.27 - f["coreRange"]) / 0.18))
    vertical = min(1.0, max(0.0, (1.25 - f["verticalRatio"]) / 0.90))
    reversal = min(1.0, f["reversalCount"] / 2.0)
    path = min(1.0, max(0.0, (f["handPath"] - 0.05) / 0.75))
    return 0.28 * lateral + 0.17 * not_large + 0.23 * body + 0.14 * vertical + 0.14 * reversal + 0.04 * path


def build_windows(pose, step):
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    windows = []
    for source, rows in grouped.items():
        rows.sort(key=lambda r: r["time"])
        times = [r["time"] for r in rows]
        if not times:
            continue
        t = times[0]
        while t <= times[-1]:
            lo = bisect_left(times, t - 1.25)
            hi = bisect_right(times, t + 1.25)
            f = features(rows, lo, hi)
            if f:
                windows.append({"sourceIndex": source, "timeSeconds": t, "score": score(f), **f})
            t += step
    return windows


def labels(work: Path):
    result = {}
    for name in ("analysis.json", "putter-review.json"):
        path = work / name
        if not path.exists():
            continue
        for c in load(path).get("candidates", []):
            label = c.get("label")
            if label:
                result[(int(c["sourceIndex"]), round(float(c["timeSeconds"]), 2))] = label
    return result


def hit_count(selected, known, positive):
    hits = 0
    for c in selected:
        for (source, t), label in known.items():
            if label in positive and source == c["sourceIndex"] and abs(t - c["timeSeconds"]) <= 3.0:
                hits += 1
                break
    return hits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--input", default="full-pose-samples-0.2.json")
    parser.add_argument("--output", default="full-pose-putter-ranked-0.2.json")
    parser.add_argument("--step", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    pose = load(args.work / args.input)
    windows = build_windows(pose, args.step)
    windows.sort(key=lambda x: x["score"], reverse=True)
    selected = []
    for candidate in windows:
        if any(candidate["sourceIndex"] == x["sourceIndex"] and abs(candidate["timeSeconds"] - x["timeSeconds"]) < 3.0 for x in selected):
            continue
        selected.append(candidate)
        if len(selected) >= args.limit:
            break
    known = labels(args.work)
    positive = {"putter"}
    negatives = {"non-shot", "nonPutter", "driver", "wood", "iron", "wedge"}
    output = {
        "schemaVersion": 2,
        "method": "0.2s Vision pose; 2.5s centered window; compact lateral stroke ranking",
        "candidateCount": len(windows),
        "selected": selected,
        "validation": {
            "knownLabeledCenters": len(known),
            "putterHitsWithin3s": hit_count(selected, known, positive),
            "negativeHitsWithin3s": hit_count(selected, known, negatives),
            "note": "Overlap metrics are validation evidence, not a claim of true shot count.",
        },
    }
    (args.work / args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["validation"], ensure_ascii=False))
    print(f"windows={len(windows)} selected={len(selected)} output={args.work / args.output}")


if __name__ == "__main__":
    main()
