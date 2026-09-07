#!/usr/bin/env python3
"""Apply the dedicated putter candidate engine to a full pose scan.

The engine is intentionally a candidate ranker, not an automatic labeler:
pose supplies the short lateral stroke, ROI motion rejects whole-frame scene
changes, and audio is a weak tie-breaker because quiet waiting scenes exist.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import re
import statistics
from pathlib import Path

import rank_pose_putter as pose_ranker


def parse_signal(path: Path, pattern: str):
    if not path.exists():
        return []
    values = []
    timestamp = 0.0
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.search(r"pts_time[:=]([0-9.]+)", line)
        if match:
            timestamp = float(match.group(1))
        match = re.search(pattern, line)
        if match:
            values.append((timestamp, float(match.group(1))))
    return values


def max_near(values, times, center, default):
    if not values:
        return default
    lo = bisect.bisect_left(times, center - 1.5)
    hi = bisect.bisect_right(times, center + 1.5)
    return max((value for _, value in values[lo:hi]), default=default)


def latest_human_labels(work: Path):
    latest = {}
    for history_name in ("label-history.jsonl", "putter-label-history.jsonl", "putter-engine-label-history.jsonl"):
        history = work / history_name
        if not history.exists():
            continue
        for line in history.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            latest[event["id"]] = event["label"]
    candidates_by_id = {}
    paths = [
        work / "putter-review.json",
        work / "putter-final-30.json",
        work / "putter-engine-review-30.json",
        work / "analysis.json",
    ]
    for path in paths:
        if not path.exists():
            continue
        for candidate in json.loads(path.read_text(encoding="utf-8")).get("candidates", []):
            candidates_by_id[candidate["id"]] = candidate
    return {
        (candidates_by_id[identifier]["sourceIndex"], candidates_by_id[identifier]["timeSeconds"]): label
        for identifier, label in latest.items()
        if identifier in candidates_by_id
    }


def candidate_windows(pose, roi_by_source, mat_by_source, audio_by_source, recall_mode=False):
    grouped = {}
    for row in pose["samples"]:
        grouped.setdefault(int(row["sourceIndex"]), []).append(row)
    windows = []
    for source, rows in grouped.items():
        rows.sort(key=lambda row: row["time"])
        times = [row["time"] for row in rows]
        for center in times[::2]:
            lo = bisect.bisect_left(times, center - 0.8)
            hi = bisect.bisect_right(times, center + 0.8)
            features = pose_ranker.features(rows, lo, hi)
            if not features:
                continue
            # Calibrated from the high-resolution putter/non-putter review:
            # lateral short travel, compact core, one reversal, low vertical ratio.
            if recall_mode:
                passes_shape = (
                    0.03 <= features["handRangeX"] <= 0.50
                    and features["coreRange"] <= 0.45
                    and 0.10 <= features["handPath"] <= 2.5
                    and features["verticalRatio"] <= 1.25
                    and (
                        features["reversalCount"] >= 1
                        or features["handPath"] >= 0.10
                    )
                )
            else:
                passes_shape = (
                    0.05 <= features["handRangeX"] <= 0.30
                    and features["coreRange"] <= 0.32
                    and 0.08 <= features["handPath"] <= 2.0
                    and features["reversalCount"] >= 1
                    and features["verticalRatio"] <= 0.60
                )
            if not passes_shape:
                continue
            roi = roi_by_source.get(source, [])
            mat = mat_by_source.get(source, [])
            audio = audio_by_source.get(source, [])
            roi_motion = max_near(roi, [time for time, _ in roi], center, 0.0)
            mat_motion = max_near(mat, [time for time, _ in mat], center, 0.0)
            peak_db = max_near(audio, [time for time, _ in audio], center, -40.0)
            roi_score = math.exp(-((roi_motion - 9.5) / 5.0) ** 2) if roi_motion else 0.0
            quiet_score = math.exp(-((peak_db + 12.0) / 10.0) ** 2)
            pose_score = pose_ranker.score(features)
            windows.append({
                "sourceIndex": source,
                "timeSeconds": center,
                "score": 0.7 * pose_score + 0.2 * quiet_score + 0.1 * roi_score,
                "poseScore": pose_score,
                "roiMotion": roi_motion,
                "matMotion": mat_motion,
                "audioPeakDb": peak_db,
                **features,
            })
    return windows


def select(windows, limit, spacing_seconds=3.0):
    selected = []
    for item in sorted(windows, key=lambda row: row["score"], reverse=True):
        if any(item["sourceIndex"] == other["sourceIndex"] and abs(item["timeSeconds"] - other["timeSeconds"]) < spacing_seconds for other in selected):
            continue
        selected.append(item)
        if len(selected) == limit:
            break
    return selected


def hit_count(selected, labels, accepted):
    return sum(
        any(source == item["sourceIndex"] and abs(time - item["timeSeconds"]) <= 3.0 for item in selected)
        for (source, time), label in labels.items()
        if label in accepted
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--pose", default="full-pose-samples-0.2.json")
    parser.add_argument("--roi0", type=Path, default=Path("/tmp/hanai-roi-motion-0.txt"))
    parser.add_argument("--roi1", type=Path, default=Path("/tmp/hanai-roi-motion.txt"))
    parser.add_argument("--mat0", type=Path, default=Path("/tmp/hanai-mat-motion-0.txt"))
    parser.add_argument("--mat1", type=Path, default=Path("/tmp/hanai-mat-motion.txt"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--spacing", type=float, default=3.0)
    parser.add_argument(
        "--recall",
        action="store_true",
        help="use a wider stroke-shape gate for a recall-oriented candidate pool",
    )
    parser.add_argument("--output", type=Path, default=Path("full-pose-putter-composite-0.2.json"))
    args = parser.parse_args()
    roi_by_source = {
        0: parse_signal(args.roi0, r"YAVG=([0-9.]+)"),
        1: parse_signal(args.roi1, r"YAVG=([0-9.]+)"),
    }
    mat_by_source = {
        0: parse_signal(args.mat0, r"YAVG=([0-9.]+)"),
        1: parse_signal(args.mat1, r"YAVG=([0-9.]+)"),
    }
    audio_by_source = {
        0: parse_signal(args.work / "audio-0.txt", r"Peak_level=([-0-9.]+)"),
        1: parse_signal(args.work / "audio-1.txt", r"Peak_level=([-0-9.]+)"),
    }
    pose = pose_ranker.load(args.work / args.pose)
    candidates = candidate_windows(
        pose, roi_by_source, mat_by_source, audio_by_source, recall_mode=args.recall
    )
    selected = select(candidates, args.limit, args.spacing)
    labels = latest_human_labels(args.work)
    validation = {
        "latestHumanLabelCount": len(labels),
        "putterHitsWithin3s": hit_count(selected, labels, {"putter"}),
        "negativeHitsWithin3s": hit_count(selected, labels, {"non-shot", "nonPutter"}),
        "otherClubHitsWithin3s": hit_count(selected, labels, {"driver", "wood", "iron", "wedge"}),
        "note": "Hits are overlap validation, not automatic truth or shot count.",
    }
    output = {
        "schemaVersion": 1,
        "method": (
            "recall-oriented wider pose gate + lower-frame ROI motion + weak quiet-audio tie-breaker"
            if args.recall
            else "pose short lateral stroke + lower-frame ROI motion + weak quiet-audio tie-breaker"
        ),
        "candidateCount": len(candidates),
        "selected": selected,
        "validation": validation,
    }
    path = args.work / args.output
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(validation, ensure_ascii=False))
    print(f"candidates={len(candidates)} selected={len(selected)} output={path}")


if __name__ == "__main__":
    main()
