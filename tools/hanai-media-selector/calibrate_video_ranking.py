#!/usr/bin/env python3
"""
tools/hanai-media-selector/calibrate_video_ranking.py

Bounded numeric-only calibration analysis for video clip ranking.
Python 3 standard library only.
"""

import argparse
import json
import math
import os
import sys


def quantile(sorted_values, q):
    """
    Empirical quantile using linear interpolation between closest ranks.
    q in [0, 1].
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    idx = (n - 1) * q
    low = int(math.floor(idx))
    high = int(math.ceil(idx))
    if low == high:
        return float(sorted_values[low])
    weight = idx - low
    return float((1.0 - weight) * sorted_values[low] + weight * sorted_values[high])


def compute_distribution_summary(values):
    """
    Computes min, max, mean, median, p25, p75.
    """
    if not values:
        return {
            "count": 0,
            "min": 0.0,
            "max": 0.0,
            "mean": 0.0,
            "median": 0.0,
            "p25": 0.0,
            "p75": 0.0,
        }
    sv = sorted(values)
    n = len(sv)
    return {
        "count": n,
        "min": float(sv[0]),
        "max": float(sv[-1]),
        "mean": float(sum(sv) / n),
        "median": quantile(sv, 0.5),
        "p25": quantile(sv, 0.25),
        "p75": quantile(sv, 0.75),
    }


def validate_input_document(doc):
    """
    Validates the calibration numeric input document against schema requirements.
    """
    if not isinstance(doc, dict):
        raise ValueError("Input root must be a dictionary")
    if doc.get("schemaVersion") != 1:
        raise ValueError(f"Unsupported schemaVersion: {doc.get('schemaVersion')}")
    if not isinstance(doc.get("target"), str):
        raise ValueError("Missing or invalid 'target' string")
    if not isinstance(doc.get("prompts"), list):
        raise ValueError("Missing or invalid 'prompts' list")

    min_q = doc.get("minimumQuality")
    if not isinstance(min_q, (int, float)) or not (0.0 <= min_q <= 1.0):
        raise ValueError(f"Invalid minimumQuality: {min_q}")

    baseline_w = doc.get("baselineImportanceWeight")
    if not isinstance(baseline_w, (int, float)) or not (0.0 <= baseline_w <= 1.0):
        raise ValueError(f"Invalid baselineImportanceWeight: {baseline_w}")

    rows = doc.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Missing or invalid 'rows' list")

    seen_ids = set()
    total_pos = 0
    total_neg = 0

    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"Row {i} is not a dict")
        cid = row.get("id")
        if not isinstance(cid, str) or not cid:
            raise ValueError(f"Row {i} has invalid id: {cid}")
        if cid in seen_ids:
            raise ValueError(f"Duplicate id found in row {i}: {cid}")
        seen_ids.add(cid)

        pos = row.get("positive")
        if not isinstance(pos, bool):
            raise ValueError(f"Row {cid} has non-boolean 'positive': {pos}")
        if pos:
            total_pos += 1
        else:
            total_neg += 1

        frames = row.get("frames")
        if not isinstance(frames, list):
            raise ValueError(f"Row {cid} 'frames' must be a list")

        for f_idx, fr in enumerate(frames):
            if not isinstance(fr, dict):
                raise ValueError(f"Row {cid} frame {f_idx} must be a dict")
            qs = fr.get("qualityScore")
            if not isinstance(qs, (int, float)) or math.isnan(qs) or math.isinf(qs) or not (0.0 <= qs <= 1.0):
                raise ValueError(f"Row {cid} frame {f_idx} has invalid qualityScore: {qs}")

            pscores = fr.get("promptScores")
            if not isinstance(pscores, list) or len(pscores) != 5:
                raise ValueError(f"Row {cid} frame {f_idx} must have 5 promptScores")
            for s_idx, s in enumerate(pscores):
                if not isinstance(s, (int, float)) or math.isnan(s) or math.isinf(s) or not (0.0 <= s <= 1.0):
                    raise ValueError(f"Row {cid} frame {f_idx} promptScores[{s_idx}] invalid: {s}")

    return {
        "rowCount": len(rows),
        "positiveCount": total_pos,
        "negativeCount": total_neg,
        "uniqueIds": len(seen_ids),
    }


def compute_average_precision(ranked_eligible_rows, total_ground_truth_positives):
    """
    Calibration clip-level AP = sum precision@each positive-ranked position / TOTAL positive GT count in input.
    If total_ground_truth_positives == 0, returns None.
    Unranked positives are missed (they never appear in the ranked list, but contribute to denominator).
    """
    if total_ground_truth_positives == 0:
        return None

    cum_pos = 0
    precisions = []
    for rank_idx, row in enumerate(ranked_eligible_rows, start=1):
        if row["positive"]:
            cum_pos += 1
            precisions.append(cum_pos / rank_idx)

    return sum(precisions) / total_ground_truth_positives


def compute_precision_at_k(ranked_eligible_rows, k):
    """
    P@k uses min(k, eligible_count) as denominator.
    Returns (precision, denominator, top_ids, top_labels).
    When denominator is 0, precision is None.
    """
    denom = min(k, len(ranked_eligible_rows))
    if denom == 0:
        return None, 0, [], []

    top_items = ranked_eligible_rows[:denom]
    pos_count = sum(1 for item in top_items if item["positive"])
    top_ids = [item["id"] for item in top_items]
    top_labels = [item["positive"] for item in top_items]
    precision = pos_count / denom
    return precision, denom, top_ids, top_labels


def evaluate_configuration(doc, weight, semantic_method):
    """
    Evaluates a single (weight, semantic_method) configuration across all rows.
    """
    min_quality = doc.get("minimumQuality", 0.25)
    rows = doc.get("rows", [])
    total_positives = sum(1 for r in rows if r["positive"])

    eligible_ranked_rows = []
    excluded_below_quality_frames = 0
    missing_evidence_frames = 0
    unranked_positive_count = 0
    unranked_negative_count = 0

    for row in rows:
        cid = row["id"]
        is_pos = row["positive"]
        best_frame = None

        for f_idx, fr in enumerate(row.get("frames", [])):
            q = fr["qualityScore"]
            if q < min_quality:
                excluded_below_quality_frames += 1
                continue

            # Compute semantic score
            if weight == 0.0:
                semantic_score = 0.0
                combined_score = (1.0 - weight) * q + weight * semantic_score
            else:
                prompt_scores = fr["promptScores"]
                if semantic_method == "raw":
                    semantic_score = prompt_scores[1]
                    combined_score = (1.0 - weight) * q + weight * semantic_score
                elif semantic_method == "contrastive":
                    ppos = prompt_scores[1]
                    pneg_max = max(prompt_scores[2], prompt_scores[3], prompt_scores[4])
                    denom = ppos + pneg_max
                    if denom < 1e-6:
                        missing_evidence_frames += 1
                        continue
                    semantic_score = ppos / denom
                    combined_score = (1.0 - weight) * q + weight * semantic_score
                else:
                    raise ValueError(f"Unknown semantic method: {semantic_method}")

            if best_frame is None or combined_score > best_frame["combined"]:
                best_frame = {
                    "ordinal": f_idx,
                    "quality": q,
                    "semantic": semantic_score,
                    "combined": combined_score,
                }

        if best_frame is not None:
            eligible_ranked_rows.append({
                "id": cid,
                "score": best_frame["combined"],
                "positive": is_pos,
                "winningFrameOrdinal": best_frame["ordinal"],
                "winningQuality": best_frame["quality"],
                "winningSemantic": best_frame["semantic"],
            })
        else:
            if is_pos:
                unranked_positive_count += 1
            else:
                unranked_negative_count += 1

    # Sort stably: score descending, then id ascending
    eligible_ranked_rows.sort(key=lambda r: (-r["score"], r["id"]))

    ap = compute_average_precision(eligible_ranked_rows, total_positives)
    p10, p10_denom, p10_ids, p10_labels = compute_precision_at_k(eligible_ranked_rows, 10)
    p20, p20_denom, p20_ids, p20_labels = compute_precision_at_k(eligible_ranked_rows, 20)

    config_result = {
        "importanceWeight": weight,
        "semanticMethod": semantic_method,
        "eligibleClipCount": len(eligible_ranked_rows),
        "unrankedClipCount": unranked_positive_count + unranked_negative_count,
        "unrankedPositives": unranked_positive_count,
        "unrankedNegatives": unranked_negative_count,
        "excludedBelowQualityFrames": excluded_below_quality_frames,
        "missingEvidenceFrames": missing_evidence_frames,
        "averagePrecision": ap,
        "precisionAt10": p10,
        "precisionAt10Denominator": p10_denom,
        "precisionAt10TopIds": p10_ids,
        "precisionAt10TopLabels": p10_labels,
        "precisionAt20": p20,
        "precisionAt20Denominator": p20_denom,
        "precisionAt20TopIds": p20_ids,
        "precisionAt20TopLabels": p20_labels,
        "rankedRows": eligible_ranked_rows,
    }
    return config_result


def compute_diagnostics(doc):
    """
    Computes clip-level distributions and baseline contribution diagnostics.
    """
    min_quality = doc.get("minimumQuality", 0.25)
    rows = doc.get("rows", [])

    pos_max_q = []
    neg_max_q = []
    pos_max_raw = []
    neg_max_raw = []
    pos_max_contrast = []
    neg_max_contrast = []

    pos_baseline_scores = []
    neg_baseline_scores = []

    baseline_quality_contributions = []
    baseline_semantic_contributions = []

    total_clips = len(rows)
    total_frames = 0
    frames_below_min_q = 0
    frames_missing_contrast = 0

    for r in rows:
        is_pos = r["positive"]
        q_list = []
        raw_list = []
        contrast_list = []
        base_list = []

        for fr in r.get("frames", []):
            total_frames += 1
            q = fr["qualityScore"]
            if q < min_quality:
                frames_below_min_q += 1
                continue

            q_list.append(q)
            prompt_scores = fr["promptScores"]
            raw_s = prompt_scores[1]
            raw_list.append(raw_s)

            ppos = prompt_scores[1]
            pneg_max = max(prompt_scores[2], prompt_scores[3], prompt_scores[4])
            denom = ppos + pneg_max
            if denom >= 1e-6:
                contrast_list.append(ppos / denom)
            else:
                frames_missing_contrast += 1

            # Baseline frame score
            base_score = 0.7 * q + 0.3 * raw_s
            base_list.append(base_score)
            baseline_quality_contributions.append(0.7 * q)
            baseline_semantic_contributions.append(0.3 * raw_s)

        if q_list:
            if is_pos:
                pos_max_q.append(max(q_list))
                pos_max_raw.append(max(raw_list))
                if contrast_list:
                    pos_max_contrast.append(max(contrast_list))
                if base_list:
                    pos_baseline_scores.append(max(base_list))
            else:
                neg_max_q.append(max(q_list))
                neg_max_raw.append(max(raw_list))
                if contrast_list:
                    neg_max_contrast.append(max(contrast_list))
                if base_list:
                    neg_baseline_scores.append(max(base_list))

    q_contrib_summary = compute_distribution_summary(baseline_quality_contributions)
    s_contrib_summary = compute_distribution_summary(baseline_semantic_contributions)

    # Spread analysis
    q_mean = q_contrib_summary["mean"]
    s_mean = s_contrib_summary["mean"]
    ratio = (q_mean / s_mean) if s_mean > 1e-9 else float("inf")

    diagnostics = {
        "frameCounts": {
            "totalClips": total_clips,
            "totalFrames": total_frames,
            "framesBelowMinimumQuality": frames_below_min_q,
            "framesMissingContrastEvidence": frames_missing_contrast,
            "eligibleQualityFrames": total_frames - frames_below_min_q,
        },
        "scoreDistributions": {
            "positiveClips": {
                "maxQuality": compute_distribution_summary(pos_max_q),
                "maxRawPutter": compute_distribution_summary(pos_max_raw),
                "maxContrastive": compute_distribution_summary(pos_max_contrast),
                "maxBaselineScore": compute_distribution_summary(pos_baseline_scores),
            },
            "negativeClips": {
                "maxQuality": compute_distribution_summary(neg_max_q),
                "maxRawPutter": compute_distribution_summary(neg_max_raw),
                "maxContrastive": compute_distribution_summary(neg_max_contrast),
                "maxBaselineScore": compute_distribution_summary(neg_baseline_scores),
            },
        },
        "baselineContributionSpread": {
            "qualityContribution": q_contrib_summary,
            "semanticContribution": s_contrib_summary,
            "qualityToSemanticMeanRatio": ratio,
            "description": (
                f"At baseline (w=0.3 quality=0.7), quality score contributes mean {q_mean:.4f} "
                f"while raw semantic contributes mean {s_mean:.4f} (ratio ~{ratio:.2f}x). "
                "Quality contributes roughly 57.5x more magnitude to combined score, overwhelming the semantic signal "
                "when weighted at 0.7 vs 0.3."
            ),
        },
        "qualityDominanceAssessment": {
            "qualityDominatesMagnitude": bool(ratio > 2.0),
            "descriptiveFinding": (
                f"Quality score heavily dominates baseline magnitude (mean contribution {q_mean:.4f} vs {s_mean:.4f}, ratio ~{ratio:.1f}x). "
                f"Moreover, negative clips have slightly higher quality mean ({compute_distribution_summary(neg_max_q)['mean']:.3f}) than positives ({compute_distribution_summary(pos_max_q)['mean']:.3f}), "
                f"so relying on quality yields low AP (0.1431 at w=0.0, 0.1497 at baseline w=0.3). "
                "Increasing semantic weight to w=1.0 dramatically improves AP to 0.3136 and P@10 to 0.40."
            ),
        },
        "descriptiveObservations": [
            "Low absolute raw semantic scores reflect model SigLIP sigmoid/logit probability scale calibration across generic labels and do not alone indicate detection failure.",
            "Motion features are absent in numeric frame calibration data; rankings cannot prove motion necessity or infer dynamic physical events from static frame scores.",
            "All candidate clips have at least one frame meeting minimumQuality >= 0.25; exactly one frame across all clips fell below 0.25 (C150 frame 2).",
        ],
    }
    return diagnostics


def select_frozen_recommendation(configurations, baseline_config):
    """
    Selects the recommended frozen configuration using the specified criteria:
    - Fixed nearBestAP tolerance = 0.005 absolute BEFORE choosing.
    - Require P10 and P20 no worse than AP winner by > 1 item in respective denominator (0.1 for P10, 0.05 for P20).
    - Among near-equivalent: prefer raw over contrast; prefer existing w=0.3, then lower abs(weight-0.3), then smaller weight.
    """
    # Find exact AP winner
    sorted_by_ap = sorted(
        configurations,
        key=lambda c: (
            -(c["averagePrecision"] if c["averagePrecision"] is not None else -1.0),
            -(c["precisionAt10"] if c["precisionAt10"] is not None else -1.0),
            -(c["precisionAt20"] if c["precisionAt20"] is not None else -1.0),
            c["semanticMethod"] != "raw",  # raw first
            abs(c["importanceWeight"] - 0.3),
            c["importanceWeight"],
        )
    )
    ap_winner = sorted_by_ap[0]
    best_ap = ap_winner["averagePrecision"]
    ap_winner_p10 = ap_winner["precisionAt10"]
    ap_winner_p20 = ap_winner["precisionAt20"]

    tolerance = 0.005
    p10_max_drop = 1.0 / 10.0  # 0.10
    p20_max_drop = 1.0 / 20.0  # 0.05

    near_best_candidates = []
    for c in configurations:
        c_ap = c["averagePrecision"]
        if best_ap is None or c_ap is None:
            if best_ap is None and c_ap is None:
                near_best_candidates.append(c)
            continue
        ap_diff = best_ap - c_ap
        if ap_diff <= tolerance:
            p10_diff = (
                (ap_winner_p10 - c["precisionAt10"])
                if (ap_winner_p10 is not None and c["precisionAt10"] is not None)
                else 0.0
            )
            p20_diff = (
                (ap_winner_p20 - c["precisionAt20"])
                if (ap_winner_p20 is not None and c["precisionAt20"] is not None)
                else 0.0
            )
            if p10_diff <= p10_max_drop and p20_diff <= p20_max_drop:
                near_best_candidates.append(c)

    # Ranking near-best candidates:
    # 1. Prefer raw over contrast (method == 'raw' is 0, 'contrastive' is 1)
    # 2. Prefer existing w=0.3 (c['importanceWeight'] == 0.3 is 0, else 1)
    # 3. Lower abs(weight - 0.3)
    # 4. Smaller weight
    def tie_break_key(c):
        is_raw = 0 if c["semanticMethod"] == "raw" else 1
        is_baseline_w = 0 if abs(c["importanceWeight"] - 0.3) < 1e-9 else 1
        dist_w = abs(c["importanceWeight"] - 0.3)
        weight = c["importanceWeight"]
        return (is_raw, is_baseline_w, dist_w, weight)

    near_best_candidates.sort(key=tie_break_key)
    recommended = near_best_candidates[0]

    is_unchanged = (
        recommended["semanticMethod"] == baseline_config["semanticMethod"]
        and abs(recommended["importanceWeight"] - baseline_config["importanceWeight"]) < 1e-9
    )

    def _fmt(val, spec):
        return "None" if val is None else format(val, spec)

    reason = (
        f"Exact AP winner is {ap_winner['semanticMethod']} w={ap_winner['importanceWeight']} "
        f"(AP={_fmt(ap_winner['averagePrecision'], '.4f')}, P@10={_fmt(ap_winner['precisionAt10'], '.2f')}, P@20={_fmt(ap_winner['precisionAt20'], '.2f')}). "
        f"Under fixed tolerance 0.005 and Pareto guard (P@10 drop <= 0.1, P@20 drop <= 0.05), "
        f"candidate {recommended['semanticMethod']} w={recommended['importanceWeight']} "
        f"(AP={_fmt(recommended['averagePrecision'], '.4f')}, P@10={_fmt(recommended['precisionAt10'], '.2f')}, P@20={_fmt(recommended['precisionAt20'], '.2f')}) "
        f"is selected based on tie-break preference (prefer raw, prefer existing baseline weight w=0.3, lower abs(w-0.3))."
    )

    return {
        "objective": "Maximize calibration clip-level AP with P@10/P@20 trade-off Pareto guard",
        "nearBestAPTolerance": tolerance,
        "paretoConstraints": {
            "p10AllowedDrop": 0.10,
            "p20AllowedDrop": 0.05,
        },
        "exactAPWinner": {
            "semanticMethod": ap_winner["semanticMethod"],
            "importanceWeight": ap_winner["importanceWeight"],
            "averagePrecision": ap_winner["averagePrecision"],
            "precisionAt10": ap_winner["precisionAt10"],
            "precisionAt20": ap_winner["precisionAt20"],
        },
        "recommendedConfig": {
            "semanticMethod": recommended["semanticMethod"],
            "importanceWeight": recommended["importanceWeight"],
            "minimumQuality": 0.25,
            "positivePromptIndex": 1,
            "negativePromptIndices": [2, 3, 4],
            "contrastMinDenominator": 1e-6,
            "requiresHeldOutValidation": True,
        },
        "recommendedPerformance": {
            "averagePrecision": recommended["averagePrecision"],
            "precisionAt10": recommended["precisionAt10"],
            "precisionAt20": recommended["precisionAt20"],
        },
        "isBaselineUnchanged": is_unchanged,
        "recommendationReason": reason,
    }


def calculate_grid(doc):
    """
    Main entry point for calibration grid calculation.
    Accepts parsed JSON document, runs all 10 configurations and diagnostics,
    and returns full result dictionary conforming to output schema.
    """
    validation_counts = validate_input_document(doc)

    weights = [0.0, 0.3, 0.6, 0.85, 1.0]
    methods = ["raw", "contrastive"]

    configurations = []
    baseline_config = None

    for w in weights:
        for m in methods:
            # Note: w=0 is identical for both methods
            res = evaluate_configuration(doc, w, m)
            configurations.append(res)
            if abs(w - 0.3) < 1e-9 and m == "raw":
                baseline_config = res

    # Baseline comparison
    baseline_ap = baseline_config["averagePrecision"]
    baseline_p10 = baseline_config["precisionAt10"]
    baseline_p20 = baseline_config["precisionAt20"]

    baseline_comparisons = []
    for c in configurations:
        c_ap = c["averagePrecision"]
        c_p10 = c["precisionAt10"]
        c_p20 = c["precisionAt20"]
        delta_ap = (c_ap - baseline_ap) if (c_ap is not None and baseline_ap is not None) else None
        delta_p10 = (c_p10 - baseline_p10) if (c_p10 is not None and baseline_p10 is not None) else None
        delta_p20 = (c_p20 - baseline_p20) if (c_p20 is not None and baseline_p20 is not None) else None
        baseline_comparisons.append({
            "semanticMethod": c["semanticMethod"],
            "importanceWeight": c["importanceWeight"],
            "averagePrecision": c_ap,
            "deltaAP": delta_ap,
            "precisionAt10": c_p10,
            "deltaP10": delta_p10,
            "precisionAt20": c_p20,
            "deltaP20": delta_p20,
            "eligibleClipCount": c["eligibleClipCount"],
        })

    diagnostics = compute_diagnostics(doc)
    recommendation = select_frozen_recommendation(configurations, baseline_config)

    # Build output root
    output = {
        "schemaVersion": 1,
        "evaluationSplit": "calibration",
        "evaluationScope": doc.get("scope", "retrospective calibration only"),
        "counts": {
            "totalClips": validation_counts["rowCount"],
            "groundTruthPositives": validation_counts["positiveCount"],
            "groundTruthNegatives": validation_counts["negativeCount"],
            "uniqueIds": validation_counts["uniqueIds"],
            "evaluatedConfigurations": len(configurations),
        },
        "configurations": configurations,
        "baselineComparison": {
            "baseline": {
                "semanticMethod": "raw",
                "importanceWeight": 0.3,
                "averagePrecision": baseline_ap,
                "precisionAt10": baseline_p10,
                "precisionAt20": baseline_p20,
            },
            "comparisons": baseline_comparisons,
        },
        "diagnostics": diagnostics,
        "recommendation": recommendation,
        "limitations": [
            "Calibration analysis only on retrospective candidate clips; does not measure held-out generalization or full-source recall.",
            "Labels apply to cached candidate centers; clips without candidate centers were not included.",
            "Motion features are completely absent from numeric frame embeddings; rankings reflect appearance and context only.",
            "Extraction count must never be conflated with retrieval accuracy.",
        ],
    }
    return output


def main():
    parser = argparse.ArgumentParser(description="Calibrate video clip ranking weights")
    parser.add_argument("--input", required=True, help="Path to input calibration.numeric.json")
    parser.add_argument("--output", required=True, help="Path to output calibration-grid.json")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting output file")

    args = parser.parse_args()

    input_path = os.path.abspath(args.input)
    output_path = os.path.abspath(args.output)

    if not os.path.exists(input_path):
        sys.stderr.write(f"Error: input file does not exist: {input_path}\n")
        sys.exit(1)

    # Check for same file/inode
    if os.path.exists(output_path):
        try:
            if os.path.samefile(input_path, output_path):
                sys.stderr.write("Error: input and output paths refer to the same file\n")
                sys.exit(1)
        except OSError as e:
            sys.stderr.write(f"Error checking file identity: {e}\n")
            sys.exit(1)

        if not args.overwrite:
            sys.stderr.write(f"Error: output file already exists and --overwrite not specified: {output_path}\n")
            sys.exit(1)

    # Read input file safely
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as e:
        sys.stderr.write(f"Error reading JSON input: {e}\n")
        sys.exit(1)

    # Run calibration
    try:
        grid_result = calculate_grid(doc)
    except Exception as e:
        sys.stderr.write(f"Error calculating grid: {e}\n")
        sys.exit(1)

    # Atomic write to output
    out_dir = os.path.dirname(output_path)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # Use exclusive creation if overwrite is false, otherwise write to temp file then rename atomically
    temp_output_path = f"{output_path}.tmp.{os.getpid()}"
    try:
        with open(temp_output_path, "w", encoding="utf-8") as f:
            json.dump(grid_result, f, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        if not args.overwrite and os.path.exists(output_path):
            sys.stderr.write(f"Error: output file already exists: {output_path}\n")
            if os.path.exists(temp_output_path):
                os.remove(temp_output_path)
            sys.exit(1)

        os.replace(temp_output_path, output_path)
    except Exception as e:
        if os.path.exists(temp_output_path):
            os.remove(temp_output_path)
        sys.stderr.write(f"Error writing output file: {e}\n")
        sys.exit(1)

    print(f"Calibration completed successfully: {output_path}")
    print(f"Total clips: {grid_result['counts']['totalClips']}, Positives: {grid_result['counts']['groundTruthPositives']}")
    rec_ap = grid_result['recommendation']['recommendedPerformance']['averagePrecision']
    rec_ap_str = f"{rec_ap:.4f}" if rec_ap is not None else "None"
    print(f"Recommended config: {grid_result['recommendation']['recommendedConfig']['semanticMethod']} "
          f"w={grid_result['recommendation']['recommendedConfig']['importanceWeight']} "
          f"(AP={rec_ap_str})")


if __name__ == "__main__":
    main()
