#!/usr/bin/env python3
"""Fuse independent Vision and pose-DTW rankings without changing either ranker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def key(item):
    return int(item["sourceIndex"]), round(float(item["timeSeconds"]), 3)


def read_ranked(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("ranked", data.get("selected", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("vision", type=Path)
    parser.add_argument("dtw", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--vision-weight", type=float, default=0.6)
    parser.add_argument("--dtw-weight", type=float, default=0.4)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    vision = read_ranked(args.vision)
    dtw = read_ranked(args.dtw)
    vision_rank = {key(item): index for index, item in enumerate(vision)}
    dtw_rank = {key(item): index for index, item in enumerate(dtw)}
    all_keys = set(vision_rank) & set(dtw_rank)
    fused = []
    for candidate in vision:
        identifier = key(candidate)
        if identifier not in all_keys:
            continue
        score = args.vision_weight / (60.0 + vision_rank[identifier]) + args.dtw_weight / (60.0 + dtw_rank[identifier])
        fused.append({**candidate, "score": score, "visionRank": vision_rank[identifier] + 1, "dtwRank": dtw_rank[identifier] + 1})
    fused.sort(key=lambda item: item["score"], reverse=True)
    selected = []
    for candidate in fused:
        if any(candidate["sourceIndex"] == other["sourceIndex"] and abs(candidate["timeSeconds"] - other["timeSeconds"]) < 3.0 for other in selected):
            continue
        selected.append(candidate)
        if len(selected) >= args.limit:
            break
    args.output.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"common={len(all_keys)} ranked={len(fused)} selected={len(selected)} output={args.output}")


if __name__ == "__main__":
    main()
