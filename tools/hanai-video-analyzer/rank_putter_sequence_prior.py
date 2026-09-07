#!/usr/bin/env python3
"""Re-rank putter candidates with a weak next-shot sequence prior.

Some screen-golf sessions place a short putt before a larger shot. This is only
a tie-breaker: candidates still need the dedicated putter shape score, and the
prior never labels a candidate automatically.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
from pathlib import Path

import rank_pose_putter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--ranked", default="full-pose-putter-composite-0.2.json")
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--analysis", default="analysis.json")
    parser.add_argument("--output", default="putter-sequence-prior-experiment.json")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--weight", type=float, default=0.12)
    args = parser.parse_args()

    pose = json.loads((args.work / args.pose).read_text(encoding="utf-8"))
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    anchors = {}
    for source, rows in grouped.items():
        rows.sort(key=lambda row: row["time"])
        times = [row["time"] for row in rows]
        values = []
        for center in times[::2]:
            lo = bisect.bisect_left(times, center - 0.8)
            hi = bisect.bisect_right(times, center + 0.8)
            feature = rank_pose_putter.features(rows, lo, hi)
            if feature and (
                feature["handRangeX"] >= 0.45
                or feature["handPath"] >= 2.2
                or feature["coreRange"] >= 0.45
            ):
                values.append(center)
        anchors[source] = sorted(set(values))

    analysis = json.loads((args.work / args.analysis).read_text(encoding="utf-8"))
    for candidate in analysis.get("candidates", []):
        if float(candidate.get("peakDb", -99.0)) >= -12.0:
            anchors.setdefault(int(candidate["sourceIndex"]), []).append(float(candidate["timeSeconds"]))
    anchors = {source: sorted(set(times)) for source, times in anchors.items()}

    ranked_data = json.loads((args.work / args.ranked).read_text(encoding="utf-8"))
    ranked = ranked_data if isinstance(ranked_data, list) else ranked_data.get("selected", [])
    output_rows = []
    for item in ranked:
        source = int(item["sourceIndex"])
        center = float(item["timeSeconds"])
        index = bisect.bisect_right(anchors.get(source, []), center + 2.0)
        next_anchor = anchors.get(source, [])[index] if index < len(anchors.get(source, [])) else None
        distance = (next_anchor - center) if next_anchor is not None else None
        prior = math.exp(-distance / 15.0) if distance is not None and distance <= 45.0 else 0.0
        output_rows.append({
            **item,
            "nextAnchorSeconds": distance,
            "sequencePrior": prior,
            "score": float(item.get("score", 0.0)) + args.weight * prior,
        })
    output_rows.sort(key=lambda row: row["score"], reverse=True)
    selected = []
    for item in output_rows:
        if any(item["sourceIndex"] == other["sourceIndex"] and abs(item["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(item)
        if len(selected) >= args.limit:
            break
    output = {
        "schemaVersion": 1,
        "method": "putter pose rank + weak next-large-shot sequence prior",
        "weight": args.weight,
        "ranked": output_rows,
        "selected": selected,
    }
    path = args.work / args.output
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"anchors={sum(len(v) for v in anchors.values())} ranked={len(output_rows)} selected={len(selected)} output={path}")


if __name__ == "__main__":
    main()
