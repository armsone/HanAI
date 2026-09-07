#!/bin/zsh
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "usage: $0 /absolute/path/to/Work/hanai-video-analysis [replay]" >&2
  exit 2
fi

work="$1"
mode="${2:-holdout}"
if [[ "$mode" != "holdout" && "$mode" != "replay" ]]; then
  echo "mode must be holdout or replay" >&2
  exit 2
fi

root="$(cd "$(dirname "$0")/../.." && pwd)"
binary="$(mktemp /tmp/hanai-putter-engine.XXXXXX)"
trap 'rm -f "$binary"' EXIT

if [[ "$mode" == "replay" ]]; then
  output="$work/putter-feature-temporal-action-quiet-mat-replay-current.json"
  replay_args=(--allow-near-labels)
else
  output="$work/putter-feature-temporal-action-quiet-mat-current.json"
  replay_args=()
fi

python3 "$root/tools/hanai-video-analyzer/apply_putter_engine.py" \
  "$work" --limit 1000 --spacing 3.0

swiftc "$root/tools/hanai-video-analyzer/rank_putter_featureprints.swift" \
  -o "$binary"

"$binary" "$work" \
  "$output" \
  action --quiet --mat --all-negative --negative-gap=6 --topk=3 \
  "${replay_args[@]}"

python3 "$root/tools/hanai-video-analyzer/validate_putter_result.py" \
  "$work" \
  "$output"

if [[ "$mode" == "replay" ]]; then
  python3 "$root/tools/hanai-video-analyzer/apply_putter_scene_rejector.py" \
    "$work" \
    --ranked "$(basename "$output")" \
    --output putter-scene-rejector-context-replay.json \
    --max-roi-motion 9.5 \
    --limit 100
  python3 "$root/tools/hanai-video-analyzer/export_putter_engine_review.py" \
    "$work" \
    --ranked putter-scene-rejector-context-replay.json \
    --output putter-engine-review-30.json \
    --limit 30
  python3 "$root/tools/hanai-video-analyzer/export_putter_engine_review.py" \
    "$work" \
    --ranked "$(basename "$output")" \
    --output putter-engine-review-uncertain-15.json \
    --limit 15 \
    --strategy uncertain
fi

python3 "$root/tools/hanai-video-analyzer/merge_putter_engine_review.py" \
  "$work"
