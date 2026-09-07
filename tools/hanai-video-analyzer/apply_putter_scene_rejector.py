#!/usr/bin/env python3
"""Apply a second-stage scene-motion rejector to an existing putter ranking.

The first-stage ranker looks for putter-like motion. This stage removes clips
whose whole scene ROI moves unusually strongly, which is a common non-shot
pattern. It is kept as a separate artifact until block-holdout evidence justifies
promotion into the canonical ranker.
"""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path

import rank_pose_putter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--ranked", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-roi-motion", type=float, default=9.5)
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--weak-hand-range", type=float, default=0.25)
    parser.add_argument("--weak-hand-path", type=float, default=0.60)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    source = args.work / args.ranked
    data = json.loads(source.read_text(encoding="utf-8"))
    ranked = data if isinstance(data, list) else data.get("selected", [])
    pose_data = json.loads((args.work / args.pose).read_text(encoding="utf-8"))
    grouped = {}
    for row in pose_data["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["time"])

    def putter_evidence(item):
        rows = grouped.get(int(item["sourceIndex"]), [])
        times = [row["time"] for row in rows]
        center = float(item["timeSeconds"])
        lo = max(0, bisect.bisect_left(times, center - 0.8))
        hi = min(len(rows), bisect.bisect_right(times, center + 0.8))
        return rank_pose_putter.features(rows, lo, hi) or {}

    kept = [
        item for item in ranked
        if not (
            float(item.get("roiMotion") or 0.0) > args.max_roi_motion
            and float(putter_evidence(item).get("handRangeX") or 0.0) < args.weak_hand_range
            and float(putter_evidence(item).get("handPath") or 0.0) < args.weak_hand_path
        )
    ][: args.limit]
    output = {
        "schemaVersion": 1,
        "method": "first-stage putter ranking + scene-motion rejector",
        "source": args.ranked,
        "maxRoiMotion": args.max_roi_motion,
        "weakHandRange": args.weak_hand_range,
        "weakHandPath": args.weak_hand_path,
        "inputCount": len(ranked),
        "selected": kept,
    }
    path = args.work / args.output
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"input={len(ranked)} selected={len(kept)} maxRoiMotion={args.max_roi_motion} output={path}")


if __name__ == "__main__":
    main()
