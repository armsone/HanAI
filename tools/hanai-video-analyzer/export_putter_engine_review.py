#!/usr/bin/env python3
"""Convert ranked putter-engine output into a reviewable UI candidate file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def stable_id(item: dict) -> str:
    return f"engine-{int(item['sourceIndex'])}-{round(float(item['timeSeconds']) * 10):08d}"


def load_history(work: Path) -> dict[str, str]:
    history_path = work / "putter-engine-label-history.jsonl"
    if not history_path.exists():
        return {}
    latest = {}
    for line in history_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            event = json.loads(line)
            latest[event["id"]] = event["label"]
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--ranked", default="putter-feature-temporal-action-quiet-mat-replay-current.json")
    parser.add_argument("--output", default="putter-engine-review-30.json")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--strategy", choices=("top", "uncertain"), default="top")
    args = parser.parse_args()

    ranked = json.loads((args.work / args.ranked).read_text(encoding="utf-8"))
    ranked_items = ranked if isinstance(ranked, list) else ranked.get("selected", [])
    existing = {}
    history = load_history(args.work)
    output_path = args.work / args.output
    if output_path.exists():
        existing = {
            row["id"]: row
            for row in json.loads(output_path.read_text(encoding="utf-8")).get("candidates", [])
            if row.get("id")
        }

    if args.strategy == "uncertain":
        ranked_items = sorted(ranked_items, key=lambda item: abs(float(item.get("score", 0.0))))
    candidates = []
    for rank, item in enumerate(ranked_items[: max(0, args.limit)], start=1):
        identifier = stable_id(item)
        old = existing.get(identifier, {})
        candidates.append({
            "id": identifier,
            "rank": rank,
            "sourceIndex": int(item["sourceIndex"]),
            "timeSeconds": float(item["timeSeconds"]),
            "peakDb": float(item.get("audioPeakDb", -40.0)),
            "score": float(item.get("score", 0.0)),
            "positiveDistance": float(item.get("positiveDistance", 0.0)),
            "negativeDistance": float(item.get("negativeDistance", 0.0)),
            "label": history.get(identifier, old.get("label")),
            "reviewed": bool(history.get(identifier, old.get("reviewed", False))),
        })

    output = {
        "schemaVersion": 1,
        "source": args.ranked,
        "method": f"dedicated putter engine {args.strategy} review candidates",
        "candidates": candidates,
    }
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"exported={len(candidates)} output={output_path}")


if __name__ == "__main__":
    main()
