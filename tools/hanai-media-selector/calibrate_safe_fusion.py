#!/usr/bin/env python3
"""Conservative local re-ranking calibration for HanAI expert video scores.

Families (both keep the expert order as the backbone):
  promote         - a row whose full-window AND short-window XCLIP scores are both
                    at or above training quantile q may move up at most M places,
                    never into the protected top, never past a cumulative expert
                    gap larger than g * gapScale, never past another agreeing row.
  promote_demote  - promote, plus a row whose full AND short scores are both at or
                    below training quantile 1-q may move down under the same limits.

Static SigLIP is only checked for row identity; it is not used for scoring.
Thresholds and gapScale come from training blocks only.

Displacement: every swap checks both rows against the base expert order, so no
row is ever (mid-way or finally) more than maxShift positions from its expert
rank. Protected head rows are never swapped. Rows with equal expert scores are
never swapped, so expert ties keep the original stable order.

Missing evidence: if any row of the list being ranked has a missing/non-finite
full or short semantic score, or any training threshold/gapScale is missing or
non-finite, the WHOLE list is returned in pure expert order (no partial fusion).
Rows without an expert score rank last in stable order in both baseline and
fused lists and are never swapped with any row.

Protection: "abs" protects the same absolute count in every list; "frac" protects
ceil(frac * n) of the actual list size (5 of 40 for validation, 20 of 160 refit).
Both are in the fixed grid and the actual count is recorded per evaluation.

Usage: calibrate_safe_fusion.py STATIC.json XCLIP_FULL.json XCLIP_SHORT.json OUT.json
"""
import json
import math
import os
import statistics
import sys

SCORE_NAMESPACE = "historical-experimental-expert-score (approx -.057..+.164; not a probability, not app 0.7 scale)"
MIN_MEAN_AP_GAIN = 0.005
QUANTILES = (0.9, 0.75)
PROTECTS = (("abs", 5), ("frac", 0.125), ("abs", 3))
MAX_SHIFTS = (3, 6)
GAP_MULTS = (1.0, 2.0)
FAMILIES = ("promote", "promote_demote")
SELECTION_RULE = ("among passing configs: max mean block AP gain (rounded 1e-9); "
                  "ties -> lowest config index (grid ordered conservative-first)")


class ToolError(Exception):
    pass


