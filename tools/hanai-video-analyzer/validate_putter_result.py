#!/usr/bin/env python3
"""Validate a ranked putter result against the latest human label history."""

from __future__ import annotations

import argparse
import collections
import json
import re
from pathlib import Path


def load_latest_labels(work: Path, include_evicted_engine_labels: bool = False):
    candidates = {}
    for path in sorted(work.glob("analysis.json")):
        candidates.update({c["id"]: c for c in json.loads(path.read_text(encoding="utf-8")).get("candidates", [])})
    review = work / "putter-review.json"
    if review.exists():
        candidates.update({c["id"]: c for c in json.loads(review.read_text(encoding="utf-8")).get("candidates", [])})
    final = work / "putter-final-30.json"
    if final.exists():
        candidates.update({c["id"]: c for c in json.loads(final.read_text(encoding="utf-8")).get("candidates", [])})
    engine = work / "putter-engine-review-30.json"
    if engine.exists():
        candidates.update({c["id"]: c for c in json.loads(engine.read_text(encoding="utf-8")).get("candidates", [])})
    uncertain = work / "putter-engine-review-uncertain-15.json"
    if uncertain.exists():
        candidates.update({c["id"]: c for c in json.loads(uncertain.read_text(encoding="utf-8")).get("candidates", [])})

    latest = {}
    for name in ("label-history.jsonl", "putter-label-history.jsonl", "putter-engine-label-history.jsonl"):
        path = work / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                latest[event["id"]] = event["label"]
    references = []
    for identifier, label in latest.items():
        candidate = candidates.get(identifier)
        if candidate is not None:
            references.append((int(candidate["sourceIndex"]), float(candidate["timeSeconds"]), label))
            continue
        # Engine review IDs are stable and encode the source plus tenths of a
        # second. Preserve those labels even after a later ranking evicts the
        # candidate from the visible review file.
        match = re.fullmatch(r"engine-(\d+)-(\d+)", identifier)
        if include_evicted_engine_labels and match:
            references.append((int(match.group(1)), int(match.group(2)) / 10.0, label))
    return references


def label_for(candidate, references):
    labels = [
        label
        for source, time, label in references
        if source == int(candidate["sourceIndex"])
        and abs(time - float(candidate["timeSeconds"])) <= 3.0
    ]
    if "putter" in labels:
        return "putter"
    if any(label in {"non-shot", "nonPutter"} for label in labels):
        return "negative"
    if any(label in {"driver", "wood", "iron", "wedge"} for label in labels):
        return "other-club"
    return "unlabeled"


def label_for_one_to_one(candidate, references, used, tolerance):
    matches = [
        (abs(time - float(candidate["timeSeconds"])), index, label)
        for index, (source, time, label) in enumerate(references)
        if index not in used
        and source == int(candidate["sourceIndex"])
        and abs(time - float(candidate["timeSeconds"])) <= tolerance
    ]
    if not matches:
        return "unlabeled"
    matches.sort(key=lambda item: (item[0], item[1]))
    distance = matches[0][0]
    tied = [label for item_distance, _, label in matches if item_distance == distance]
    used.add(matches[0][1])
    if len(set(tied)) > 1:
        return "ambiguous"
    label = tied[0]
    if label == "putter":
        return "putter"
    if label in {"non-shot", "nonPutter"}:
        return "negative"
    if label in {"driver", "wood", "iron", "wedge"}:
        return "other-club"
    return "unlabeled"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("ranked", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tolerance", type=float, default=3.0)
    parser.add_argument(
        "--one-to-one",
        action="store_true",
        help="match each human label to at most one ranked candidate",
    )
    parser.add_argument(
        "--include-evicted-engine-labels",
        action="store_true",
        help="restore stable engine labels no longer present in the visible review file",
    )
    args = parser.parse_args()

    ranked_data = json.loads(args.ranked.read_text(encoding="utf-8"))
    if isinstance(ranked_data, list):
        ranked = ranked_data
    else:
        # Ranked experiments use `selected`; the one-by-one review export uses
        # `candidates`. Accept both so the review artifact itself is verifiable.
        ranked = ranked_data.get("selected", ranked_data.get("candidates", []))
    references = load_latest_labels(
        args.work,
        include_evicted_engine_labels=args.include_evicted_engine_labels,
    )
    result = {
        "schemaVersion": 1,
        "rankedFile": str(args.ranked),
        "latestReferenceCount": len(references),
        "top": {},
        "note": "Overlap with human labels is validation evidence, not automatic ground truth or shot count.",
    }
    for limit in (30, 50, 100):
        used = set()
        if args.one_to_one:
            labels = [
                label_for_one_to_one(candidate, references, used, args.tolerance)
                for candidate in ranked[:limit]
            ]
        else:
            labels = [label_for(candidate, references) for candidate in ranked[:limit]]
        counts = collections.Counter(labels)
        result["top"][str(limit)] = {
            "putter": counts["putter"],
            "negative": counts["negative"],
            "otherClub": counts["other-club"],
            "ambiguous": counts["ambiguous"],
            "unlabeled": counts["unlabeled"],
        }
    result["matching"] = {
        "mode": "one-to-one" if args.one_to_one else "window-overlap",
        "toleranceSeconds": args.tolerance,
        "note": "One-to-one mode prevents one human label from crediting multiple ranked candidates.",
    }
    output_name = args.output or (
        Path("putter-validation-current.json")
        if args.ranked.name == "putter-feature-temporal-action-quiet-mat-current.json"
        else Path(f"{args.ranked.stem}-validation.json")
    )
    output = args.work / output_name
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
