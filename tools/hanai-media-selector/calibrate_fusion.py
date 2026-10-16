#!/usr/bin/env python3
"""
tools/hanai-media-selector/calibrate_fusion.py

Bounded retrospective calibration-only expert+semantic fusion experiment.
Calculates weighted-RRF and protected-head RRF calibrations on approved numeric inputs.
Standard Python 3 only; no third-party dependencies.
"""

import argparse
import json
import math
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional, Tuple


def _validate_schema(doc: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not isinstance(doc, dict):
        raise ValueError("Root must be a JSON object.")
    if doc.get("schemaVersion") != 1:
        raise ValueError("Unsupported schemaVersion. Expected 1.")
    
    scope = doc.get("scope", "")
    target = doc.get("target", "")
    min_quality = doc.get("minimumQuality", 0.25)
    expert_higher_is_better = doc.get("expertHigherIsBetter", True)
    semantic_score_def = doc.get("semanticScoreDefinition", "")
    
    rows = doc.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Input 'rows' must be a list.")
    
    seen_ids = set()
    validated_rows = []
    
    for idx, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ValueError(f"Row {idx} is not an object.")
        row_id = r.get("id")
        if not isinstance(row_id, str) or not row_id:
            raise ValueError(f"Row {idx} missing valid string id.")
        if row_id in seen_ids:
            raise ValueError(f"Duplicate id found: {row_id}")
        seen_ids.add(row_id)
        
        pos = r.get("positive")
        if type(pos) is not bool:
            raise ValueError(f"Row {row_id} 'positive' must be exact bool.")
            
        group_id = r.get("groupId")
        if not isinstance(group_id, str) or not group_id:
            raise ValueError(f"Row {row_id} 'groupId' must be non-empty string.")
            
        exp_score = r.get("expertScore")
        if exp_score is not None:
            if not isinstance(exp_score, (int, float)) or math.isnan(exp_score) or math.isinf(exp_score):
                raise ValueError(f"Row {row_id} 'expertScore' must be finite number or null.")
            exp_score = float(exp_score)
            
        sem_score = r.get("semanticScore")
        if sem_score is not None:
            if not isinstance(sem_score, (int, float)) or math.isnan(sem_score) or math.isinf(sem_score):
                raise ValueError(f"Row {row_id} 'semanticScore' must be finite number or null.")
            sem_score = float(sem_score)
            if sem_score < 0.0 or sem_score > 1.0:
                raise ValueError(f"Row {row_id} 'semanticScore' must be in [0, 1].")
                
        qual_score = r.get("qualityScore")
        if not isinstance(qual_score, (int, float)) or math.isnan(qual_score) or math.isinf(qual_score):
            raise ValueError(f"Row {row_id} 'qualityScore' must be finite number.")
        qual_score = float(qual_score)
        if qual_score < 0.0 or qual_score > 1.0:
            raise ValueError(f"Row {row_id} 'qualityScore' must be in [0, 1].")
            
        motion = r.get("motion")
        validated_motion = None
        if motion is not None:
            if not isinstance(motion, dict):
                raise ValueError(f"Row {row_id} 'motion' must be an object.")
            validated_motion = {}
            for k, v in motion.items():
                if not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v):
                    raise ValueError(f"Row {row_id} motion field {k} must be finite number.")
                if v < 0:
                    raise ValueError(f"Row {row_id} motion field {k} must be non-negative.")
                validated_motion[k] = float(v)
                
        validated_rows.append({
            "id": row_id,
            "positive": pos,
            "groupId": group_id,
            "expertScore": exp_score,
            "semanticScore": sem_score,
            "qualityScore": qual_score,
            "motion": validated_motion,
        })
        
    meta = {
        "scope": scope,
        "target": target,
        "minimumQuality": min_quality,
        "expertHigherIsBetter": expert_higher_is_better,
        "semanticScoreDefinition": semantic_score_def,
    }
    return validated_rows, meta


