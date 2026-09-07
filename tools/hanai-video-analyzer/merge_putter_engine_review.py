#!/usr/bin/env python3
"""Combine engine review slices into the single user-facing queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--output", default="putter-engine-review-all.json")
    args = parser.parse_args()

    work = args.work
    merged = []
    seen = set()
    sources = ["putter-engine-review-30.json", "putter-engine-review-uncertain-15.json"]
    for name in sources:
        path = work / name
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for candidate in payload.get("candidates", []):
            cid = candidate.get("id")
            if cid in seen:
                continue
            seen.add(cid)
            item = dict(candidate)
            item["queueRank"] = len(merged) + 1
            merged.append(item)

    output = {
        "schemaVersion": 1,
        "method": "unified-engine-review-queue",
        "sourceSlices": sources,
        "candidates": merged,
    }
    (work / args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged {len(merged)} candidates -> {work / args.output}")


if __name__ == "__main__":
    main()
