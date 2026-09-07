#!/usr/bin/env python3
"""Search short pose-window configurations against existing human labels."""

from __future__ import annotations

import argparse
import bisect
import json
from pathlib import Path

import rank_pose_putter as ranker


def windows(pose, half_window, step):
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    result = []
    for source, rows in grouped.items():
        rows.sort(key=lambda row: row["time"])
        times = [row["time"] for row in rows]
        for time in times[::max(1, round(step / 0.2))]:
            lo = bisect.bisect_left(times, time - half_window)
            hi = bisect.bisect_right(times, time + half_window)
            features = ranker.features(rows, lo, hi)
            if features:
                result.append({"sourceIndex": source, "timeSeconds": time, **features, "score": ranker.score(features)})
    return result


def hits(selected, labels, accepted):
    return sum(
        any(source == item["sourceIndex"] and abs(time - item["timeSeconds"]) <= 3.0 for item in selected)
        for (source, time), label in labels.items()
        if label in accepted
    )


def select(candidates, limit):
    selected = []
    for item in sorted(candidates, key=lambda row: row["score"], reverse=True):
        if any(item["sourceIndex"] == other["sourceIndex"] and abs(item["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(item)
        if len(selected) == limit:
            break
    return selected


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    pose = ranker.load(args.work / "full-pose-samples-0.2.json")
    labels = ranker.labels(args.work)
    trials = []
    for half_window in (0.4, 0.6, 0.8, 1.0, 1.25):
        for step in (0.2, 0.4, 0.6):
            candidates = windows(pose, half_window, step)
            selected = select(candidates, 30)
            putter = hits(selected, labels, {"putter"})
            negative = hits(selected, labels, {"non-shot", "nonPutter"})
            other = hits(selected, labels, {"driver", "wood", "iron", "wedge"})
            trials.append({
                "halfWindowSeconds": half_window,
                "stepSeconds": step,
                "selected": len(selected),
                "putterHits": putter,
                "negativeHits": negative,
                "otherClubHits": other,
                "objective": putter * 5 - negative * 2 - other,
            })
    trials.sort(key=lambda row: (row["objective"], row["putterHits"], -row["negativeHits"]), reverse=True)
    output = {
        "schemaVersion": 1,
        "method": "0.2s full pose scan; sliding centered windows; no label rewriting",
        "knownLabelCount": len(labels),
        "best": trials[:10],
        "allTrials": trials,
    }
    path = args.work / "putter-parameter-search-0.2.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"knownLabelCount": len(labels), "best": trials[:3]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