def _compute_metrics(ranked_rows: List[Dict[str, Any]], total_positives_split: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Computes AP, P@10, P@20.
    AP = sum precision at positive positions / totalpositiveGT on relevant fixedsplit. Denom 0 -> null.
    P@K = positive count in top min(K, N) / min(K, N). Denom 0 -> null.
    """
    n = len(ranked_rows)
    if total_positives_split <= 0:
        ap = None
    else:
        num_hits = 0
        prec_sum = 0.0
        for i, r in enumerate(ranked_rows, start=1):
            if r["positive"]:
                num_hits += 1
                prec_sum += (num_hits / float(i))
        ap = prec_sum / float(total_positives_split)
        
    def calc_p_at_k(k: int) -> Optional[float]:
        denom = min(k, n)
        if denom == 0:
            return None
        hits = sum(1 for r in ranked_rows[:denom] if r["positive"])
        return hits / float(denom)
        
    p10 = calc_p_at_k(10)
    p20 = calc_p_at_k(20)
    return ap, p10, p20


def _compute_motion_distribution(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    pos_motion: Dict[str, List[float]] = {}
    neg_motion: Dict[str, List[float]] = {}
    
    for r in rows:
        m = r.get("motion")
        if not m:
            continue
        target_dict = pos_motion if r["positive"] else neg_motion
        for k, v in m.items():
            target_dict.setdefault(k, []).append(v)
            
    def summarize(vals: List[float]) -> Dict[str, Optional[float]]:
        if not vals:
            return {"min": None, "max": None, "mean": None, "median": None, "count": 0}
        s_vals = sorted(vals)
        n = len(s_vals)
        v_min = s_vals[0]
        v_max = s_vals[-1]
        v_mean = sum(s_vals) / float(n)
        if n % 2 == 1:
            v_med = s_vals[n // 2]
        else:
            v_med = (s_vals[n // 2 - 1] + s_vals[n // 2]) / 2.0
        return {"min": v_min, "max": v_max, "mean": v_mean, "median": v_med, "count": n}
        
    all_fields = sorted(list(set(list(pos_motion.keys()) + list(neg_motion.keys()))))
    dist: Dict[str, Any] = {}
    for f in all_fields:
        dist[f] = {
            "positive": summarize(pos_motion.get(f, [])),
            "negative": summarize(neg_motion.get(f, [])),
        }
    return dist


def calculate_fusion(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main entry point for calibration calculations.
    """
    rows, meta = _validate_schema(doc)
    
    total_count = len(rows)
    positives_count = sum(1 for r in rows if r["positive"])
    
    # 1. Partition rows into primary expert-present (158) and missing expert (2)
    primary_rows = [r for r in rows if r["expertScore"] is not None]
    missing_expert_rows = [r for r in rows if r["expertScore"] is None]
    
    primary_count = len(primary_rows)
    primary_positives = sum(1 for r in primary_rows if r["positive"])
    
    missing_count = len(missing_expert_rows)
    missing_positives = sum(1 for r in missing_expert_rows if r["positive"])
    
    # Check fallback availability for missing expert rows
    # Fallback tail requires valid semanticScore
    missing_with_valid_semantic = [
        r for r in missing_expert_rows if r["semanticScore"] is not None
    ]
    # Sort fallback tail by semanticScore descending then id ascending
    missing_with_valid_semantic_sorted = sorted(
        missing_with_valid_semantic,
        key=lambda r: (-r["semanticScore"], r["id"])
    )
    excluded_no_evidence = [
        r for r in missing_expert_rows if r["semanticScore"] is None
    ]
    
    # Check if ANY expert-present row in each group lacks valid semantic
    # "If ANY expert-supported group row lacks validsemantic, ENTIRE expert-supported group uniformly uses pureexpert ranking"
    groups = set(r["groupId"] for r in rows)
    group_lacks_semantic: Dict[str, bool] = {}
    for g in groups:
        g_exp_rows = [r for r in primary_rows if r["groupId"] == g]
        group_lacks_semantic[g] = any(r["semanticScore"] is None for r in g_exp_rows)
        
    # Baseline Expert order on primary 158: expertScore descending, id ascending
    baseline_expert_order = sorted(primary_rows, key=lambda r: (-r["expertScore"], r["id"]))
    
    # Baseline Semantic order on primary 158: semanticScore descending (or -inf if missing), id ascending
    semantic_order = sorted(
        primary_rows,
        key=lambda r: (-(r["semanticScore"] if r["semanticScore"] is not None else -1e9), r["id"])
    )
    
    # Compute 1-based ranks on primary 158
    expert_ranks = {r["id"]: rank for rank, r in enumerate(baseline_expert_order, start=1)}
    semantic_ranks = {r["id"]: rank for rank, r in enumerate(semantic_order, start=1)}
    
    # Grid parameters
    k_const = 60
    alphas = [0.0, 0.025, 0.05, 0.1, 0.2, 0.35, 0.5]
    h_heads = [0, 3, 5, 10]
    
    # Helper to rank primary rows for a given (alpha, h)
    def rank_primary(alpha: float, h: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        # Compute fusion score for all primary rows
        scored = []
        for r in primary_rows:
            rid = r["id"]
            gid = r["groupId"]
            if alpha == 0.0 or group_lacks_semantic[gid]:
                # Pure expert ranking
                exp_r = expert_ranks[rid]
                fs = 1.0 / (k_const + exp_r)
            else:
                exp_r = expert_ranks[rid]
                sem_r = semantic_ranks[rid]
                fs = ((1.0 - alpha) / (k_const + exp_r)) + (alpha / (k_const + sem_r))
            scored.append({
                "row": r,
                "id": rid,
                "expertRank": expert_ranks[rid],
                "fusionScore": fs,
            })
            
        if h == 0:
            # Method: weighted-rrf
            if alpha == 0.0:
                # Must exactly reproduce actual expert order
                ranked = baseline_expert_order
            else:
                # Sort by fusion score descending, then original expertRank ascending, then id ascending
                scored_sorted = sorted(scored, key=lambda x: (-x["fusionScore"], x["expertRank"], x["id"]))
                ranked = [x["row"] for x in scored_sorted]
            config = {
                "method": "weighted-rrf",
                "k": k_const,
                "semanticWeight": alpha,
                "protectedHead": 0,
            }
        else:
            # Method: protected-head-weighted-rrf
            if alpha == 0.0:
                ranked = baseline_expert_order
            else:
                head_rows = baseline_expert_order[:h]
                head_ids = set(r["id"] for r in head_rows)
                tail_scored = [x for x in scored if x["id"] not in head_ids]
                # Tail ranks MUST be ranks computed on fullprimary158 (already in fusionScore), then tail sorted by fusion score / expertRank / id
                tail_sorted = sorted(tail_scored, key=lambda x: (-x["fusionScore"], x["expertRank"], x["id"]))
                tail_rows = [x["row"] for x in tail_sorted]
                ranked = head_rows + tail_rows
            config = {
                "method": "protected-head-weighted-rrf",
                "k": k_const,
                "semanticWeight": alpha,
                "protectedHead": h,
            }
        return ranked, config

    # Compute baseline primary metrics
    exp_ap, exp_p10, exp_p20 = _compute_metrics(baseline_expert_order, primary_positives)
    baseline_primary_info = {
        "name": "expertBaseline",
        "description": "Primary expert-present 158 ordered by expertScore descending then id ascending",
        "ap": exp_ap,
        "p10": exp_p10,
        "p20": exp_p20,
        "n": primary_count,
        "positives": primary_positives,
        "top10Ids": [r["id"] for r in baseline_expert_order[:10]],
        "top10Labels": [r["positive"] for r in baseline_expert_order[:10]],
        "top20Ids": [r["id"] for r in baseline_expert_order[:20]],
        "top20Labels": [r["positive"] for r in baseline_expert_order[:20]],
        "rankedIds": [r["id"] for r in baseline_expert_order],
    }

    # Evaluate full grid on primary
    primary_configs = []
    
    # Store candidates for recommendation selection
    candidate_results = []
    
    for h in h_heads:
        for alpha in alphas:
            ranked_p, cfg = rank_primary(alpha, h)
            ap, p10, p20 = _compute_metrics(ranked_p, primary_positives)
            
            delta_ap = ap - exp_ap if ap is not None and exp_ap is not None else 0.0
            delta_p10 = p10 - exp_p10 if p10 is not None and exp_p10 is not None else 0.0
            delta_p20 = p20 - exp_p20 if p20 is not None and exp_p20 is not None else 0.0
            
            # Constraints: AP >= expertAP - 1e-12, P10 >= expertP10 - 1e-12, P20 >= expertP20 - 1e-12
            fail_reasons = []
            tol = 1e-12
            if ap is not None and exp_ap is not None and ap < exp_ap - tol:
                fail_reasons.append(f"AP degraded ({ap:.6f} < {exp_ap:.6f})")
            if p10 is not None and exp_p10 is not None and p10 < exp_p10 - tol:
                fail_reasons.append(f"P10 degraded ({p10:.6f} < {exp_p10:.6f})")
            if p20 is not None and exp_p20 is not None and p20 < exp_p20 - tol:
                fail_reasons.append(f"P20 degraded ({p20:.6f} < {exp_p20:.6f})")
                
            is_eligible = len(fail_reasons) == 0
            has_strict_ap_gain = (ap is not None and exp_ap is not None and (ap - exp_ap) > 1e-12)
            
            cfg_entry = {
                "config": cfg,
                "ap": ap,
                "p10": p10,
                "p20": p20,
                "deltaApVsBaseline": delta_ap,
                "deltaP10VsBaseline": delta_p10,
                "deltaP20VsBaseline": delta_p20,
                "isEligible": is_eligible,
                "hasStrictApGain": has_strict_ap_gain,
                "constraintFailReasons": fail_reasons,
                "top10Ids": [r["id"] for r in ranked_p[:10]],
                "top10Labels": [r["positive"] for r in ranked_p[:10]],
                "top20Ids": [r["id"] for r in ranked_p[:20]],
                "top20Labels": [r["positive"] for r in ranked_p[:20]],
                "rankedIds": [r["id"] for r in ranked_p],
            }
            primary_configs.append(cfg_entry)
            candidate_results.append({
                "cfg_entry": cfg_entry,
                "ranked_rows": ranked_p,
                "alpha": alpha,
                "h": h,
                "ap": ap,
                "p10": p10,
                "p20": p20,
                "is_eligible": is_eligible,
                "has_strict_ap_gain": has_strict_ap_gain,
            })

    # Selection logic for Recommended Config:
    # "Recommended config selection constraints PRIMARY AP>=expertAP AND P10>=expertP10 AND P20>=expertP20 (1e-12 tolerance only floatingnoise), among eligible maximizeAP; if no strict APgain>1e-12 recommend baseline unchanged (do NOT force fusion). Tie AP then P20 thenP10, simplerconfiguration priority baseline, unprotectedRRF before protected, loweralpha, lowerH. Include exact winner, delta, constraintfailreason perconfiguration. Recommending smallnumerical improvement doesn't prove generalized benefit; report nearzero .005 improvements fragile but don't modify objective."
    
    strict_gain_eligible = [c for c in candidate_results if c["is_eligible"] and c["has_strict_ap_gain"]]
    
    if not strict_gain_eligible:
        # Recommend baseline unchanged
        recommended_winner = "expertBaseline"
        recommended_config = {
            "method": "expert-baseline",
            "k": k_const,
            "semanticWeight": 0.0,
            "protectedHead": 0,
        }
        recommended_primary_ranked = baseline_expert_order
        recommendation_reason = "No eligible configuration achieved strict primary AP gain > 1e-12 over expert baseline without metric degradation; baseline recommended unchanged."
        recommended_delta_ap = 0.0
        recommended_delta_p10 = 0.0
        recommended_delta_p20 = 0.0
        is_fragile = False
    else:
        # Sort key to maximize AP, then P20, then P10, then simpler configuration priority:
        # Priority: baseline (alpha=0), unprotectedRRF (h=0) before protected (h>0), lower alpha, lower h
        def sort_key(c):
            # We want to maximize AP, P20, P10
            # Then minimize complexity:
            # is_baseline = 1 if (c['alpha'] == 0 and c['h'] == 0) else 0
            # is_unprotected = 1 if c['h'] == 0 else 0
            # We negate metrics for max, and use positive for complexity minimization
            return (
                -c["ap"],
                -c["p20"],
                -c["p10"],
                0 if c["h"] == 0 else 1, # unprotected first
                c["alpha"],               # lower alpha first
                c["h"],                   # lower H first
            )
        strict_gain_eligible.sort(key=sort_key)
        best = strict_gain_eligible[0]
        recommended_winner = f"{best['cfg_entry']['config']['method']}_alpha_{best['alpha']}_h_{best['h']}"
        recommended_config = best["cfg_entry"]["config"]
        recommended_primary_ranked = best["ranked_rows"]
        recommended_delta_ap = best["cfg_entry"]["deltaApVsBaseline"]
        recommended_delta_p10 = best["cfg_entry"]["deltaP10VsBaseline"]
        recommended_delta_p20 = best["cfg_entry"]["deltaP20VsBaseline"]
        is_fragile = (recommended_delta_ap < 0.005)
        recommendation_reason = (
            f"Configuration {recommended_winner} achieved highest AP among constraint-satisfying candidates. "
            f"AP delta: +{recommended_delta_ap:.6f}, P10 delta: +{recommended_delta_p10:.6f}, P20 delta: +{recommended_delta_p20:.6f}."
        )
        if is_fragile:
            recommendation_reason += " Notice: Gain is near-zero (< 0.005) and fragile, not proving generalized benefit."

    # FullAvailable160 Reporting:
    # "primary expertpresent158 ranks as above; append missingexpert rows WITH validsemantic in semanticScore descending/idascending fallback tail. Neither score available -> excluded no-ranking-evidence, never invent qualityrank. This conservative separatecoverage fallback does NOT fabricate expertrank/score and cannot displace expert-ranked head."
    # "Baseline full needs TWO explicitlydifferent reports: legacyExpertOnly actualexpert158 order (missingexpertunranked, all160positivesdenominator) and expertWithSemanticFallbackBaseline (sameexpert158 then missingexpertsemanticvalidtail). alpha0 pureexpert +semanticfallbacktail therefore can differ outputCOUNT from actuallegacyonly; explicitlydocument. Report exactfullMissingExpertPolicy and missing counts/labels/fallbackIDs (anonymous only), reason 'no-expert-score-record'."

    # 1. legacyExpertOnly: actualexpert158 order (missingexpertunranked, all 160 positives denominator = 31)
    legacy_full_ap, legacy_full_p10, legacy_full_p20 = _compute_metrics(baseline_expert_order, positives_count)
    
    # 2. expertWithSemanticFallbackBaseline: same expert158 then missingexpertsemanticvalidtail (denominator = 31)
    expert_with_fallback_order = baseline_expert_order + missing_with_valid_semantic_sorted
    fb_full_ap, fb_full_p10, fb_full_p20 = _compute_metrics(expert_with_fallback_order, positives_count)
    
    full_baseline_info = {
        "legacyExpertOnly": {
            "name": "legacyExpertOnly",
            "description": "Actual expert 158 ranked rows only; 2 missing-expert rows unranked (output count 158). Positives denominator is all 160 GT positives.",
            "outputCount": len(baseline_expert_order),
            "ap": legacy_full_ap,
            "p10": legacy_full_p10,
            "p20": legacy_full_p20,
            "rankedIds": [r["id"] for r in baseline_expert_order],
        },
        "expertWithSemanticFallbackBaseline": {
            "name": "expertWithSemanticFallbackBaseline",
            "description": "Baseline expert 158 ranked rows followed by missing-expert rows with valid semantic scores in descending semantic order (output count 160). Positives denominator is all 160 GT positives.",
            "outputCount": len(expert_with_fallback_order),
            "ap": fb_full_ap,
            "p10": fb_full_p10,
            "p20": fb_full_p20,
            "rankedIds": [r["id"] for r in expert_with_fallback_order],
        },
        "outputCountDiscrepancyNote": (
            "alpha=0 weighted-RRF under fullAvailable includes the fallback tail of missing-expert rows with valid semantic scores (output count 160), "
            "whereas legacyExpertOnly completely leaves missing-expert rows unranked (output count 158)."
        )
    }

    # Evaluate fullAvailable configurations (appending missing_with_valid_semantic_sorted to each ranked primary list)
    full_configs = []
    for c_res in candidate_results:
        cfg = c_res["cfg_entry"]["config"]
        ranked_p = c_res["ranked_rows"]
        full_ranked = ranked_p + missing_with_valid_semantic_sorted
        f_ap, f_p10, f_p20 = _compute_metrics(full_ranked, positives_count)
        full_configs.append({
            "config": cfg,
            "outputCount": len(full_ranked),
            "ap": f_ap,
            "p10": f_p10,
            "p20": f_p20,
            "deltaApVsLegacyBaseline": f_ap - legacy_full_ap if f_ap is not None and legacy_full_ap is not None else 0.0,
            "deltaApVsSemanticFallbackBaseline": f_ap - fb_full_ap if f_ap is not None and fb_full_ap is not None else 0.0,
            "top10Ids": [r["id"] for r in full_ranked[:10]],
            "top10Labels": [r["positive"] for r in full_ranked[:10]],
            "top20Ids": [r["id"] for r in full_ranked[:20]],
            "top20Labels": [r["positive"] for r in full_ranked[:20]],
            "rankedIds": [r["id"] for r in full_ranked],
        })

    missing_coverage_info = {
        "policy": "conservative-separate-coverage-fallback",
        "description": "Primary expert-present 158 rows ranked by candidate fusion policy. Missing expert rows with valid semanticScore appended as fallback tail sorted by semanticScore descending then id ascending. Rows lacking both expert and semantic scores are excluded.",
        "missingExpertCount": missing_count,
        "missingExpertDetails": [
            {
                "id": r["id"],
                "positive": r["positive"],
                "groupId": r["groupId"],
                "semanticScore": r["semanticScore"],
                "qualityScore": r["qualityScore"],
                "reason": "no-expert-score-record",
                "status": "appended-to-fallback-tail" if r["semanticScore"] is not None else "excluded-no-ranking-evidence",
            }
            for r in missing_expert_rows
        ],
        "fallbackAppendedIds": [r["id"] for r in missing_with_valid_semantic_sorted],
        "excludedIds": [r["id"] for r in excluded_no_evidence],
    }

    # Recommended full ranking
    recommended_full_ranked = recommended_primary_ranked + missing_with_valid_semantic_sorted
    calibration_model_order_ids = {
        "recommendedPrimaryExpertComplete158": [r["id"] for r in recommended_primary_ranked],
        "recommendedFullAvailable160": [r["id"] for r in recommended_full_ranked],
        "legacyExpertBaselinePrimary158": [r["id"] for r in baseline_expert_order],
        "legacyExpertBaselineFull158": [r["id"] for r in baseline_expert_order],
    }

    # Per-Block Diagnostic:
    # "Perblock diagnostic: same frozen config/expert/semantic orders restricted each group; NO retuning/rankrecomputation usinglabels, AP/P10/P20+N/positives, smallsamplewarning N<30 orpositives<5; do not modify globalchoice from perblockwinners."
    sorted_groups = sorted(list(groups))
    per_block_diagnostics = {}
    for g in sorted_groups:
        # Group rows restricted from primary and full
        g_primary_baseline = [r for r in baseline_expert_order if r["groupId"] == g]
        g_rec_primary = [r for r in recommended_primary_ranked if r["groupId"] == g]
        g_rec_full = [r for r in recommended_full_ranked if r["groupId"] == g]
        
        g_total_rows = [r for r in rows if r["groupId"] == g]
        g_positives = sum(1 for r in g_total_rows if r["positive"])
        g_primary_pos = sum(1 for r in g_primary_baseline if r["positive"])
        
        g_base_ap, g_base_p10, g_base_p20 = _compute_metrics(g_primary_baseline, g_primary_pos)
        g_rec_p_ap, g_rec_p_p10, g_rec_p_p20 = _compute_metrics(g_rec_primary, g_primary_pos)
        g_rec_f_ap, g_rec_f_p10, g_rec_f_p20 = _compute_metrics(g_rec_full, g_positives)
        
        small_sample_warning = (len(g_total_rows) < 30 or g_positives < 5)
        
        per_block_diagnostics[g] = {
            "totalCount": len(g_total_rows),
            "totalPositives": g_positives,
            "primaryCount": len(g_primary_baseline),
            "primaryPositives": g_primary_pos,
            "smallSampleWarning": small_sample_warning,
            "warningNote": "Sample size N < 30 or positives < 5; metrics subject to high variance." if small_sample_warning else None,
            "expertBaselinePrimary": {
                "ap": g_base_ap,
                "p10": g_base_p10,
                "p20": g_base_p20,
            },
            "recommendedConfigPrimary": {
                "ap": g_rec_p_ap,
                "p10": g_rec_p_p10,
                "p20": g_rec_p_p20,
                "deltaAp": g_rec_p_ap - g_base_ap if g_rec_p_ap is not None and g_base_ap is not None else 0.0,
            },
            "recommendedConfigFull": {
                "ap": g_rec_f_ap,
                "p10": g_rec_f_p10,
                "p20": g_rec_f_p20,
            }
        }

    # Motion Diagnostics
    motion_diagnostics = {
        "distributions": _compute_motion_distribution(rows),
        "disclaimer": "Descriptive positive vs negative distribution only. High motion does not imply meaningful action, learning, or fusion improvement.",
    }

    limitations = [
        "Retrospective calibration: these examples were previously seen in historical review and do NOT constitute unseen or held-out split verification.",
        "Candidate-conditioned evaluation: metrics assess candidate ranking performance, not full source event recall.",
        "Head structure preserves expert prioritization but does not imply independent action confirmation.",
        "Missing expert score record represents an absent score entry in historical logs, NOT a failure of detection by the legacy engine.",
        "Thresholds and weights calibrated here are putter-candidate-ranking specific and must not be generalized without independent verification.",
        "Runtime tie-breaking contract: RRF fusion score ties are resolved by original expertRank ascending, then anonymous id ascending (id maps stable input index). Both expert and semantic ranks are computed 1-based on the whole expert-present primary group (158); protected head fixed order is preserved and semantic-only tail items are never included in primary ranks.",
        "Requires independent TM execution on frozen config next."
    ]

    out_doc = {
        "schemaVersion": 1,
        "scope": meta["scope"],
        "target": meta["target"],
        "evaluationSplit": "retrospective-calibration",
        "tiePolicy": "RRF fusion score ties sort original expertRank ascending then anonymous id ascending (id maps stable input index). Both expert and semantic ranks computed 1-based on whole expert-present primary group; protected expert head fixed order preserved.",
        "counts": {
            "totalRows": total_count,
            "totalPositives": positives_count,
            "primaryExpertPresent": primary_count,
            "primaryExpertPresentPositives": primary_positives,
            "missingExpert": missing_count,
            "missingExpertPositives": missing_positives,
            "missingExpertWithSemantic": len(missing_with_valid_semantic),
            "missingExpertExcluded": len(excluded_no_evidence),
        },
        "primaryExpertComplete": {
            "baseline": baseline_primary_info,
            "configurations": primary_configs,
        },
        "fullAvailable": {
            "baseline": full_baseline_info,
            "configurations": full_configs,
            "missingExpertCoverage": missing_coverage_info,
        },
        "recommendedConfig": recommended_config,
        "recommendation": {
            "winner": recommended_winner,
            "selectionBasis": "PRIMARY158-only",
            "reason": recommendation_reason,
            "isFragile": is_fragile,
            "deltaApVsBaseline": recommended_delta_ap,
            "deltaP10VsBaseline": recommended_delta_p10,
            "deltaP20VsBaseline": recommended_delta_p20,
            "primaryMetrics": {
                "ap": candidate_results[0]["ap"] if recommended_winner == "expertBaseline" else best["ap"],
                "p10": candidate_results[0]["p10"] if recommended_winner == "expertBaseline" else best["p10"],
                "p20": candidate_results[0]["p20"] if recommended_winner == "expertBaseline" else best["p20"],
            }
        },
        "calibrationModelOrderIDs": calibration_model_order_ids,
        "perBlockDiagnostics": per_block_diagnostics,
        "motionDiagnostics": motion_diagnostics,
        "provenance": {
            "source": "caller-provided expert + actual semantic features; not independently verified decoder/model here",
            "status": "frozen-calibration-results",
        },
        "limitations": limitations,
    }
    
    return out_doc


def calculate_confidence_fusion(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bounded confidence-gap-preserving complement experiment using blocked logistic regression.
    Evaluates 36 predeclared configurations across blocked leave-one-group-out 4 folds.
    """
    rows, meta = _validate_schema(doc)
    
    total_count = len(rows)
    positives_count = sum(1 for r in rows if r["positive"])
    
    # Partition primary expert-present (158) and missing expert (2)
    primary_rows = [r for r in rows if r["expertScore"] is not None]
    missing_expert_rows = [r for r in rows if r["expertScore"] is None]
    
    primary_count = len(primary_rows)
    primary_positives = sum(1 for r in primary_rows if r["positive"])
    
    missing_count = len(missing_expert_rows)
    missing_positives = sum(1 for r in missing_expert_rows if r["positive"])
    
    # Missing expert semantic fallback tail
    missing_with_valid_semantic = [
        r for r in missing_expert_rows if r["semanticScore"] is not None
    ]
    missing_with_valid_semantic_sorted = sorted(
        missing_with_valid_semantic,
        key=lambda r: (-r["semanticScore"], r["id"])
    )
    excluded_no_evidence = [
        r for r in missing_expert_rows if r["semanticScore"] is None
    ]
    
    # Groups
    groups = sorted(list(set(r["groupId"] for r in primary_rows)))
    
    # Predeclared feature sets and parameters
    feature_sets = [
        ["E", "S"],
        ["E", "S", "D"],
        ["E", "S", "R"],
        ["E", "S", "D", "R"]
    ]
    l2_lambdas = [0.1, 1.0, 10.0]
    protected_heads = [0, 5, 10]
    eps_sem = 1e-6
    
    def transform_feat(r: Dict[str, Any], feat_name: str) -> float:
        if feat_name == "E":
            # Keep expertScore RAW preserving gap (never logit expert)
            return float(r["expertScore"])
        elif feat_name == "S":
            # semanticScore sigmoid prob [0, 1] -> fixed clip EPS 1e-6 -> logit(p/(1-p))
            p = float(r["semanticScore"])
            p_clip = min(max(p, eps_sem), 1.0 - eps_sem)
            return math.log(p_clip / (1.0 - p_clip))
        elif feat_name == "D":
            m = r.get("motion")
            if not m or m.get("dinoTemporalDistanceMean") is None:
                raise ValueError("Missing D feature")
            return float(m["dinoTemporalDistanceMean"])
        elif feat_name == "R":
            m = r.get("motion")
            if not m or m.get("roiDifferenceMean") is None:
                raise ValueError("Missing R feature")
            return float(m["roiDifferenceMean"])
        raise ValueError(f"Unknown feature: {feat_name}")

    # Check feature completeness on primary rows
    # "If requiredfeature missing anyprimary row, mark candidatefeatureconfiguration unavailable"
    # "If ANY primarysemantic missing -> entireprimary pureexpert uniform fallback"
    primary_has_all_semantic = all(r.get("semanticScore") is not None for r in primary_rows)
    feature_availability: Dict[str, bool] = {}
    for feat_key in ["E", "S", "D", "R"]:
        available = True
        for r in primary_rows:
            try:
                transform_feat(r, feat_key)
            except Exception:
                available = False
                break
        feature_availability[feat_key] = available

    # Pure Python Newton-Raphson solver for logistic regression with L2 regularization
    def solve_logistic(X: List[List[float]], y: List[float], l2: float, max_iter: int = 100, tol: float = 1e-7) -> Tuple[List[float], float, int, float, bool]:
        """
        Fits logistic regression z = b + sum(beta * xstd).
        Minimizes mean stable logloss + 0.5 * l2 * sum(beta^2) excluding intercept b.
        Returns: (theta, final_loss, iterations, grad_norm, converged)
        theta: [b, beta_1, ..., beta_D]
        """
        N = len(y)
        D = len(X[0])
        theta = [0.0] * (D + 1)
        A = [[1.0] + list(row) for row in X]
        
        def stable_sigmoid(z: float) -> float:
            if z >= 0.0:
                return 1.0 / (1.0 + math.exp(-z))
            else:
                ez = math.exp(z)
                return ez / (1.0 + ez)
                
        def get_loss(th: List[float]) -> float:
            loss_sum = 0.0
            for i in range(N):
                z = sum(th[j] * A[i][j] for j in range(D + 1))
                if y[i] == 1.0:
                    l = math.log1p(math.exp(-z)) if z >= 0.0 else -z + math.log1p(math.exp(z))
                else:
                    l = math.log1p(math.exp(z)) if z <= 0.0 else z + math.log1p(math.exp(-z))
                loss_sum += l
            mean_loss = loss_sum / float(N)
            reg = 0.5 * l2 * sum(th[j] ** 2 for j in range(1, D + 1))
            return mean_loss + reg

        converged = False
        it = 0
        final_grad_norm = 0.0
        
        for it in range(max_iter):
            curr_loss = get_loss(theta)
            p = [stable_sigmoid(sum(theta[j] * A[i][j] for j in range(D + 1))) for i in range(N)]
            
            # Gradient
            grad = [0.0] * (D + 1)
            for j in range(D + 1):
                s = sum((p[i] - y[i]) * A[i][j] for i in range(N))
                grad[j] = (s / float(N)) + (l2 * theta[j] if j > 0 else 0.0)
                
            grad_norm = math.sqrt(sum(g ** 2 for g in grad))
            final_grad_norm = grad_norm
            if grad_norm < tol:
                converged = True
                break
                
            # Hessian
            H = [[0.0] * (D + 1) for _ in range(D + 1)]
            w = [p[i] * (1.0 - p[i]) for i in range(N)]
            for j in range(D + 1):
                for k in range(j, D + 1):
                    val = sum(w[i] * A[i][j] * A[i][k] for i in range(N)) / float(N)
                    if j == k and j > 0:
                        val += l2
                    H[j][k] = val
                    H[k][j] = val
                    
            # Gauss-Jordan solve: H * delta = -grad
            n_dim = D + 1
            M = [H[r][:] + [-grad[r]] for r in range(n_dim)]
            for c in range(n_dim):
                pivot = c
                max_val = abs(M[c][c])
                for r in range(c + 1, n_dim):
                    if abs(M[r][c]) > max_val:
                        max_val = abs(M[r][c])
                        pivot = r
                if max_val < 1e-12:
                    M[c][c] += 1e-6
                elif pivot != c:
                    M[c], M[pivot] = M[pivot], M[c]
                p_val = M[c][c]
                for col in range(c, n_dim + 1):
                    M[c][col] /= p_val
                for r in range(n_dim):
                    if r != c:
                        factor = M[r][c]
                        if abs(factor) > 1e-14:
                            for col in range(c, n_dim + 1):
                                M[r][col] -= factor * M[c][col]
                                
            delta = [M[r][n_dim] for r in range(n_dim)]
            
            # Damped backtracking line search
            step = 1.0
            new_theta = [theta[j] + step * delta[j] for j in range(D + 1)]
            new_loss = get_loss(new_theta)
            backtrack = 0
            while new_loss > curr_loss + 1e-9 and backtrack < 20:
                step *= 0.5
                new_theta = [theta[j] + step * delta[j] for j in range(D + 1)]
                new_loss = get_loss(new_theta)
                backtrack += 1
                
            diff = math.sqrt(sum((new_theta[j] - theta[j]) ** 2 for j in range(D + 1)))
            theta = new_theta
            if diff < tol:
                converged = True
                break
                
        final_loss = get_loss(theta)
        final_p = [stable_sigmoid(sum(theta[j] * A[i][j] for j in range(D + 1))) for i in range(N)]
        final_grad = [0.0] * (D + 1)
        for j in range(D + 1):
            s = sum((final_p[i] - y[i]) * A[i][j] for i in range(N))
            final_grad[j] = (s / float(N)) + (l2 * theta[j] if j > 0 else 0.0)
        final_grad_norm = math.sqrt(sum(g ** 2 for g in final_grad))
        if final_grad_norm < tol:
            converged = True
            
        return theta, final_loss, it + 1, final_grad_norm, converged

    # 1. Baseline by fold (Pure expert baseline on primary rows: expertScore descending, id ascending)
    baseline_by_fold: Dict[str, Dict[str, Any]] = {}
    base_ap_list = []
    base_p10_list = []
    base_p20_list = []
    
    for val_g in groups:
        g_rows = [r for r in primary_rows if r["groupId"] == val_g]
        g_sorted = sorted(g_rows, key=lambda r: (-r["expertScore"], r["id"]))
        pos = sum(1 for r in g_rows if r["positive"])
        ap, p10, p20 = _compute_metrics(g_sorted, pos)
        baseline_by_fold[val_g] = {
            "groupId": val_g,
            "n": len(g_rows),
            "positives": pos,
            "ap": ap,
            "p10": p10,
            "p20": p20,
            "rankedIds": [r["id"] for r in g_sorted],
        }
        base_ap_list.append(ap if ap is not None else 0.0)
        base_p10_list.append(p10 if p10 is not None else 0.0)
        base_p20_list.append(p20 if p20 is not None else 0.0)
        
    baseline_macro = {
        "macroAp": sum(base_ap_list) / float(len(groups)),
        "macroP10": sum(base_p10_list) / float(len(groups)),
        "macroP20": sum(base_p20_list) / float(len(groups)),
        "numFolds": len(groups),
    }

    # 2. Evaluate all 36 configurations across the 4 blocked folds
    evaluated_configurations = []
    guard_passed_configs = []
    
    for f_set in feature_sets:
        # Check if all required features in this set are available on primary rows
        feats_available = all(feature_availability.get(f, False) for f in f_set) and primary_has_all_semantic
        
        for l2 in l2_lambdas:
            for head in protected_heads:
                config_id = f"feat_{'_'.join(f_set)}_l2_{l2}_head_{head}"
                config_dict = {
                    "method": "blocked-logistic",
                    "features": f_set,
                    "l2": l2,
                    "protectedHead": head,
                }
                
                if not feats_available:
                    evaluated_configurations.append({
                        "configId": config_id,
                        "config": config_dict,
                        "status": "unavailable-feature-missing",
                        "guardPassed": False,
                        "guardFailureReasons": ["Required feature missing on primary rows; configuration unavailable."],
                        "foldEvaluations": [],
                        "macroMetrics": None,
                    })
                    continue
                    
                fold_evals = []
                fold_ap_list = []
                fold_p10_list = []
                fold_p20_list = []
                all_converged = True
                failure_reasons = []
                
                for val_g in groups:
                    train_rows = [r for r in primary_rows if r["groupId"] != val_g]
                    val_rows = [r for r in primary_rows if r["groupId"] == val_g]
                    val_pos = sum(1 for r in val_rows if r["positive"])
                    
                    # Compute train-only statistics for standardizing features
                    means = {}
                    stds = {}
                    for feat in f_set:
                        train_vals = [transform_feat(r, feat) for r in train_rows]
                        m = sum(train_vals) / float(len(train_vals))
                        var = sum((v - m) ** 2 for v in train_vals) / float(len(train_vals))
                        s = math.sqrt(var)
                        if s == 0.0:
                            s = 1.0
                        means[feat] = m
                        stds[feat] = s
                        
                    X_train = [
                        [(transform_feat(r, feat) - means[feat]) / stds[feat] for feat in f_set]
                        for r in train_rows
                    ]
                    y_train = [1.0 if r["positive"] else 0.0 for r in train_rows]
                    
                    theta, solver_loss, iters, g_norm, converged = solve_logistic(X_train, y_train, l2=l2)
                    if not converged:
                        all_converged = False
                        failure_reasons.append(f"Fold {val_g} failed to converge (iterations={iters}, gradNorm={g_norm:.2e})")
                        
                    # Pre-calculate baseline expert order for fold to break ties and head protection
                    val_expert_order = sorted(val_rows, key=lambda r: (-r["expertScore"], r["id"]))
                    val_expert_rank = {r["id"]: idx for idx, r in enumerate(val_expert_order, start=1)}
                    
                    # Score validation rows: z = b + sum(beta * xstd)
                    scored_val = []
                    for r in val_rows:
                        xstd = [(transform_feat(r, feat) - means[feat]) / stds[feat] for feat in f_set]
                        z = theta[0] + sum(theta[j + 1] * xstd[j] for j in range(len(f_set)))
                        scored_val.append({
                            "row": r,
                            "id": r["id"],
                            "score": z,
                            "expertRank": val_expert_rank[r["id"]],
                        })
                        
                    # Candidate score descending, ties old expertRank ascending, id ascending
                    candidate_sorted = sorted(scored_val, key=lambda x: (-x["score"], x["expertRank"], x["id"]))
                    
                    # Protected head applied AFTER model score
                    if head > 0:
                        head_rows = val_expert_order[:head]
                        head_ids = set(r["id"] for r in head_rows)
                        tail_rows = [x["row"] for x in candidate_sorted if x["id"] not in head_ids]
                        val_ranked = head_rows + tail_rows
                    else:
                        val_ranked = [x["row"] for x in candidate_sorted]
                        
                    ap, p10, p20 = _compute_metrics(val_ranked, val_pos)
                    fold_ap_list.append(ap if ap is not None else 0.0)
                    fold_p10_list.append(p10 if p10 is not None else 0.0)
                    fold_p20_list.append(p20 if p20 is not None else 0.0)
                    
                    base_f = baseline_by_fold[val_g]
                    delta_f_ap = (ap - base_f["ap"]) if (ap is not None and base_f["ap"] is not None) else 0.0
                    delta_f_p10 = (p10 - base_f["p10"]) if (p10 is not None and base_f["p10"] is not None) else 0.0
                    delta_f_p20 = (p20 - base_f["p20"]) if (p20 is not None and base_f["p20"] is not None) else 0.0
                    
                    # Individual fold degradation check (epsilon 1e-12)
                    tol = 1e-12
                    if p10 is not None and base_f["p10"] is not None and p10 < base_f["p10"] - tol:
                        failure_reasons.append(f"Fold {val_g} P10 degraded: {p10:.4f} < {base_f['p10']:.4f}")
                    if p20 is not None and base_f["p20"] is not None and p20 < base_f["p20"] - tol:
                        failure_reasons.append(f"Fold {val_g} P20 degraded: {p20:.4f} < {base_f['p20']:.4f}")
                        
                    fold_evals.append({
                        "validationGroupId": val_g,
                        "n": len(val_rows),
                        "positives": val_pos,
                        "ap": ap,
                        "p10": p10,
                        "p20": p20,
                        "deltaApVsBaseline": delta_f_ap,
                        "deltaP10VsBaseline": delta_f_p10,
                        "deltaP20VsBaseline": delta_f_p20,
                        "solver": {
                            "intercept": theta[0],
                            "coefficients": {feat: theta[idx + 1] for idx, feat in enumerate(f_set)},
                            "objectiveLoss": solver_loss,
                            "iterations": iters,
                            "gradientNorm": g_norm,
                            "converged": converged,
                        },
                        "trainStatistics": {
                            "means": means,
                            "stds": stds,
                        },
                        "top10Ids": [r["id"] for r in val_ranked[:10]],
                        "rankedIds": [r["id"] for r in val_ranked],
                    })
                    
                macro_ap = sum(fold_ap_list) / float(len(groups))
                macro_p10 = sum(fold_p10_list) / float(len(groups))
                macro_p20 = sum(fold_p20_list) / float(len(groups))
                mean_ap_gain = macro_ap - baseline_macro["macroAp"]
                delta_macro_p10 = macro_p10 - baseline_macro["macroP10"]
                delta_macro_p20 = macro_p20 - baseline_macro["macroP20"]
                
                # Check mean AP gain >= 0.005 (with epsilon tolerance)
                if mean_ap_gain < 0.005 - tol:
                    failure_reasons.append(f"meanAPgain insufficient: {mean_ap_gain:+.6f} < +0.005")
                if not all_converged:
                    failure_reasons.append("One or more fold fits did not converge.")
                    
                guard_passed = (len(failure_reasons) == 0 and all_converged)
                
                cfg_result = {
                    "configId": config_id,
                    "config": config_dict,
                    "status": "evaluated",
                    "guardPassed": guard_passed,
                    "guardFailureReasons": failure_reasons,
                    "macroMetrics": {
                        "macroAp": macro_ap,
                        "macroP10": macro_p10,
                        "macroP20": macro_p20,
                        "meanApGain": mean_ap_gain,
                        "deltaMacroP10": delta_macro_p10,
                        "deltaMacroP20": delta_macro_p20,
                    },
                    "foldEvaluations": fold_evals,
                }
                evaluated_configurations.append(cfg_result)
                
                if guard_passed:
                    guard_passed_configs.append(cfg_result)

    # Find best guard-failed diagnostic config (highest meanApGain among evaluated configs)
    all_evaluated = [c for c in evaluated_configurations if c.get("macroMetrics") is not None]
    all_evaluated_sorted_gain = sorted(
        all_evaluated,
        key=lambda c: (
            -c["macroMetrics"]["meanApGain"],
            len(c["config"]["features"]),
            -c["config"]["l2"],
            c["config"]["protectedHead"]
        )
    )
    best_diagnostic_config = all_evaluated_sorted_gain[0] if all_evaluated_sorted_gain else None

    # Selection logic:
    # "Select among guardpass greatestmeanAP then simplerfeatures (#features), largerL2, smallerhead, deterministicdefinedorder. If none pass recommendedConfig={method:'expert-only',reason:...}, include bestguardfailed diagnostic config but NO forcedfusion."
    final_calibration_fit = None
    if guard_passed_configs:
        guard_passed_configs.sort(
            key=lambda c: (
                -c["macroMetrics"]["macroAp"],
                len(c["config"]["features"]),     # simpler features (# features ascending)
                -c["config"]["l2"],               # larger L2 descending
                c["config"]["protectedHead"]      # smaller head ascending
            )
        )
        winner_cfg = guard_passed_configs[0]
        recommended_config = {
            "method": "blocked-logistic",
            "features": winner_cfg["config"]["features"],
            "l2": winner_cfg["config"]["l2"],
            "protectedHead": winner_cfg["config"]["protectedHead"],
            "status": "guard-passed-winner",
            "meanApGain": winner_cfg["macroMetrics"]["meanApGain"],
        }
        recommendation = {
            "winner": winner_cfg["configId"],
            "status": "guard-passed",
            "reason": (
                f"Configuration {winner_cfg['configId']} passed all success guards with mean AP gain "
                f"+{winner_cfg['macroMetrics']['meanApGain']:.6f} >= +0.005 and no P10/P20 degradation in any fold."
            ),
            "bestGuardFailedDiagnostic": None,
        }
        # Fit ONE chosen model on all primary 158 rows
        chosen_feats = winner_cfg["config"]["features"]
        chosen_l2 = winner_cfg["config"]["l2"]
        chosen_head = winner_cfg["config"]["protectedHead"]
        
        all_means = {}
        all_stds = {}
        for feat in chosen_feats:
            all_vals = [transform_feat(r, feat) for r in primary_rows]
            m = sum(all_vals) / float(len(all_vals))
            var = sum((v - m) ** 2 for v in all_vals) / float(len(all_vals))
            s = math.sqrt(var)
            if s == 0.0:
                s = 1.0
            all_means[feat] = m
            all_stds[feat] = s
            
        X_all = [
            [(transform_feat(r, feat) - all_means[feat]) / all_stds[feat] for feat in chosen_feats]
            for r in primary_rows
        ]
        y_all = [1.0 if r["positive"] else 0.0 for r in primary_rows]
        
        all_theta, all_loss, all_iters, all_gnorm, all_conv = solve_logistic(X_all, y_all, l2=chosen_l2)
        
        # Primary rows scored and ordered
        all_expert_order = sorted(primary_rows, key=lambda r: (-r["expertScore"], r["id"]))
        all_expert_rank = {r["id"]: idx for idx, r in enumerate(all_expert_order, start=1)}
        scored_all = []
        for r in primary_rows:
            xstd = [(transform_feat(r, feat) - all_means[feat]) / all_stds[feat] for feat in chosen_feats]
            z = all_theta[0] + sum(all_theta[j + 1] * xstd[j] for j in range(len(chosen_feats)))
            scored_all.append({
                "row": r,
                "id": r["id"],
                "score": z,
                "expertRank": all_expert_rank[r["id"]],
            })
        cand_all_sorted = sorted(scored_all, key=lambda x: (-x["score"], x["expertRank"], x["id"]))
        if chosen_head > 0:
            h_rows = all_expert_order[:chosen_head]
            h_ids = set(r["id"] for r in h_rows)
            t_rows = [x["row"] for x in cand_all_sorted if x["id"] not in h_ids]
            fin_ranked_primary = h_rows + t_rows
        else:
            fin_ranked_primary = [x["row"] for x in cand_all_sorted]
            
        final_calibration_fit = {
            "features": chosen_feats,
            "l2": chosen_l2,
            "protectedHead": chosen_head,
            "intercept": all_theta[0],
            "coefficients": {feat: all_theta[idx + 1] for idx, feat in enumerate(chosen_feats)},
            "means": all_means,
            "scales": all_stds,
            "featureTransforms": {
                "E": "raw expertScore",
                "S": "min(max(p, 1e-6), 1-1e-6) -> logit(p/(1-p))",
                "D": "motion.dinoTemporalDistanceMean",
                "R": "motion.roiDifferenceMean",
            },
            "formula": "z = intercept + sum(coefficients[f] * (transformed(f) - means[f]) / scales[f])",
            "solver": {
                "objectiveLoss": all_loss,
                "iterations": all_iters,
                "gradientNorm": all_gnorm,
                "converged": all_conv,
            },
            "rankedPrimaryIDs": [r["id"] for r in fin_ranked_primary],
            "semanticFallbackTailIDs": [r["id"] for r in missing_with_valid_semantic_sorted],
            "fallbackPolicy": "missing-expert items with valid semanticScore appended in semanticScore descending order; missing-motion or missing-semantic groups fallback safely to pure expert.",
        }
    else:
        # None passed guard -> baseline fallback
        reason_str = (
            "No logistic candidate satisfied predeclared success guards (mean AP gain >= +0.005 and no P10/P20 degradation in any fold). "
            "Falling back to pure expert baseline without forced fusion."
        )
        recommended_config = {
            "method": "expert-only",
            "reason": reason_str,
            "status": "fallback-to-expert-baseline",
        }
        recommendation = {
            "winner": "expert-only",
            "status": "baseline-fallback",
            "reason": reason_str,
            "bestGuardFailedDiagnostic": {
                "configId": best_diagnostic_config["configId"] if best_diagnostic_config else None,
                "features": best_diagnostic_config["config"]["features"] if best_diagnostic_config else None,
                "l2": best_diagnostic_config["config"]["l2"] if best_diagnostic_config else None,
                "protectedHead": best_diagnostic_config["config"]["protectedHead"] if best_diagnostic_config else None,
                "macroMetrics": best_diagnostic_config["macroMetrics"] if best_diagnostic_config else None,
                "guardFailureReasons": best_diagnostic_config["guardFailureReasons"] if best_diagnostic_config else None,
            } if best_diagnostic_config else None,
        }

    limitations = [
        "Retrospective calibration: these examples were previously seen historically and do not constitute unseen independent proof.",
        "Blocked cross-validation figures are within-fold macro statistics only; out-of-fold raw predictions are never pooled into a single ranking.",
        "Candidate logistic probabilities are fitted ordering logits, not independently calibrated real-world posterior probabilities.",
        "Missing expert score record represents an absent score entry in historical logs, not an engine missed event.",
        "Protected head structure fixes top expert items but does not alter underlying detector predictions.",
        "Fallback policy appends semantic-only items after expert-ranked items without fabricating artificial expert scores."
    ]

    out_doc = {
        "schemaVersion": 1,
        "evaluationScope": meta["scope"],
        "evaluationDesign": {
            "method": "blocked-logistic-regression",
            "folds": "leave-one-group-out (4 folds)",
            "groups": groups,
            "featureSets": feature_sets,
            "l2Lambdas": l2_lambdas,
            "protectedHeads": protected_heads,
            "totalConfigurations": len(feature_sets) * len(l2_lambdas) * len(protected_heads),
            "successGuard": "meanAPgain >= 0.005 AND no individual fold P10 or P20 degradation (epsilon 1e-12) AND all fits converged",
        },
        "counts": {
            "totalRows": total_count,
            "totalPositives": positives_count,
            "primaryExpertPresent": primary_count,
            "primaryExpertPresentPositives": primary_positives,
            "missingExpert": missing_count,
            "missingExpertPositives": missing_positives,
            "missingExpertWithSemantic": len(missing_with_valid_semantic),
            "missingExpertExcluded": len(excluded_no_evidence),
        },
        "baselineByFold": baseline_by_fold,
        "baselineMacro": baseline_macro,
        "configurations": evaluated_configurations,
        "recommendedConfig": recommended_config,
        "recommendation": recommendation,
        "limitations": limitations,
    }
    
    if final_calibration_fit is not None:
        out_doc["finalCalibrationFit"] = final_calibration_fit
        
    return out_doc


def _safe_write_json(out_path: str, data: Dict[str, Any], overwrite: bool = False, in_path: Optional[str] = None) -> None:
    # Resolve realpaths
    norm_out = os.path.realpath(os.path.abspath(out_path))
    if in_path:
        norm_in = os.path.realpath(os.path.abspath(in_path))
        if norm_out == norm_in:
            raise ValueError("Output file cannot have the same realpath/inode as input file.")
            
    out_dir = os.path.dirname(norm_out)
    if out_dir and not os.path.exists(out_dir):
        os.makedirs(out_dir, exist_ok=True)
        
    if os.path.exists(norm_out) and not overwrite:
        raise FileExistsError(f"Output file already exists and overwrite is False: {os.path.basename(norm_out)}")
        
    # Atomic exclusive write using temporary file in same directory
    prefix = f".tmp_{os.path.basename(norm_out)}_"
    fd, tmp_file = tempfile.mkstemp(prefix=prefix, dir=out_dir)
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        # Rename atomically
        if not overwrite and os.path.exists(norm_out):
            raise FileExistsError(f"Output file already exists: {os.path.basename(norm_out)}")
        os.replace(tmp_file, norm_out)
    except Exception:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass
        raise


def main():
    parser = argparse.ArgumentParser(description="Bounded retrospective calibration expert+semantic fusion.")
    parser.add_argument("--input", required=True, help="Input JSON file path.")
    parser.add_argument("--output", required=True, help="Output JSON file path.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting output file.")
    parser.add_argument(
        "--experiment",
        default="rrf",
        choices=["rrf", "blocked-logistic"],
        help="Experiment method: rrf (default) or blocked-logistic."
    )
    
    args = parser.parse_args()
    
    try:
        if not os.path.exists(args.input):
            raise FileNotFoundError(f"Input file not found: {os.path.basename(args.input)}")
            
        with open(args.input, "r", encoding="utf-8") as f:
            doc = json.load(f)
            
        if args.experiment == "blocked-logistic":
            result = calculate_confidence_fusion(doc)
        else:
            result = calculate_fusion(doc)
            
        _safe_write_json(args.output, result, overwrite=args.overwrite, in_path=args.input)
        print(f"Calibration successfully calculated ({args.experiment}) and written to: {os.path.basename(args.output)}")
    except Exception as e:
        sys.stderr.write(f"Error: {type(e).__name__}: {str(e)}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()

