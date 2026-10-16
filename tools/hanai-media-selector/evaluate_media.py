#!/usr/bin/env python3
"""
Numeric-only evaluation harness for HanAI media selector.
Pure Python standard library implementation.
"""

import sys
import os
import json
import argparse
import tempfile
import math
from typing import Any, Dict, List, Optional, Tuple, Set

MAX_CASES = 10000
MAX_CANDIDATES = 2000
MAX_PAIRS = 250000
DEFAULT_TOLERANCE_SECONDS = 3.0


class ValidationError(Exception):
    pass


def _is_exact_bool(val: Any) -> bool:
    return isinstance(val, bool)


def _is_exact_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _is_finite_number(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if not isinstance(val, (int, float)):
        return False
    return math.isfinite(val)


def _parse_strict_json(content: str) -> Any:
    def _parse_constant(c: str):
        raise ValidationError(f"Invalid non-finite JSON constant: {c}")

    try:
        return json.loads(content, parse_constant=_parse_constant)
    except Exception as e:
        raise ValidationError(f"Strict JSON parsing failed: {e}")


def _validate_root(doc: Any) -> None:
    if not isinstance(doc, dict):
        raise ValidationError("Document root must be an object.")
    if "schemaVersion" not in doc or doc["schemaVersion"] != 1 or _is_exact_bool(doc["schemaVersion"]):
        raise ValidationError("schemaVersion must be integer 1.")
    if "evaluationScope" in doc and not isinstance(doc["evaluationScope"], str):
        raise ValidationError("evaluationScope must be string if present.")
    if "metadata" in doc and not isinstance(doc["metadata"], dict):
        raise ValidationError("metadata must be an object if present.")
    if "cases" not in doc or not isinstance(doc["cases"], list):
        raise ValidationError("cases must be a list.")
    if len(doc["cases"]) > MAX_CASES:
        raise ValidationError(f"cases count exceeds maximum allowed limit {MAX_CASES}.")


def _validate_and_check_leakage(cases: List[Dict[str, Any]]) -> None:
    calibration_sources: Set[str] = set()
    calibration_sessions: Set[str] = set()
    heldout_sources: Set[str] = set()
    heldout_sessions: Set[str] = set()

    case_ids: Set[str] = set()

    for idx, c in enumerate(cases):
        if not isinstance(c, dict):
            raise ValidationError(f"Case at index {idx} must be an object.")

        case_id = c.get("caseId")
        if not isinstance(case_id, str) or not case_id:
            raise ValidationError(f"Case at index {idx} has missing or empty caseId.")
        if case_id in case_ids:
            raise ValidationError(f"Duplicate caseId found: {case_id}")
        case_ids.add(case_id)

        mode = c.get("mode")
        if mode not in ("photo", "video"):
            raise ValidationError(f"Case {case_id} has invalid mode: {mode}")

        split = c.get("split")
        if split not in ("calibration", "heldOut"):
            raise ValidationError(f"Case {case_id} has invalid split: {split}")

        source_id = c.get("sourceId")
        session_id = c.get("sessionId")
        if not isinstance(source_id, str) or not source_id:
            raise ValidationError(f"Case {case_id} missing or invalid sourceId.")
        if not isinstance(session_id, str) or not session_id:
            raise ValidationError(f"Case {case_id} missing or invalid sessionId.")

        if split == "calibration":
            calibration_sources.add(source_id)
            calibration_sessions.add(session_id)
        else:
            heldout_sources.add(source_id)
            heldout_sessions.add(session_id)

        # Candidates validation
        candidates = c.get("candidates")
        if not isinstance(candidates, list):
            raise ValidationError(f"Case {case_id} candidates must be a list.")
        if len(candidates) > MAX_CANDIDATES:
            raise ValidationError(f"Case {case_id} candidate count exceeds {MAX_CANDIDATES}.")

        cand_indices: Set[int] = set()
        cand_time_map: Dict[int, float] = {}
        for cand in candidates:
            if not isinstance(cand, dict):
                raise ValidationError(f"Case {case_id} candidate must be an object.")
            c_idx = cand.get("index")
            if not _is_exact_int(c_idx) or c_idx < 0:
                raise ValidationError(f"Case {case_id} candidate index must be a non-negative int.")
            if c_idx in cand_indices:
                raise ValidationError(f"Case {case_id} duplicate candidate index: {c_idx}")
            cand_indices.add(c_idx)
            if "timeSeconds" in cand:
                t_sec = cand["timeSeconds"]
                if not _is_finite_number(t_sec) or t_sec < 0.0:
                    raise ValidationError(f"Case {case_id} candidate timeSeconds must be non-negative finite number.")
                cand_time_map[c_idx] = float(t_sec)

        # Request validation
        req = c.get("request")
        if not isinstance(req, dict):
            raise ValidationError(f"Case {case_id} request must be an object.")
        req_count = req.get("count")
        if not _is_exact_int(req_count) or req_count < 0:
            raise ValidationError(f"Case {case_id} request.count must be a non-negative int.")
        req_budget = req.get("budgetSeconds")
        if req_budget is not None:
            if not _is_finite_number(req_budget) or req_budget < 0.0:
                raise ValidationError(f"Case {case_id} request.budgetSeconds must be non-negative finite number.")

        # Photo GT & Prefs validation
        photo_pairs = c.get("photoPairGT")
        seen_pairs: Set[Tuple[int, int]] = set()
        if photo_pairs is not None:
            if not isinstance(photo_pairs, list):
                raise ValidationError(f"Case {case_id} photoPairGT must be a list.")
            if len(photo_pairs) > MAX_PAIRS:
                raise ValidationError(f"Case {case_id} photoPairGT exceeds {MAX_PAIRS} pairs.")
            for p in photo_pairs:
                if not isinstance(p, dict):
                    raise ValidationError(f"Case {case_id} photoPairGT item must be an object.")
                f_idx = p.get("firstIndex")
                s_idx = p.get("secondIndex")
                if not _is_exact_int(f_idx) or not _is_exact_int(s_idx) or f_idx < 0 or s_idx < 0:
                    raise ValidationError(f"Case {case_id} photoPairGT indices must be non-negative int.")
                if f_idx == s_idx:
                    raise ValidationError(f"Case {case_id} photoPairGT cannot pair candidate with itself.")
                if f_idx not in cand_indices or s_idx not in cand_indices:
                    raise ValidationError(f"Case {case_id} photoPairGT indices not in candidates.")
                pair_key = (min(f_idx, s_idx), max(f_idx, s_idx))
                if pair_key in seen_pairs:
                    raise ValidationError(f"Case {case_id} duplicate photoPairGT pair: {pair_key}")
                seen_pairs.add(pair_key)
                label = p.get("label")
                if label not in ("similar", "different", "unknown"):
                    raise ValidationError(f"Case {case_id} photoPairGT invalid label: {label}")
                origin = p.get("origin")
                if origin not in ("tm-visual-review", "user"):
                    raise ValidationError(f"Case {case_id} photoPairGT invalid origin: {origin}")

        rep_prefs = c.get("representativePrefs")
        if rep_prefs is not None:
            if not isinstance(rep_prefs, list):
                raise ValidationError(f"Case {case_id} representativePrefs must be a list.")
            for rp in rep_prefs:
                if not isinstance(rp, dict):
                    raise ValidationError(f"Case {case_id} representativePref must be an object.")
                p_idx = rp.get("preferredIndex")
                o_idx = rp.get("otherIndex")
                if not _is_exact_int(p_idx) or not _is_exact_int(o_idx) or p_idx < 0 or o_idx < 0:
                    raise ValidationError(f"Case {case_id} representativePrefs indices must be non-negative int.")
                if p_idx == o_idx:
                    raise ValidationError(f"Case {case_id} representativePrefs indices must be distinct.")
                if p_idx not in cand_indices or o_idx not in cand_indices:
                    raise ValidationError(f"Case {case_id} representativePrefs index not in candidates.")

        # Video GT validation
        video_gt = c.get("videoGT")
        seen_event_ids: Set[str] = set()
        if video_gt is not None:
            if not isinstance(video_gt, list):
                raise ValidationError(f"Case {case_id} videoGT must be a list.")
            for v_idx, ev in enumerate(video_gt):
                if not isinstance(ev, dict):
                    raise ValidationError(f"Case {case_id} videoGT event must be an object.")
                ev_id = ev.get("eventId")
                if not isinstance(ev_id, str) or not ev_id:
                    raise ValidationError(f"Case {case_id} videoGT missing or invalid eventId.")
                if ev_id in seen_event_ids:
                    raise ValidationError(f"Case {case_id} duplicate videoGT eventId: {ev_id}")
                seen_event_ids.add(ev_id)
                t_sec = ev.get("timeSeconds")
                if not _is_finite_number(t_sec) or t_sec < 0.0:
                    raise ValidationError(f"Case {case_id} videoGT timeSeconds must be non-negative finite number.")
                s_sec = ev.get("startSeconds")
                e_sec = ev.get("endSeconds")
                if s_sec is not None or e_sec is not None:
                    if s_sec is None or e_sec is None:
                        raise ValidationError(f"Case {case_id} videoGT event interval requires both startSeconds and endSeconds.")
                    if not _is_finite_number(s_sec) or not _is_finite_number(e_sec) or s_sec < 0.0 or e_sec < 0.0:
                        raise ValidationError(f"Case {case_id} videoGT event interval bounds must be non-negative finite numbers.")
                    if e_sec < s_sec:
                        raise ValidationError(f"Case {case_id} videoGT event endSeconds < startSeconds.")
                ev_label = ev.get("label")
                if ev_label not in ("positive", "negative"):
                    raise ValidationError(f"Case {case_id} videoGT invalid label: {ev_label}")
                ev_orig = ev.get("origin")
                if ev_orig not in ("user", "tm-visual-review"):
                    raise ValidationError(f"Case {case_id} videoGT invalid origin: {ev_orig}")

        # Label coverage intervals
        coverage_intervals = c.get("labelCoverageIntervals")
        if coverage_intervals is not None:
            if not isinstance(coverage_intervals, list):
                raise ValidationError(f"Case {case_id} labelCoverageIntervals must be a list.")
            for cov in coverage_intervals:
                if not isinstance(cov, dict):
                    raise ValidationError(f"Case {case_id} labelCoverageInterval must be an object.")
                cs_sec = cov.get("startSeconds")
                ce_sec = cov.get("endSeconds")
                if not _is_finite_number(cs_sec) or not _is_finite_number(ce_sec) or cs_sec < 0.0 or ce_sec < 0.0:
                    raise ValidationError(f"Case {case_id} coverage interval bounds must be non-negative finite numbers.")
                if ce_sec < cs_sec:
                    raise ValidationError(f"Case {case_id} coverage interval endSeconds < startSeconds.")

        tol = c.get("matchingToleranceSeconds", DEFAULT_TOLERANCE_SECONDS)
        if not _is_finite_number(tol) or tol < 0.0:
            raise ValidationError(f"Case {case_id} matchingToleranceSeconds must be non-negative finite number.")

        # Runs validation
        runs = c.get("runs")
        if not isinstance(runs, list):
            raise ValidationError(f"Case {case_id} runs must be a list.")
        seen_run_ids: Set[str] = set()

        for r_idx, run in enumerate(runs):
            if not isinstance(run, dict):
                raise ValidationError(f"Case {case_id} run at {r_idx} must be an object.")
            run_id = run.get("runId")
            if not isinstance(run_id, str) or not run_id:
                raise ValidationError(f"Case {case_id} run missing runId.")
            if run_id in seen_run_ids:
                raise ValidationError(f"Case {case_id} duplicate runId: {run_id}")
            seen_run_ids.add(run_id)

            version = run.get("version")
            if not isinstance(version, str) or not version:
                raise ValidationError(f"Case {case_id} run {run_id} missing version.")
            variant = run.get("featureVariant")
            if not isinstance(variant, str) or not variant:
                raise ValidationError(f"Case {case_id} run {run_id} missing featureVariant.")

            prov = run.get("provenance")
            if not isinstance(prov, dict):
                raise ValidationError(f"Case {case_id} run {run_id} provenance must be an object.")
            actual_core = prov.get("actualCore")
            if actual_core is not True or not _is_exact_bool(actual_core):
                raise ValidationError(f"Case {case_id} run {run_id} provenance.actualCore must be exactly True.")
            runner_ver = prov.get("runnerVersion")
            if not isinstance(runner_ver, str) or not runner_ver:
                raise ValidationError(f"Case {case_id} run {run_id} provenance.runnerVersion must be nonempty string.")

            elapsed = run.get("elapsedSeconds")
            if elapsed is not None:
                if not _is_finite_number(elapsed) or elapsed < 0.0:
                    raise ValidationError(f"Case {case_id} run {run_id} elapsedSeconds must be non-negative finite number.")

            # Selections validation
            sel_indices = run.get("selectedIndices")
            sel_frames = run.get("selectedFrameIndices")
            active_selection = sel_indices if mode == "photo" else sel_frames

            if active_selection is not None:
                if not isinstance(active_selection, list):
                    raise ValidationError(f"Case {case_id} run {run_id} selected list must be an array.")
                if len(active_selection) > req_count:
                    raise ValidationError(f"Case {case_id} run {run_id} count violation: selected {len(active_selection)} > request.count {req_count}")
                seen_sel: Set[int] = set()
                for s_i in active_selection:
                    if not _is_exact_int(s_i) or s_i < 0:
                        raise ValidationError(f"Case {case_id} run {run_id} selected index must be non-negative int.")
                    if s_i in seen_sel:
                        raise ValidationError(f"Case {case_id} run {run_id} duplicate selected index: {s_i}")
                    seen_sel.add(s_i)
                    if s_i not in cand_indices:
                        raise ValidationError(f"Case {case_id} run {run_id} selected index {s_i} not in candidate indices.")

            # selectionOrderIndices optional check
            sel_order = run.get("selectionOrderIndices")
            if sel_order is not None:
                if not isinstance(sel_order, list):
                    raise ValidationError(f"Case {case_id} run {run_id} selectionOrderIndices must be a list.")
                if active_selection is None:
                    raise ValidationError(f"Case {case_id} run {run_id} selectionOrderIndices provided without selected indices.")
                if set(sel_order) != set(active_selection) or len(sel_order) != len(active_selection):
                    raise ValidationError(f"Case {case_id} run {run_id} selectionOrderIndices must match selected indices exactly as a set.")

            # Photo groups
            groups = run.get("groups")
            if groups is not None:
                if not isinstance(groups, list):
                    raise ValidationError(f"Case {case_id} run {run_id} groups must be a list.")
                seen_members: Set[int] = set()
                for g in groups:
                    if not isinstance(g, dict):
                        raise ValidationError(f"Case {case_id} run {run_id} group must be an object.")
                    rep_i = g.get("representativeIndex")
                    if not _is_exact_int(rep_i) or rep_i not in cand_indices:
                        raise ValidationError(f"Case {case_id} run {run_id} group representativeIndex must be valid candidate index.")
                    members = g.get("memberIndices")
                    if not isinstance(members, list):
                        raise ValidationError(f"Case {case_id} run {run_id} group memberIndices must be a list.")
                    for m_i in members:
                        if not _is_exact_int(m_i) or m_i not in cand_indices:
                            raise ValidationError(f"Case {case_id} run {run_id} memberIndex must be valid candidate index.")
                        if m_i in seen_members:
                            raise ValidationError(f"Case {case_id} run {run_id} candidate {m_i} assigned to multiple groups.")
                        seen_members.add(m_i)
                    if rep_i not in members:
                        raise ValidationError(f"Case {case_id} run {run_id} representativeIndex must be in memberIndices.")

            # Pair decision scope validation
            scope_val = run.get("pairDecisionScope")
            if scope_val is not None:
                if scope_val not in ("isolated-pair", "same-batch"):
                    raise ValidationError(f"Case {case_id} run {run_id} invalid pairDecisionScope: {scope_val}")

            # Predicted photo pairs
            pred_pairs = run.get("predictedPhotoPairs")
            if pred_pairs is not None:
                if not isinstance(pred_pairs, list):
                    raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPairs must be a list.")
                seen_pred_pairs: Set[Tuple[int, int]] = set()
                for pp in pred_pairs:
                    if not isinstance(pp, dict):
                        raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPair must be an object.")
                    pf_idx = pp.get("firstIndex")
                    ps_idx = pp.get("secondIndex")
                    if not _is_exact_int(pf_idx) or not _is_exact_int(ps_idx) or pf_idx < 0 or ps_idx < 0:
                        raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPair indices must be non-negative int.")
                    if pf_idx == ps_idx:
                        raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPair indices cannot be identical.")
                    if pf_idx not in cand_indices or ps_idx not in cand_indices:
                        raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPair index not in candidates.")
                    sim = pp.get("similar")
                    if not _is_exact_bool(sim):
                        raise ValidationError(f"Case {case_id} run {run_id} predictedPhotoPair similar must be exact bool.")
                    p_key = (min(pf_idx, ps_idx), max(pf_idx, ps_idx))
                    if p_key in seen_pred_pairs:
                        raise ValidationError(f"Case {case_id} run {run_id} duplicate predictedPhotoPair: {p_key}")
                    seen_pred_pairs.add(p_key)

            # Segments validation
            segments = run.get("segments")
            if segments is not None:
                if not isinstance(segments, list):
                    raise ValidationError(f"Case {case_id} run {run_id} segments must be a list.")
                total_duration = 0.0
                prev_end = -1.0
                seen_anchors: Set[int] = set()
                for seg in segments:
                    if not isinstance(seg, dict):
                        raise ValidationError(f"Case {case_id} run {run_id} segment must be an object.")
                    ss_sec = seg.get("startSeconds")
                    se_sec = seg.get("endSeconds")
                    if not _is_finite_number(ss_sec) or not _is_finite_number(se_sec) or ss_sec < 0.0 or se_sec < 0.0:
                        raise ValidationError(f"Case {case_id} run {run_id} segment bounds must be non-negative finite numbers.")
                    if se_sec < ss_sec:
                        raise ValidationError(f"Case {case_id} run {run_id} segment endSeconds < startSeconds.")
                    # Out-of-order or overlapping check for positive-length segments
                    seg_len = se_sec - ss_sec
                    if seg_len > 0:
                        if ss_sec < prev_end:
                            raise ValidationError(f"Case {case_id} run {run_id} segments are overlapping or out of order.")
                        prev_end = se_sec
                        total_duration += seg_len
                    anchors = seg.get("anchorIndices")
                    if anchors is not None:
                        if not isinstance(anchors, list):
                            raise ValidationError(f"Case {case_id} run {run_id} segment anchorIndices must be a list.")
                        for a_idx in anchors:
                            if not _is_exact_int(a_idx) or a_idx not in cand_indices:
                                raise ValidationError(f"Case {case_id} run {run_id} anchorIndex must be valid candidate index.")
                            if active_selection is not None and a_idx not in active_selection:
                                raise ValidationError(f"Case {case_id} run {run_id} anchorIndex {a_idx} not in selected indices.")
                            if a_idx in seen_anchors:
                                raise ValidationError(f"Case {case_id} run {run_id} anchorIndex {a_idx} assigned to multiple segments.")
                            seen_anchors.add(a_idx)

                if req_budget is not None and total_duration > req_budget + 1e-6:
                    raise ValidationError(f"Case {case_id} run {run_id} segment total duration {total_duration} > budgetSeconds {req_budget}")

        # In any photo case comparing runs with predictedPhotoPairs, pairDecisionScope values must match exactly
        if mode == "photo":
            runs_with_pairs = [r for r in runs if r.get("predictedPhotoPairs") is not None]
            if len(runs_with_pairs) > 1:
                first_scope = runs_with_pairs[0].get("pairDecisionScope", "unspecified")
                for r in runs_with_pairs[1:]:
                    r_scope = r.get("pairDecisionScope", "unspecified")
                    if r_scope != first_scope:
                        raise ValidationError(
                            f"Strict comparison fairness violation in photo case {case_id}: "
                            f"runs have mismatched pairDecisionScope ('{first_scope}' vs '{r_scope}')."
                        )

    # Leakage check: no shared sourceId or sessionId between calibration and heldOut
    shared_sources = calibration_sources.intersection(heldout_sources)
    if shared_sources:
        raise ValidationError(f"Data leakage detected: sourceId shared between calibration and heldOut splits: {sorted(list(shared_sources))}")
    shared_sessions = calibration_sessions.intersection(heldout_sessions)
    if shared_sessions:
        raise ValidationError(f"Data leakage detected: sessionId shared between calibration and heldOut splits: {sorted(list(shared_sessions))}")


def _check_run_definition_consistency(cases: List[Dict[str, Any]]) -> None:
    run_defs: Dict[str, Tuple[str, str, str]] = {}
    for c in cases:
        for run in c.get("runs", []):
            run_id = run["runId"]
            ver = run["version"]
            var = run["featureVariant"]
            scope = run.get("pairDecisionScope", "unspecified")
            entry = (ver, var, scope)
            if run_id in run_defs:
                if run_defs[run_id] != entry:
                    raise ValidationError(f"Run definition inconsistency for runId {run_id}: previously {run_defs[run_id]}, now {entry}")
            else:
                run_defs[run_id] = entry


def _evaluate_photo_case_run(c: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    photo_gt = c.get("photoPairGT")
    pred_pairs = run.get("predictedPhotoPairs")

    pair_metrics: Dict[str, Any] = {
        "status": "unmeasured",
        "humanGtPairCount": 0,
        "predictedPairCount": 0,
        "tp": 0,
        "fp": 0,
        "tn": 0,
        "fn": 0,
        "precision": None,
        "recall": None,
        "f1": None,
        "pairCoverage": None,
        "unknownGtCount": 0,
        "missingPredictionCount": 0,
    }

    if photo_gt is not None:
        gt_map: Dict[Tuple[int, int], str] = {}
        for p in photo_gt:
            key = (min(p["firstIndex"], p["secondIndex"]), max(p["firstIndex"], p["secondIndex"]))
            gt_map[key] = p["label"]

        pred_map: Dict[Tuple[int, int], bool] = {}
        if pred_pairs is not None:
            for pp in pred_pairs:
                key = (min(pp["firstIndex"], pp["secondIndex"]), max(pp["firstIndex"], pp["secondIndex"]))
                pred_map[key] = pp["similar"]

        pair_metrics["humanGtPairCount"] = len(gt_map)
        pair_metrics["predictedPairCount"] = len(pred_map)

        tp = fp = tn = fn = 0
        unknown_gt = 0
        missing_pred = 0
        evaluable_labeled_pairs = 0
        labeled_pairs_with_pred = 0

        for key, label in gt_map.items():
            if label == "unknown":
                unknown_gt += 1
                continue

            evaluable_labeled_pairs += 1
            if key not in pred_map:
                missing_pred += 1
                continue

            labeled_pairs_with_pred += 1
            pred_sim = pred_map[key]
            if label == "similar":
                if pred_sim:
                    tp += 1
                else:
                    fn += 1
            elif label == "different":
                if pred_sim:
                    fp += 1
                else:
                    tn += 1

        pair_metrics["tp"] = tp
        pair_metrics["fp"] = fp
        pair_metrics["tn"] = tn
        pair_metrics["fn"] = fn
        pair_metrics["unknownGtCount"] = unknown_gt
        pair_metrics["missingPredictionCount"] = missing_pred

        if evaluable_labeled_pairs > 0:
            pair_metrics["status"] = "evaluated"
            pair_metrics["pairCoverage"] = labeled_pairs_with_pred / evaluable_labeled_pairs
        else:
            pair_metrics["status"] = "unmeasured"
            pair_metrics["pairCoverage"] = None

        if (tp + fp) > 0:
            pair_metrics["precision"] = tp / (tp + fp)
        if (tp + fn) > 0:
            pair_metrics["recall"] = tp / (tp + fn)
        if pair_metrics["precision"] is not None and pair_metrics["recall"] is not None:
            denom = pair_metrics["precision"] + pair_metrics["recall"]
            if denom > 0:
                pair_metrics["f1"] = 2.0 * pair_metrics["precision"] * pair_metrics["recall"] / denom

    # Groups descriptive metrics
    groups = run.get("groups")
    group_metrics = {
        "groupCount": len(groups) if groups is not None else 0,
        "groupedCandidateCount": sum(len(g["memberIndices"]) for g in groups) if groups is not None else 0,
    }

    # Representative preferences
    rep_prefs = c.get("representativePrefs")
    pref_metrics: Dict[str, Any] = {
        "status": "unmeasured",
        "totalPrefs": len(rep_prefs) if rep_prefs is not None else 0,
        "evaluablePrefs": 0,
        "wins": 0,
        "losses": 0,
        "winRate": None,
    }

    if rep_prefs is not None and groups is not None:
        member_to_group: Dict[int, Dict[str, Any]] = {}
        for g in groups:
            for m in g["memberIndices"]:
                member_to_group[m] = g

        evaluable = 0
        wins = 0
        losses = 0
        for rp in rep_prefs:
            p_idx = rp["preferredIndex"]
            o_idx = rp["otherIndex"]
            g_p = member_to_group.get(p_idx)
            g_o = member_to_group.get(o_idx)
            if g_p is not None and g_p is g_o:
                rep_idx = g_p["representativeIndex"]
                if rep_idx == p_idx:
                    evaluable += 1
                    wins += 1
                elif rep_idx == o_idx:
                    evaluable += 1
                    losses += 1

        pref_metrics["evaluablePrefs"] = evaluable
        pref_metrics["wins"] = wins
        pref_metrics["losses"] = losses
        if evaluable > 0:
            pref_metrics["status"] = "evaluated"
            pref_metrics["winRate"] = wins / evaluable

    sel_indices = run.get("selectedIndices")
    selected_count = len(sel_indices) if sel_indices is not None else 0

    return {
        "selectedCount": selected_count,
        "pairMetrics": pair_metrics,
        "groupMetrics": group_metrics,
        "representativePrefMetrics": pref_metrics,
        "elapsedSeconds": run.get("elapsedSeconds"),
    }


def _max_bipartite_matching(u_nodes: List[int], graph: Dict[int, List[str]], v_priority: Dict[int, List[str]]) -> Dict[int, str]:
    """
    Standard augmenting path maximum bipartite matching.
    u_nodes: list of prediction indices
    graph[u]: list of candidate event IDs that prediction u can match
    v_priority[u]: sorted list of event IDs according to deterministic tie-breaking:
                   abs time diff ascending, then event ID lexicographically
    Returns matching: dict of u -> v
    """
    match_v_to_u: Dict[str, int] = {}
    match_u_to_v: Dict[int, str] = {}

    def dfs(u: int, visited_v: Set[str]) -> bool:
        for v in v_priority[u]:
            if v in visited_v:
                continue
            visited_v.add(v)
            if v not in match_v_to_u or dfs(match_v_to_u[v], visited_v):
                match_v_to_u[v] = u
                match_u_to_v[u] = v
                return True
        return False

    for u in u_nodes:
        visited: Set[str] = set()
        dfs(u, visited)

    return match_u_to_v


def _evaluate_video_case_run(c: Dict[str, Any], run: Dict[str, Any]) -> Dict[str, Any]:
    cand_time_map: Dict[int, float] = {
        cand["index"]: float(cand["timeSeconds"]) for cand in c["candidates"] if "timeSeconds" in cand
    }

    selected_frames = run.get("selectedFrameIndices")
    selection_order = run.get("selectionOrderIndices")
    tolerance = float(c.get("matchingToleranceSeconds", DEFAULT_TOLERANCE_SECONDS))

    if selection_order is not None:
        eval_order = selection_order
        ranking_available = True
    elif selected_frames is not None:
        eval_order = selected_frames
        ranking_available = False
    else:
        eval_order = []
        ranking_available = False

    video_gt = c.get("videoGT")
    coverage_intervals = c.get("labelCoverageIntervals")

    positive_events: List[Dict[str, Any]] = []
    negative_events: List[Dict[str, Any]] = []
    if video_gt is not None:
        for ev in video_gt:
            if ev["label"] == "positive":
                positive_events.append(ev)
            elif ev["label"] == "negative":
                negative_events.append(ev)

    def matches_event(pred_time: float, ev: Dict[str, Any]) -> bool:
        if "startSeconds" in ev and "endSeconds" in ev:
            return ev["startSeconds"] <= pred_time <= ev["endSeconds"]
        return abs(pred_time - ev["timeSeconds"]) <= tolerance + 1e-9

    def distance_to_event(pred_time: float, ev: Dict[str, Any]) -> float:
        if "startSeconds" in ev and "endSeconds" in ev:
            if ev["startSeconds"] <= pred_time <= ev["endSeconds"]:
                return 0.0
            if pred_time < ev["startSeconds"]:
                return ev["startSeconds"] - pred_time
            return pred_time - ev["endSeconds"]
        return abs(pred_time - ev["timeSeconds"])

    def in_coverage(pred_time: float) -> bool:
        if coverage_intervals is None:
            return False
        for cov in coverage_intervals:
            if cov["startSeconds"] <= pred_time <= cov["endSeconds"]:
                return True
        return False

    u_nodes = list(range(len(eval_order)))
    u_to_cand = {u_idx: eval_order[u_idx] for u_idx in u_nodes}
    u_to_time = {u_idx: cand_time_map.get(u_to_cand[u_idx], 0.0) for u_idx in u_nodes}

    # Bipartite matching setup against positive events
    graph: Dict[int, List[str]] = {}
    v_priority: Dict[int, List[str]] = {}

    for u in u_nodes:
        t_p = u_to_time[u]
        compatible: List[Tuple[float, str]] = []
        for ev in positive_events:
            if matches_event(t_p, ev):
                dist = distance_to_event(t_p, ev)
                compatible.append((dist, ev["eventId"]))
        # Deterministic sorting: abs time difference ascending, then eventId
        compatible.sort(key=lambda x: (x[0], x[1]))
        graph[u] = [ev_id for _, ev_id in compatible]
        v_priority[u] = [ev_id for _, ev_id in compatible]

    matching = _max_bipartite_matching(u_nodes, graph, v_priority)
    matched_pos_event_ids: Set[str] = set(matching.values())

    matched_pairs: List[Dict[str, Any]] = []
    for u in u_nodes:
        if u in matching:
            matched_pairs.append({
                "predictionIndex": u_to_cand[u],
                "eventId": matching[u]
            })

    tp = len(matching)
    duplicate_fp = 0
    negative_fp = 0
    coverage_fp = 0
    unknown_predictions = 0

    for u in u_nodes:
        if u in matching:
            continue
        t_p = u_to_time[u]
        # 1. Matches an already-covered positive event?
        matches_any_pos = any(matches_event(t_p, ev) for ev in positive_events)
        if matches_any_pos:
            duplicate_fp += 1
            continue
        # 2. Falls in explicit negative event?
        matches_neg = any(matches_event(t_p, ev) for ev in negative_events)
        if matches_neg:
            negative_fp += 1
            continue
        # 3. Falls in labelCoverageIntervals?
        if in_coverage(t_p):
            coverage_fp += 1
            continue
        # 4. Outside annotated windows/coverage => unknown
        unknown_predictions += 1

    total_known_fp = duplicate_fp + negative_fp + coverage_fp
    known_predictions = tp + total_known_fp
    total_selected = len(eval_order)

    status = "evaluated" if (video_gt is not None and len(video_gt) > 0) else "unmeasured"

    selected_event_precision = None
    if (tp + total_known_fp) > 0:
        selected_event_precision = tp / (tp + total_known_fp)

    selected_event_recall = None
    if len(positive_events) > 0:
        selected_event_recall = tp / len(positive_events)

    coverage_known = None
    if total_selected > 0:
        coverage_known = known_predictions / total_selected

    # Ranked precision@k / recall@k if selectionOrderIndices provided
    ranked_precision_at_k = None
    ranked_recall_at_k = None
    if ranking_available and status == "evaluated":
        # At K = case request count
        ranked_precision_at_k = selected_event_precision
        ranked_recall_at_k = selected_event_recall

    # Segments metrics
    segments = run.get("segments")
    seg_metrics = {
        "segmentCount": len(segments) if segments is not None else 0,
        "totalDurationSeconds": sum((seg["endSeconds"] - seg["startSeconds"]) for seg in segments) if segments is not None else 0.0,
    }

    return {
        "status": status,
        "selectedCount": total_selected,
        "positiveGtCount": len(positive_events),
        "negativeGtCount": len(negative_events),
        "tp": tp,
        "duplicateFp": duplicate_fp,
        "negativeFp": negative_fp,
        "coverageFp": coverage_fp,
        "totalKnownFp": total_known_fp,
        "unknownPredictionCount": unknown_predictions,
        "knownPredictionCount": known_predictions,
        "selectedEventPrecision": selected_event_precision,
        "selectedEventRecall": selected_event_recall,
        "predictionCoverageKnown": coverage_known,
        "rankingAvailable": ranking_available,
        "rankedPrecisionAtK": ranked_precision_at_k,
        "rankedRecallAtK": ranked_recall_at_k,
        "matchedPairs": matched_pairs,
        "segmentMetrics": seg_metrics,
        "elapsedSeconds": run.get("elapsedSeconds"),
    }


def evaluate_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluates a strictly loaded JSON document conforming to the schema.
    Returns report dictionary.
    """
    _validate_root(doc)
    cases = doc["cases"]
    _validate_and_check_leakage(cases)
    _check_run_definition_consistency(cases)

    # Propagate evaluationScope exactly
    eval_scope = "unspecified"
    if "evaluationScope" in doc:
        eval_scope = doc["evaluationScope"]
    elif "metadata" in doc and isinstance(doc["metadata"], dict) and "evaluationScope" in doc["metadata"]:
        eval_scope = str(doc["metadata"]["evaluationScope"])

    # Aggregates structures
    # Keys: (split, mode, runId, pairDecisionScope)
    split_mode_run_groups: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

    case_results: List[Dict[str, Any]] = []

    calibration_cases_count = sum(1 for c in cases if c["split"] == "calibration")
    heldout_cases_count = sum(1 for c in cases if c["split"] == "heldOut")

    for c in cases:
        case_id = c["caseId"]
        mode = c["mode"]
        split = c["split"]

        c_res: Dict[str, Any] = {
            "caseId": case_id,
            "split": split,
            "mode": mode,
            "runEvaluations": {}
        }

        for run in c.get("runs", []):
            run_id = run["runId"]
            ver = run["version"]
            var = run["featureVariant"]
            pair_scope = run.get("pairDecisionScope", "unspecified")

            group_key = (split, mode, run_id, pair_scope)
            if group_key not in split_mode_run_groups:
                split_mode_run_groups[group_key] = {
                    "split": split,
                    "mode": mode,
                    "runId": run_id,
                    "version": ver,
                    "featureVariant": var,
                    "pairDecisionScope": pair_scope,
                    "caseCount": 0,
                    # Photo aggregations
                    "photo": {
                        "humanGtPairCount": 0,
                        "tp": 0,
                        "fp": 0,
                        "tn": 0,
                        "fn": 0,
                        "unknownGtCount": 0,
                        "missingPredictionCount": 0,
                        "evaluableLabeledPairs": 0,
                        "labeledPairsWithPred": 0,
                        "repPrefEvaluable": 0,
                        "repPrefWins": 0,
                        "repPrefLosses": 0,
                    },
                    # Video aggregations
                    "video": {
                        "positiveGtCount": 0,
                        "negativeGtCount": 0,
                        "tp": 0,
                        "duplicateFp": 0,
                        "negativeFp": 0,
                        "coverageFp": 0,
                        "totalKnownFp": 0,
                        "unknownPredictionCount": 0,
                        "knownPredictionCount": 0,
                        "totalSelected": 0,
                        "rankingAvailableCount": 0,
                    },
                    # Elapsed aggregations
                    "elapsed": {
                        "measuredElapsedCount": 0,
                        "totalSeconds": 0.0,
                    }
                }
                if pair_scope == "unspecified":
                    split_mode_run_groups[group_key]["fairnessNote"] = "unverified fairness: pairDecisionScope is unspecified"

            group = split_mode_run_groups[group_key]
            group["caseCount"] += 1

            if mode == "photo":
                res = _evaluate_photo_case_run(c, run)
                res["pairDecisionScope"] = pair_scope
                if pair_scope == "unspecified":
                    res["fairnessNote"] = "unverified fairness: pairDecisionScope is unspecified"
                c_res["runEvaluations"][run_id] = res

                pm = res["pairMetrics"]
                group["photo"]["humanGtPairCount"] += pm["humanGtPairCount"]
                group["photo"]["tp"] += pm["tp"]
                group["photo"]["fp"] += pm["fp"]
                group["photo"]["tn"] += pm["tn"]
                group["photo"]["fn"] += pm["fn"]
                group["photo"]["unknownGtCount"] += pm["unknownGtCount"]
                group["photo"]["missingPredictionCount"] += pm["missingPredictionCount"]
                evaluable_pairs = (pm["tp"] + pm["fp"] + pm["tn"] + pm["fn"]) + pm["missingPredictionCount"]
                group["photo"]["evaluableLabeledPairs"] += evaluable_pairs
                group["photo"]["labeledPairsWithPred"] += (pm["tp"] + pm["fp"] + pm["tn"] + pm["fn"])

                rpm = res["representativePrefMetrics"]
                group["photo"]["repPrefEvaluable"] += rpm["evaluablePrefs"]
                group["photo"]["repPrefWins"] += rpm["wins"]
                group["photo"]["repPrefLosses"] += rpm["losses"]

            else:  # video
                res = _evaluate_video_case_run(c, run)
                c_res["runEvaluations"][run_id] = res

                group["video"]["positiveGtCount"] += res["positiveGtCount"]
                group["video"]["negativeGtCount"] += res["negativeGtCount"]
                group["video"]["tp"] += res["tp"]
                group["video"]["duplicateFp"] += res["duplicateFp"]
                group["video"]["negativeFp"] += res["negativeFp"]
                group["video"]["coverageFp"] += res["coverageFp"]
                group["video"]["totalKnownFp"] += res["totalKnownFp"]
                group["video"]["unknownPredictionCount"] += res["unknownPredictionCount"]
                group["video"]["knownPredictionCount"] += res["knownPredictionCount"]
                group["video"]["totalSelected"] += res["selectedCount"]
                if res["rankingAvailable"]:
                    group["video"]["rankingAvailableCount"] += 1

            elapsed = run.get("elapsedSeconds")
            if elapsed is not None:
                group["elapsed"]["measuredElapsedCount"] += 1
                group["elapsed"]["totalSeconds"] += float(elapsed)

        case_results.append(c_res)

    # Compute micro aggregates for each split/mode/runId/pairDecisionScope group
    aggregated_summaries: List[Dict[str, Any]] = []
    for key in sorted(split_mode_run_groups.keys()):
        group = split_mode_run_groups[key]
        split = group["split"]
        mode = group["mode"]

        summary: Dict[str, Any] = {
            "split": split,
            "mode": mode,
            "runId": group["runId"],
            "version": group["version"],
            "featureVariant": group["featureVariant"],
            "pairDecisionScope": group["pairDecisionScope"],
            "caseCount": group["caseCount"],
        }
        if "fairnessNote" in group:
            summary["fairnessNote"] = group["fairnessNote"]

        # Elapsed
        el = group["elapsed"]
        summary["elapsedSeconds"] = {
            "measuredCount": el["measuredElapsedCount"],
            "totalSeconds": el["totalSeconds"] if el["measuredElapsedCount"] > 0 else 0.0,
            "meanSeconds": (el["totalSeconds"] / el["measuredElapsedCount"]) if el["measuredElapsedCount"] > 0 else None,
        }

        if mode == "photo":
            p = group["photo"]
            tp = p["tp"]
            fp = p["fp"]
            tn = p["tn"]
            fn = p["fn"]

            precision = (tp / (tp + fp)) if (tp + fp) > 0 else None
            recall = (tp / (tp + fn)) if (tp + fn) > 0 else None
            f1 = None
            if precision is not None and recall is not None and (precision + recall) > 0:
                f1 = 2.0 * precision * recall / (precision + recall)

            pair_cov = (p["labeledPairsWithPred"] / p["evaluableLabeledPairs"]) if p["evaluableLabeledPairs"] > 0 else None

            rep_pref_win_rate = (p["repPrefWins"] / p["repPrefEvaluable"]) if p["repPrefEvaluable"] > 0 else None

            summary["photoMetrics"] = {
                "humanGtPairCount": p["humanGtPairCount"],
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "pairCoverage": pair_cov,
                "unknownGtCount": p["unknownGtCount"],
                "missingPredictionCount": p["missingPredictionCount"],
                "representativePref": {
                    "evaluableCount": p["repPrefEvaluable"],
                    "wins": p["repPrefWins"],
                    "losses": p["repPrefLosses"],
                    "winRate": rep_pref_win_rate,
                }
            }
        else:  # video
            v = group["video"]
            tp = v["tp"]
            total_fp = v["totalKnownFp"]
            pos_gt = v["positiveGtCount"]
            known_preds = v["knownPredictionCount"]
            total_sel = v["totalSelected"]

            sel_precision = (tp / (tp + total_fp)) if (tp + total_fp) > 0 else None
            sel_recall = (tp / pos_gt) if pos_gt > 0 else None
            coverage_known = (known_preds / total_sel) if total_sel > 0 else None

            summary["videoMetrics"] = {
                "positiveGtCount": pos_gt,
                "negativeGtCount": v["negativeGtCount"],
                "tp": tp,
                "duplicateFp": v["duplicateFp"],
                "negativeFp": v["negativeFp"],
                "coverageFp": v["coverageFp"],
                "totalKnownFp": total_fp,
                "unknownPredictionCount": v["unknownPredictionCount"],
                "knownPredictionCount": known_preds,
                "totalSelectedCount": total_sel,
                "selectedEventPrecision": sel_precision,
                "selectedEventRecall": sel_recall,
                "predictionCoverageKnown": coverage_known,
                "rankingAvailableCaseCount": v["rankingAvailableCount"],
                "note": "selected-event precision/recall measures count-limited selections; ranking-dependent @K metrics require explicit selectionOrderIndices."
            }

        aggregated_summaries.append(summary)

    report: Dict[str, Any] = {
        "evaluationScope": eval_scope,
        "provenance": "caller-reported actual core provenance",
        "datasetAudit": {
            "calibrationCasesCount": calibration_cases_count,
            "heldOutCasesCount": heldout_cases_count,
            "totalCasesCount": len(cases),
            "leakageCheckPassed": True,
            "leakageCheckDetails": "Verified zero sourceId and sessionId overlap between calibration and heldOut splits."
        },
        "metricDefinitions": {
            "photoPairMetrics": "Matched strictly by unordered pair to human ground truth photoPairGT. Unknown GT labels and missing predictions excluded from precision/recall/F1 and tracked separately.",
            "representativePrefs": "Scored as win if preferred candidate equals group representative and other candidate is member of same group; loss if reversed; unmeasured otherwise.",
            "videoEventMetrics": "One-to-one maximum-cardinality bipartite matching predictions to positive events with tolerance/interval. Duplicate positive matches count as duplicateFP. Predictions in explicit negative events or coverage intervals count as FP. Outside windows are unknown.",
            "rankingNote": "Video selectedFrameIndices is core chronological order; ranking precision@K unavailable without explicit selectionOrderIndices."
        },
        "limitations": {
            "scopeHonesty": "Metrics are conditioned on candidate excerpts provided in the dataset and do not reflect unmeasured full-source footage unless verified by full-source ground truth.",
            "provenanceAssertion": "Provenance actualCore is accepted as caller assertion, not independently verified by evaluator.",
            "eventCanonicalization": "Input positive events must be canonicalized by source/event identity; maximum matching does not infer physical event identity."
        },
        "aggregates": aggregated_summaries,
        "caseEvaluations": case_results,
    }

    return report


def _check_path_identity(input_path: str, output_path: str) -> None:
    try:
        in_real = os.path.realpath(input_path)
        out_real = os.path.realpath(output_path)
        if in_real == out_real:
            sys.stderr.write("Error: Output path cannot refer to the input file or its aliases.\n")
            sys.exit(1)
        if os.path.exists(output_path):
            try:
                if os.path.samefile(input_path, output_path):
                    sys.stderr.write("Error: Output path cannot refer to the input file or its aliases.\n")
                    sys.exit(1)
            except OSError:
                sys.stderr.write("Error: Output path cannot refer to the input file or its aliases.\n")
                sys.exit(1)
    except SystemExit:
        raise
    except Exception:
        sys.stderr.write("Error: Output path cannot refer to the input file or its aliases.\n")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Numeric-only evaluation harness for HanAI media selector.")
    parser.add_argument("--input", required=True, help="Input JSON dataset file path.")
    parser.add_argument("--output", required=True, help="Output JSON report file path.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing output file.")
    args = parser.parse_args()

    input_path = args.input
    output_path = args.output

    if not os.path.exists(input_path):
        sys.stderr.write("Error: Input file does not exist.\n")
        sys.exit(1)

    _check_path_identity(input_path, output_path)

    if os.path.exists(output_path) and not args.overwrite:
        sys.stderr.write("Error: Output file already exists and --overwrite not specified.\n")
        sys.exit(1)

    try:
        with open(input_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception:
        sys.stderr.write("Error: Failed to read input file.\n")
        sys.exit(1)

    try:
        doc = _parse_strict_json(content)
        report = evaluate_document(doc)
        report_json = json.dumps(report, indent=2, allow_nan=False, sort_keys=True)
    except ValidationError:
        sys.stderr.write("Validation Error: Schema validation failed.\n")
        sys.exit(2)
    except Exception:
        sys.stderr.write("Evaluation Error: Evaluation processing failed.\n")
        sys.exit(3)

    # Safe atomic write in the same folder as output_path
    output_dir = os.path.dirname(os.path.abspath(output_path)) or "."
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=output_dir, delete=False, encoding="utf-8") as tf:
            tf.write(report_json)
            temp_name = tf.name

        if args.overwrite:
            _check_path_identity(input_path, output_path)
            os.replace(temp_name, output_path)
            temp_name = None
        else:
            # Exclusively create without replacing any existing file
            linked = False
            try:
                os.link(temp_name, output_path)
                linked = True
            except FileExistsError:
                sys.stderr.write("Error: Output file already exists.\n")
                sys.exit(1)
            except OSError:
                linked = False

            if not linked:
                fd = None
                try:
                    fd = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    with os.fdopen(fd, "w", encoding="utf-8") as out_f:
                        fd = None
                        out_f.write(report_json)
                except FileExistsError:
                    sys.stderr.write("Error: Output file already exists.\n")
                    sys.exit(1)
                except OSError:
                    sys.stderr.write("Error: Failed to write output file.\n")
                    sys.exit(1)
                finally:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
    except SystemExit:
        raise
    except Exception:
        sys.stderr.write("Error: Failed to write output file.\n")
        sys.exit(1)
    finally:
        if temp_name is not None and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


if __name__ == "__main__":
    main()