def fixed_configs():
    out = []
    for family in FAMILIES:
        for q in QUANTILES:
            for mode, value in PROTECTS:
                for m in MAX_SHIFTS:
                    for g in GAP_MULTS:
                        out.append({"index": len(out), "family": family, "quantile": q,
                                    "protectMode": mode, "protectValue": value,
                                    "maxShift": m, "gapMult": g})
    return out


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def load_rows(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data["rows"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ToolError("input unreadable or malformed")
    if not isinstance(rows, list) or data.get("expertHigherIsBetter") is not True:
        raise ToolError("input schema mismatch")
    for r in rows:
        if not isinstance(r, dict) or not (r.get("expertScore") is None or finite(r.get("expertScore"))) \
                or not isinstance(r.get("positive"), bool) or "id" not in r or "groupId" not in r:
            raise ToolError("row schema mismatch")
    return rows


def merge_inputs(static_rows, full_rows, short_rows):
    if not (len(static_rows) == len(full_rows) == len(short_rows)) or not static_rows:
        raise ToolError("row count mismatch")
    merged = []
    for i, (a, b, c) in enumerate(zip(static_rows, full_rows, short_rows)):
        for k in ("id", "positive", "groupId", "expertScore"):
            if not (a[k] == b[k] == c[k]):
                raise ToolError("row identity/label/group/expert mismatch")
        merged.append({"order": i, "positive": a["positive"], "group": a["groupId"],
                       "expert": -math.inf if a["expertScore"] is None else float(a["expertScore"]),
                       "full": b.get("semanticScore") if finite(b.get("semanticScore")) else None,
                       "short": c.get("semanticScore") if finite(c.get("semanticScore")) else None})
    if len({r["id"] for r in static_rows}) != len(static_rows):
        raise ToolError("duplicate ids")
    return merged


def quantile(values, q):
    v = sorted(values)
    if not v:
        return None
    pos = q * (len(v) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def expert_order(rows):
    return sorted(rows, key=lambda r: (-r["expert"], r["order"]))


def train_stats(rows, q):
    """Thresholds and within-block adjacent-gap scale from the given (training) rows."""
    full = [r["full"] for r in rows if r["full"] is not None]
    short = [r["short"] for r in rows if r["short"] is not None]
    gaps = []
    for g in sorted({r["group"] for r in rows}):
        ranked = expert_order([r for r in rows if r["group"] == g])
        gaps += [ranked[i]["expert"] - ranked[i + 1]["expert"] for i in range(len(ranked) - 1)]
    pos_gaps = [x for x in gaps if 0 < x < math.inf]
    return {"rows": len(rows), "blocks": len({r["group"] for r in rows}),
            "fullHigh": quantile(full, q), "fullLow": quantile(full, 1 - q),
            "shortHigh": quantile(short, q), "shortLow": quantile(short, 1 - q),
            "gapScale": statistics.median(pos_gaps) if pos_gaps else 0.0,
            "semanticCoverage": min(len(full), len(short)) / len(rows)}


def protect_count(config, n):
    if config["protectMode"] == "abs":
        return min(n, int(config["protectValue"]))
    return min(n, int(math.ceil(config["protectValue"] * n - 1e-12)))


def apply_frozen(frozen, rows):
    """Reusable: rank rows ({expert, full, short}, optional order) with a frozen config.

    `frozen` is {"family": "expert_only"} or needs family, protectMode,
    protectValue, maxShift, gapMult and stats {fullHigh, fullLow, shortHigh,
    shortLow, gapScale}. Returns rows in final order. Any missing/non-finite
    full/short score in `rows`, or incomplete stats, returns pure expert order.
    """
    rows = [dict(r, order=r.get("order", i), expert=float(r["expert"]) if finite(r.get("expert")) else -math.inf)
            for i, r in enumerate(rows)]
    ranked = expert_order(rows)
    if frozen.get("family") == "expert_only":
        return ranked
    st = frozen["stats"]
    keys = ("fullHigh", "fullLow", "shortHigh", "shortLow", "gapScale")
    if not all(finite(st.get(k)) for k in keys) or not finite(frozen.get("gapMult")) \
            or not all(finite(r.get("full")) and finite(r.get("short")) for r in ranked):
        return ranked
    n = len(ranked)
    protect = protect_count(frozen, n)
    limit = frozen["gapMult"] * st["gapScale"]
    max_shift = int(frozen["maxShift"])
    if limit <= 0 or max_shift <= 0:
        return ranked
    base = {id(r): i for i, r in enumerate(ranked)}

    def can_swap(hi, lo, pos):
        """Swap ranked[pos]=hi (moves down) with ranked[pos+1]=lo (moves up)?"""
        if pos < protect or hi["expert"] == -math.inf or lo["expert"] == -math.inf:
            return False
        if hi["expert"] == lo["expert"] or hi["expert"] - lo["expert"] > limit:
            return False
        return abs(pos + 1 - base[id(hi)]) <= max_shift and abs(pos - base[id(lo)]) <= max_shift

    up = {id(r) for r in ranked if r["full"] >= st["fullHigh"] and r["short"] >= st["shortHigh"]}
    down = set()
    if frozen["family"] == "promote_demote":
        down = {id(r) for r in ranked if r["full"] <= st["fullLow"]
                and r["short"] <= st["shortLow"]} - up
    for item in [r for r in ranked if id(r) in up]:
        pos = ranked.index(item)
        steps = 0
        while steps < max_shift and pos - 1 >= protect:
            above = ranked[pos - 1]
            if id(above) in up or not can_swap(above, item, pos - 1):
                break
            ranked[pos - 1], ranked[pos] = item, above
            pos -= 1
            steps += 1
    for item in reversed([r for r in ranked if id(r) in down]):
        pos = ranked.index(item)
        steps = 0
        while steps < max_shift and pos + 1 < n:
            below = ranked[pos + 1]
            if id(below) in down or id(below) in up or not can_swap(item, below, pos):
                break
            ranked[pos + 1], ranked[pos] = item, below
            pos += 1
            steps += 1
    return ranked


def metrics(ranked):
    labels = [r["positive"] for r in ranked]
    total = sum(labels)
    hits, ap = 0, 0.0
    for i, lab in enumerate(labels, 1):
        if lab:
            hits += 1
            ap += hits / i
    return {"ap": ap / total if total else 0.0,
            "p10": sum(labels[:10]) / min(10, len(labels)),
            "p20": sum(labels[:20]) / min(20, len(labels))}


def moved(ranked):
    base = expert_order(ranked)
    return sum(1 for a, b in zip(base, ranked) if a is not b)


def contract(config, ranked):
    """Max displacement vs expert rank, protected head kept, expert ties kept in stable order."""
    base = expert_order(ranked)
    where = {id(r): i for i, r in enumerate(ranked)}
    shift = max((abs(where[id(r)] - i) for i, r in enumerate(base)), default=0)
    head = 0 if config.get("family") == "expert_only" else protect_count(config, len(ranked))
    ties = all(where[id(a)] < where[id(b)] for i, a in enumerate(base) for b in base[i + 1:]
               if a["expert"] == b["expert"])
    return {"maxDisplacement": shift, "headPreserved": all(a is b for a, b in zip(base[:head], ranked[:head])),
            "tieOrderPreserved": ties}


def rnd(x):
    return None if x is None else round(x, 6)


def evaluate(config, rows, blocks):
    folds, gains, ok = [], [], True
    reasons = []
    for held in blocks:
        train = [r for r in rows if r["group"] != held]
        val = [r for r in rows if r["group"] == held]
        stats = train_stats(train, config["quantile"])
        fused = apply_frozen(dict(config, stats=stats), val)
        base_m, fused_m = metrics(expert_order(val)), metrics(fused)
        p_ok = fused_m["p10"] >= base_m["p10"] - 1e-12 and fused_m["p20"] >= base_m["p20"] - 1e-12
        if not p_ok:
            ok = False
            reasons.append("fold%d P@10/P@20 below expert" % (len(folds) + 1))
        gains.append(fused_m["ap"] - base_m["ap"])
        folds.append({"fold": len(folds) + 1, "trainRows": len(train), "validationRows": len(val),
                      "validationPositives": sum(r["positive"] for r in val),
                      "protectCount": protect_count(config, len(val)), "movedRows": moved(fused),
                      "contract": contract(config, fused),
                      "trainStats": {k: rnd(v) if isinstance(v, float) else v for k, v in stats.items()},
                      "expert": {k: rnd(v) for k, v in base_m.items()},
                      "fused": {k: rnd(v) for k, v in fused_m.items()},
                      "precisionPreserved": p_ok})
    mean_gain = sum(gains) / len(gains)
    if mean_gain < MIN_MEAN_AP_GAIN - 1e-12:
        ok = False
        reasons.append("mean block AP gain below %.3f" % MIN_MEAN_AP_GAIN)
    stats = train_stats(rows, config["quantile"])
    refit = apply_frozen(dict(config, stats=stats), rows)
    base_r, fused_r = metrics(expert_order(rows)), metrics(refit)
    r_ok = fused_r["p10"] >= base_r["p10"] - 1e-12 and fused_r["p20"] >= base_r["p20"] - 1e-12
    if not r_ok:
        ok = False
        reasons.append("refit P@10/P@20 below expert")
    return {"config": config, "passed": ok, "failReasons": reasons, "folds": folds,
            "meanBlockApExpert": rnd(sum(f["expert"]["ap"] for f in folds) / len(folds)),
            "meanBlockApFused": rnd(sum(f["fused"]["ap"] for f in folds) / len(folds)),
            "meanBlockApGain": round(mean_gain, 9),
            "refit": {"rows": len(rows), "protectCount": protect_count(config, len(rows)),
                      "movedRows": moved(refit), "contract": contract(config, refit),
                      "stats": {k: rnd(v) if isinstance(v, float) else v for k, v in stats.items()},
                      "expert": {k: rnd(v) for k, v in base_r.items()},
                      "fused": {k: rnd(v) for k, v in fused_r.items()},
                      "precisionPreserved": r_ok}}


def check_output(out, inputs):
    name = os.path.basename(out)
    if not (name.startswith("safe-calibration") and name.endswith(".json")):
        raise ToolError("output name must be safe-calibration*.json")
    parent = os.path.realpath(os.path.dirname(os.path.abspath(out)))
    if parent != os.path.realpath(os.path.dirname(os.path.abspath(inputs[0]))):
        raise ToolError("output must sit beside the calibration inputs")
    if os.path.lexists(out):
        raise ToolError("output already exists")
    lowered = name.lower()
    if any(e.lower() == lowered for e in os.listdir(parent)):
        raise ToolError("output aliases an existing file")
    target = os.path.join(parent, name)
    if any(os.path.realpath(p) == target for p in inputs):
        raise ToolError("output aliases an input")
    return target


def write_new(target, payload):
    tmp = target + ".tmp-%d" % os.getpid()
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp, target)  # fails if target appeared meanwhile; never overwrites
    except FileExistsError:
        raise ToolError("output already exists")
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def main(argv):
    if len(argv) != 5:
        print("usage: calibrate_safe_fusion.py STATIC FULL SHORT OUT", file=sys.stderr)
        return 2
    inputs, out = argv[1:4], argv[4]
    try:
        target = check_output(out, inputs)
        rows = merge_inputs(*(load_rows(p) for p in inputs))
        blocks = sorted({r["group"] for r in rows})
        if len(blocks) != 4:
            raise ToolError("expected exactly 4 time blocks")
        configs = fixed_configs()
        if len(configs) > 48:
            raise ToolError("grid exceeds 48")
        results = [evaluate(c, rows, blocks) for c in configs]
        passing = [r for r in results if r["passed"]]
        if passing:
            best = sorted(passing, key=lambda r: (-round(r["meanBlockApGain"], 9), r["config"]["index"]))[0]
            # Full float precision: these stats are what apply_frozen consumes on reuse.
            selected = dict(best["config"], stats=train_stats(rows, best["config"]["quantile"]))
            decision = {"method": "safe_fusion", "configIndex": best["config"]["index"],
                        "reason": "passed all gates; " + SELECTION_RULE, "frozen": selected}
            reference = apply_frozen(selected, rows)
        else:
            counts = {}
            for r in results:
                for reason in r["failReasons"]:
                    key = reason.split(" ", 1)[1] if reason.startswith("fold") else reason
                    counts[key] = counts.get(key, 0) + 1
            best_gain = max(r["meanBlockApGain"] for r in results)
            decision = {"method": "expert_only", "configIndex": None, "frozen": {"family": "expert_only"},
                        "reason": "no config passed all gates; expert-only fixed",
                        "failureReasonCounts": counts, "bestMeanBlockApGainAnyConfig": best_gain}
            reference = expert_order(rows)
        reapplied = apply_frozen(json.loads(json.dumps(decision["frozen"])), rows)
        decision["frozenReapplyCheck"] = {
            "method": "frozen config JSON round-trip re-applied to the 160 calibration rows",
            "matchesFullCalibrationRefit": [r["order"] for r in reapplied] == [r["order"] for r in reference],
            "contract": contract(decision["frozen"], reapplied)}
        if decision["frozenReapplyCheck"]["matchesFullCalibrationRefit"] is not True:
            raise ToolError("frozen re-application differs from refit")
        checks = [(r["config"]["maxShift"], c) for r in results
                  for c in [f["contract"] for f in r["folds"]] + [r["refit"]["contract"]]]
        contract_summary = {
            "evaluatedLists": len(checks),
            "maxDisplacementVsExpertRank": max(c["maxDisplacement"] for _, c in checks),
            "everyListWithinMaxShift": all(c["maxDisplacement"] <= m for m, c in checks),
            "everyProtectedHeadPreserved": all(c["headPreserved"] for _, c in checks),
            "everyExpertTieOrderPreserved": all(c["tieOrderPreserved"] for _, c in checks)}
        payload = {
            "schemaVersion": 2, "tool": "calibrate_safe_fusion",
            "scoreNamespace": SCORE_NAMESPACE,
            "semanticEvidence": {"used": ["xclip-full-window", "xclip-short-window"],
                                 "staticSiglip": "identity check only, not scored"},
            "rows": len(rows), "positives": sum(r["positive"] for r in rows),
            "blocks": len(blocks), "blockSizes": sorted(sum(1 for r in rows if r["group"] == b) for b in blocks),
            "missingSemantic": {"full": sum(r["full"] is None for r in rows),
                                "short": sum(r["short"] is None for r in rows)},
            "missingExpert": sum(r["expert"] == -math.inf for r in rows),
            "gates": {"minMeanBlockApGain": MIN_MEAN_AP_GAIN,
                      "everyFoldP10P20NotBelowExpert": True, "refitP10P20NotBelowExpert": True,
                      "evaluation": "per-block leave-one-block-out; no pooled OOF ranking"},
            "selectionRule": SELECTION_RULE,
            "protectionDesign": "abs: same count in 40-row validation and 160-row refit lists; "
                                "frac: ceil(frac*n) of actual list size (fold and refit differ)",
            "gapDesign": "gapScale = median positive adjacent expert gap within each training block (score units); "
                         "moves blocked when cumulative expert gap exceeds gapMult*gapScale",
            "tieRule": "expert descending, then original stable row order",
            "displacementRule": "every swap checks both rows: no row is ever more than maxShift positions "
                                "from its base expert rank (mid-way or final); protected head rows never swap; "
                                "equal expert scores never swap",
            "missingRule": "if any row in the list being ranked has a missing/non-finite full or short semantic "
                           "score, or any training threshold/gapScale is missing/non-finite, the whole list is "
                           "returned in pure expert order; training thresholds are quantiles of the finite "
                           "training scores; rows with missing expert score rank last in stable order for both "
                           "baseline and fused and are never swapped with any row",
            "statsPrecision": "decision.frozen.stats are full float precision (used for reuse); "
                              "per-fold/refit stats and metrics in results are rounded to 6 places for display only",
            "contractSummary": contract_summary,
            "candidateCount": len(configs), "passingCount": len(passing),
            "configs": configs, "decision": decision, "results": results,
            "limitations": [
                "160 rows, 31 positives, 4 blocks: gates are noisy and can pass/fail by one item",
                "refit list is 160 rows; position-based maxShift covers a smaller score range than in 40-row folds",
                "frac protection of 160 rows protects 20, so refit P@10 cannot change for those configs",
                "semantic thresholds assume XCLIP score scale is stable across sessions",
                "scores are historical experimental values, not app scores; do not map to probabilities"],
        }
        write_new(target, payload)
    except ToolError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, TypeError, KeyError, ValueError, AttributeError):
        print("error: input/output unreadable, malformed or not writable", file=sys.stderr)
        return 1
    print("candidates=%d passing=%d selected=%s" % (len(configs), len(passing), decision["method"]))
    if passing:
        print("meanBlockApGain=%.4f" % best["meanBlockApGain"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
