#!/usr/bin/env python3
"""Experiment with pre/post stroke context without changing the canonical ranker."""

from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path

import rank_pose_putter


def context_score(rows, center):
    times = [row["time"] for row in rows]
    windows = []
    for left, right in ((center - 1.8, center - 0.8), (center + 0.8, center + 1.8)):
        lo = bisect.bisect_left(times, left)
        hi = bisect.bisect_right(times, right)
        feature = rank_pose_putter.features(rows, lo, hi)
        if not feature:
            return 0.0
        windows.append(feature)
    pre, post = windows
    stillness = math.exp(-0.8 * (pre["handPath"] + post["handPath"]))
    body_stillness = math.exp(-1.2 * (pre["coreRange"] + post["coreRange"]))
    return 0.65 * stillness + 0.35 * body_stillness


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--ranked", default="putter-feature-temporal-action-quiet-mat-current.json")
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--output", default="putter-context-experiment.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--context-weight", type=float, default=0.3)
    args = parser.parse_args()
    pose = json.loads((args.work / args.pose).read_text(encoding="utf-8"))
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["time"])
    ranked_data = json.loads((args.work / args.ranked).read_text(encoding="utf-8"))
    ranked = ranked_data if isinstance(ranked_data, list) else ranked_data.get("selected", [])
    scored = []
    for item in ranked:
        rows = grouped.get(int(item["sourceIndex"]), [])
        if not rows:
            continue
        context = context_score(rows, float(item["timeSeconds"]))
        scored.append({**item, "contextScore": context})
    vision_order = {(
        int(item["sourceIndex"]), round(float(item["timeSeconds"]), 3)
    ): index for index, item in enumerate(scored)}
    context_order = {
        (int(item["sourceIndex"]), round(float(item["timeSeconds"]), 3)): index
        for index, item in enumerate(sorted(scored, key=lambda x: x["contextScore"], reverse=True))
    }
    fused = []
    for item in scored:
        key = (int(item["sourceIndex"]), round(float(item["timeSeconds"]), 3))
        fused.append({
            **item,
            "score": (1.0 - args.context_weight) / (60 + vision_order[key])
            + args.context_weight / (60 + context_order[key]),
        })
    fused.sort(key=lambda x: x["score"], reverse=True)
    selected = []
    for item in fused:
        if any(item["sourceIndex"] == other["sourceIndex"] and abs(item["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(item)
        if len(selected) >= args.limit:
            break
    args.work.joinpath(args.output).write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ranked={len(scored)} selected={len(selected)} output={args.work / args.output}")


if __name__ == "__main__":
    main()
