#!/usr/bin/env python3
"""Local, label-free evaluation-feature extractor for HanAI clip manifests.

Reads a clip manifest (schemaVersion 1: clipId/sessionId/sourceId/timeSeconds/
path) and emits per-frame/per-clip numeric features (quality proxies, fixed
text-prompt match scores, and adjacent-frame temporal-distance proxies) to a
new local JSON file. This is a standalone evaluation tool: it does not
select, rank, or train anything, and it never reads ground-truth labels,
annotations, or media directory listings - only the manifest path and the
media files the manifest itself names.

Reuses sibling analyze_media.py (ffmpeg/ffprobe decode/probe helpers, basic
descriptor) and learned_features.py (pinned local DINOv2/SigLIP2 adapters) as
black-box APIs; neither sibling file is modified by this script.
"""

import argparse
import importlib.util
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

MAX_CLIPS_HARD_LIMIT = 10000
MAX_MANIFEST_BYTES = 20 * 1024 * 1024
FRAME_CHOICES = (3, 5)
TOOL_ID = "hanai-local-evaluation-features-v1"

PROMPTS = (
    (0, "a person hitting a golf ball with a full golf swing"),
    (1, "a person putting a golf ball with a putter"),
    (2, "a person walking or standing without playing a shot"),
    (3, "an empty indoor golf hitting bay"),
    (4, "a person preparing to hit a golf ball"),
)

ROI_X0, ROI_X1 = 0.25, 0.75
ROI_Y0, ROI_Y1 = 0.40, 0.95

FEATURE_SEMANTICS = {
    "qualityScore": "sharpness * exposureScore; a non-validated technical-quality proxy, not an aesthetics model.",
    "promptCosines": "cosine similarity between a frame's L2-normalized SigLIP2 image embedding and each fixed prompt's text embedding, range [-1, 1].",
    "promptLogits": "cosine * exp(logit_scale) + logit_bias from the pinned SigLIP2 model; an uncalibrated raw logit.",
    "promptScores": "sigmoid(promptLogit); a text-match score in [0, 1], NOT a calibrated probability and NOT an importance/selection score.",
    "dinoTemporalDistance": "1 - dot(L2-normalized DINOv2 CLS embeddings) between adjacent successfully sampled frames, clamped to [0, 2].",
    "basicTemporalDistance": "tile/histogram perceptual descriptor distance (analyze_media.descriptor_distance) between adjacent successfully sampled frames, range [0, 1].",
    "grayDifference": "mean absolute normalized-luma difference over the full padding-excluded content region between adjacent successfully sampled frames, range [0, 1].",
    "roiDifference": "mean absolute normalized-luma difference over a fixed central-lower region of interest (x 0.25-0.75, y 0.40-0.95 of the content region) between adjacent successfully sampled frames, range [0, 1].",
    "timestampStatus": "'measured' if ffmpeg reported a decodable, in-range source-relative pts for the frame; otherwise 'unknown' and actualSeconds is null.",
    "timeSeconds (clip)": "the manifest's original parent-source time offset, passed through as metadata only; not a capture timestamp and not used for seeking.",
}

MODEL_SCORE_SEMANTICS = {
    "promptLogits": "raw uncalibrated SigLIP2 logit (cosine * exp(logit_scale) + logit_bias)",
    "promptScores": "sigmoid(logit); a text-match score, NOT a calibrated probability and NOT a learned importance/selection weight",
    "note": "No model here was trained or fine-tuned on these clips; scores are fixed pretrained-model outputs only.",
}


class EvalError(Exception):
    """Carries only a fixed error code; never raw exception text or paths."""


def _load_sibling(filename):
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        raise EvalError("sibling_module_missing")
    module_name = "hanai_eval_" + filename.replace(".py", "")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise EvalError("sibling_module_missing")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise EvalError("sibling_module_load_failed")
    return module


def safe_sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def mean_or_none(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)


def max_or_none(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return max(vals)


def min_or_none(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return min(vals)


def mean_per_index(list_of_lists, count):
    return [mean_or_none([row[k] for row in list_of_lists]) for k in range(count)]


def max_per_index(list_of_lists, count):
    return [max_or_none([row[k] for row in list_of_lists]) for k in range(count)]


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def luma_grid(raw, box, canvas, luma_fn):
    x0, y0, w, h = box
    vals = []
    for yy in range(y0, y0 + h):
        row = (yy * canvas) * 3
        for xx in range(x0, x0 + w):
            off = row + xx * 3
            vals.append(luma_fn(raw[off], raw[off + 1], raw[off + 2]) / 255.0)
    return vals


def roi_box_within(box):
    x0, y0, w, h = box
    rx0 = x0 + int(round(ROI_X0 * w))
    rx1 = x0 + int(round(ROI_X1 * w))
    ry0 = y0 + int(round(ROI_Y0 * h))
    ry1 = y0 + int(round(ROI_Y1 * h))
    rx1 = min(max(rx1, rx0 + 1), x0 + w)
    ry1 = min(max(ry1, ry0 + 1), y0 + h)
    return rx0, ry0, max(1, rx1 - rx0), max(1, ry1 - ry0)


def mean_abs_diff(a, b):
    if not a or not b or len(a) != len(b):
        return None
    total = sum(abs(x - y) for x, y in zip(a, b))
    return max(0.0, min(1.0, total / len(a)))


def load_models(lf, models_root):
    if not os.path.isdir(models_root):
        raise EvalError("models_root_invalid")
    dino_dir = os.path.join(models_root, "dinov2")
    siglip_dir = os.path.join(models_root, "siglip2")
    try:
        dino = lf.LearnedFeatures(backend="dinov2", model_dir=dino_dir, query=None)
    except Exception:
        raise EvalError("dino_model_init_failed")
    try:
        siglip = lf.LearnedFeatures(backend="siglip2", model_dir=siglip_dir, query=None)
    except Exception:
        raise EvalError("siglip_model_init_failed")
    return dino, siglip, siglip_dir


def build_text_embeddings(lf, siglip, siglip_model_dir):
    try:
        tokenizer = siglip.transformers.AutoTokenizer.from_pretrained(
            siglip_model_dir,
            local_files_only=True,
            trust_remote_code=False,
        )
    except Exception:
        raise EvalError("siglip_tokenizer_load_failed")

    max_pos = getattr(
        getattr(siglip.model.config, "text_config", None),
        "max_position_embeddings",
        64,
    )
    texts = [text for _, text in PROMPTS]
    try:
        text_inputs = tokenizer(
            text=texts,
            padding="max_length",
            truncation=True,
            max_length=max_pos,
            return_tensors="pt",
        )
        with siglip.torch.inference_mode():
            text_features = siglip.model.get_text_features(**text_inputs)
            text_tensor = lf._extract_pooler_or_tensor(text_features)
            if not siglip.torch.isfinite(text_tensor).all():
                raise EvalError("siglip_text_embedding_invalid")
            if bool((text_tensor == 0).all()):
                raise EvalError("siglip_text_embedding_invalid")
            text_tensor = siglip.torch.nn.functional.normalize(text_tensor, p=2, dim=-1)
        text_vectors = text_tensor.tolist()
    except EvalError:
        raise
    except Exception:
        raise EvalError("siglip_text_embedding_failed")

    if len(text_vectors) != len(PROMPTS):
        raise EvalError("siglip_text_embedding_invalid")

    try:
        logit_scale = float(siglip.model.logit_scale.exp().item())
        logit_bias = float(siglip.model.logit_bias.item())
    except Exception:
        raise EvalError("siglip_logit_params_invalid")
    if not (math.isfinite(logit_scale) and math.isfinite(logit_bias)):
        raise EvalError("siglip_logit_params_invalid")

    return text_vectors, logit_scale, logit_bias


def validate_manifest(data):
    if not isinstance(data, dict):
        raise EvalError("manifest_invalid")
    if data.get("schemaVersion") != 1:
        raise EvalError("manifest_schema_unsupported")
    clips = data.get("clips")
    if not isinstance(clips, list):
        raise EvalError("manifest_invalid")
    if len(clips) > MAX_CLIPS_HARD_LIMIT:
        raise EvalError("manifest_too_large")
    return clips


def validate_clip_entry(entry):
    if not isinstance(entry, dict):
        raise EvalError("clip_invalid")
    clip_id = entry.get("clipId")
    session_id = entry.get("sessionId")
    source_id = entry.get("sourceId")
    time_seconds = entry.get("timeSeconds")
    path = entry.get("path")
    if not isinstance(clip_id, int) or isinstance(clip_id, bool):
        raise EvalError("clip_invalid")
    if not isinstance(session_id, str) or not session_id:
        raise EvalError("clip_invalid")
    if not isinstance(source_id, str) or not source_id:
        raise EvalError("clip_invalid")
    try:
        time_seconds_f = float(time_seconds)
    except (TypeError, ValueError):
        raise EvalError("clip_invalid")
    if not math.isfinite(time_seconds_f):
        raise EvalError("clip_invalid")
    if not isinstance(path, str) or not path:
        raise EvalError("clip_invalid")
    return clip_id, session_id, source_id, time_seconds_f, path


def sample_frame(am, ffmpeg, path, width, height, rotation, box, roi_box, duration,
                  start_time, planned, dino, siglip, text_vectors, logit_scale, logit_bias):
    raw64, raw_pts = am.decode_canvas_with_pts(ffmpeg, path, rotation, planned)
    actual = None
    status = "unknown"
    if raw_pts is not None:
        normalized = raw_pts - start_time
        if 0.0 <= normalized < duration:
            actual = normalized
            status = "measured"

    sharpness, exposure, tiles, hist = am.compute_descriptor(raw64, box)
    quality = max(0.0, min(1.0, sharpness * exposure))

    full_luma = luma_grid(raw64, box, am.CANVAS, am.luma)
    roi_luma = luma_grid(raw64, roi_box, am.CANVAS, am.luma)

    learned_w, learned_h = am.content_dims(width, height, rotation, am.LEARNED_CANVAS)
    raw512 = am.decode_exact(ffmpeg, path, rotation, learned_w, learned_h, seek_seconds=planned)

    dino_vector, _ = dino.encode(raw512, learned_w, learned_h)
    siglip_vector, _ = siglip.encode(raw512, learned_w, learned_h)

    cosines = []
    logits = []
    scores = []
    for text_vec in text_vectors:
        c = max(-1.0, min(1.0, dot(siglip_vector, text_vec)))
        logit = c * logit_scale + logit_bias
        if not math.isfinite(logit):
            raise EvalError("prompt_score_invalid")
        score = max(0.0, min(1.0, safe_sigmoid(logit)))
        cosines.append(c)
        logits.append(logit)
        scores.append(score)

    frame_out = {
        "plannedSeconds": planned,
        "actualSeconds": actual,
        "timestampStatus": status,
        "qualityScore": quality,
        "sharpness": max(0.0, min(1.0, sharpness)),
        "exposureScore": max(0.0, min(1.0, exposure)),
        "promptCosines": cosines,
        "promptLogits": logits,
        "promptScores": scores,
    }
    internal = {
        "basic": (tiles, hist),
        "dino": dino_vector,
        "fullLuma": full_luma,
        "roiLuma": roi_luma,
    }
    return frame_out, internal


def build_pairs(am, successes):
    pairs = []
    for a, b in zip(successes, successes[1:]):
        idx_a, internal_a = a
        idx_b, internal_b = b
        dino_distance = am.embedding_distance(internal_a["dino"], internal_b["dino"])
        basic_distance = am.descriptor_distance(internal_a["basic"], internal_b["basic"])
        gray_difference = mean_abs_diff(internal_a["fullLuma"], internal_b["fullLuma"])
        roi_difference = mean_abs_diff(internal_a["roiLuma"], internal_b["roiLuma"])
        pairs.append(
            {
                "firstFrameIndex": idx_a,
                "secondFrameIndex": idx_b,
                "dinoDistance": dino_distance,
                "basicDistance": basic_distance,
                "grayDifference": gray_difference,
                "roiDifference": roi_difference,
            }
        )
    return pairs


def build_aggregate(frames_ok, pairs):
    n_prompts = len(PROMPTS)
    if not frames_ok:
        return {
            "qualityMean": None,
            "qualityMin": None,
            "qualityMax": None,
            "promptCosineMean": [None] * n_prompts,
            "promptCosineMax": [None] * n_prompts,
            "promptLogitMean": [None] * n_prompts,
            "promptLogitMax": [None] * n_prompts,
            "promptScoreMean": [None] * n_prompts,
            "promptScoreMax": [None] * n_prompts,
            "dinoTemporalDistanceMean": None,
            "dinoTemporalDistanceMax": None,
            "grayDifferenceMean": None,
            "grayDifferenceMax": None,
            "roiDifferenceMean": None,
            "roiDifferenceMax": None,
            "basicTemporalDistanceMean": None,
            "basicTemporalDistanceMax": None,
        }
    qualities = [f["qualityScore"] for f in frames_ok]
    cosines = [f["promptCosines"] for f in frames_ok]
    logits = [f["promptLogits"] for f in frames_ok]
    scores = [f["promptScores"] for f in frames_ok]
    dino_dists = [p["dinoDistance"] for p in pairs]
    basic_dists = [p["basicDistance"] for p in pairs]
    gray_diffs = [p["grayDifference"] for p in pairs]
    roi_diffs = [p["roiDifference"] for p in pairs]
    return {
        "qualityMean": mean_or_none(qualities),
        "qualityMin": min_or_none(qualities),
        "qualityMax": max_or_none(qualities),
        "promptCosineMean": mean_per_index(cosines, n_prompts),
        "promptCosineMax": max_per_index(cosines, n_prompts),
        "promptLogitMean": mean_per_index(logits, n_prompts),
        "promptLogitMax": max_per_index(logits, n_prompts),
        "promptScoreMean": mean_per_index(scores, n_prompts),
        "promptScoreMax": max_per_index(scores, n_prompts),
        "dinoTemporalDistanceMean": mean_or_none(dino_dists),
        "dinoTemporalDistanceMax": max_or_none(dino_dists),
        "grayDifferenceMean": mean_or_none(gray_diffs),
        "grayDifferenceMax": max_or_none(gray_diffs),
        "roiDifferenceMean": mean_or_none(roi_diffs),
        "roiDifferenceMax": max_or_none(roi_diffs),
        "basicTemporalDistanceMean": mean_or_none(basic_dists),
        "basicTemporalDistanceMax": max_or_none(basic_dists),
    }


def process_clip(am, ffmpeg, ffprobe, dino, siglip, text_vectors, logit_scale, logit_bias,
                  frames_requested, clip_id, session_id, source_id, time_seconds, path):
    failures = []
    frames_out = []
    internals = []

    try:
        am.validate_local_media_path(path)
        width, height, duration, rotation, start_time = am.probe_video(ffprobe, path)
    except am.SanitizedError as exc:
        return {
            "clipId": clip_id,
            "sessionId": session_id,
            "sourceId": source_id,
            "timeSeconds": time_seconds,
            "status": "error",
            "frames": [],
            "aggregate": build_aggregate([], []),
            "pairs": [],
            "failures": [{"code": str(exc)}],
        }

    box = am.content_box(width, height, rotation)
    roi_box = roi_box_within(box)
    planned_times = [duration * (i + 1) / (frames_requested + 1) for i in range(frames_requested)]

    for frame_index, planned in enumerate(planned_times):
        try:
            frame_out, internal = sample_frame(
                am, ffmpeg, path, width, height, rotation, box, roi_box, duration,
                start_time, planned, dino, siglip, text_vectors, logit_scale, logit_bias,
            )
            frame_out["frameIndex"] = frame_index
            frames_out.append(frame_out)
            internals.append((frame_index, internal))
        except am.SanitizedError as exc:
            failures.append({"frameIndex": frame_index, "code": str(exc)})
        except EvalError as exc:
            failures.append({"frameIndex": frame_index, "code": str(exc)})
        except Exception:
            failures.append({"frameIndex": frame_index, "code": "frame_processing_failed"})

    pairs = build_pairs(am, internals)
    frames_ok = frames_out
    succeeded = len(frames_ok)
    if succeeded == frames_requested:
        status = "ok"
    elif succeeded > 0:
        status = "partial"
    else:
        status = "error"

    return {
        "clipId": clip_id,
        "sessionId": session_id,
        "sourceId": source_id,
        "timeSeconds": time_seconds,
        "status": status,
        "frames": frames_out,
        "aggregate": build_aggregate(frames_ok, pairs),
        "pairs": pairs,
        "failures": failures,
    }


def resolve_output_path(output_arg, overwrite, forbidden_paths):
    output_path = os.path.abspath(output_arg)
    for forbidden in forbidden_paths:
        if not forbidden:
            continue
        forbidden_abs = os.path.abspath(forbidden)
        if forbidden_abs == output_path:
            raise EvalError("output_conflicts_with_input")
        if os.path.isdir(forbidden_abs):
            common = os.path.commonpath([forbidden_abs, output_path])
            if common == forbidden_abs:
                raise EvalError("output_conflicts_with_input")
    parent = os.path.dirname(output_path)
    if not os.path.isdir(parent):
        raise EvalError("output_parent_missing")
    if os.path.exists(output_path) and not overwrite:
        raise EvalError("output_exists")
    return output_path


def read_manifest(manifest_path):
    if not os.path.isfile(manifest_path):
        raise EvalError("manifest_read_failed")
    try:
        size = os.path.getsize(manifest_path)
    except OSError:
        raise EvalError("manifest_read_failed")
    if size <= 0 or size > MAX_MANIFEST_BYTES:
        raise EvalError("manifest_too_large")
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        raise EvalError("manifest_read_failed")
    return data


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--models-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-clips", type=int, default=None)
    parser.add_argument("--frames", type=int, choices=FRAME_CHOICES, default=3)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.max_clips is not None and args.max_clips <= 0:
        print(json.dumps({"status": "error", "error": "invalid_max_clips"}))
        return 1

    try:
        am = _load_sibling("analyze_media.py")
        lf = _load_sibling("learned_features.py")

        manifest_data = read_manifest(args.manifest)
        clips_raw = validate_manifest(manifest_data)
        if args.max_clips is not None:
            clips_raw = clips_raw[: args.max_clips]

        source_paths = []
        validated_clips = []
        for index, entry in enumerate(clips_raw):
            try:
                clip_id, session_id, source_id, time_seconds, path = validate_clip_entry(entry)
            except EvalError as exc:
                validated_clips.append((index, None, str(exc)))
                continue
            source_paths.append(path)
            validated_clips.append((index, (clip_id, session_id, source_id, time_seconds, path), None))

        models_root = os.path.abspath(args.models_root)
        forbidden = [args.manifest, models_root] + source_paths
        output_path = resolve_output_path(args.output, args.overwrite, forbidden)

        ffmpeg = am.find_tool("ffmpeg", None)
        ffprobe = am.find_tool("ffprobe", None)

        dino, siglip, siglip_dir = load_models(lf, models_root)
        text_vectors, logit_scale, logit_bias = build_text_embeddings(lf, siglip, siglip_dir)

        clips_output = []
        requested = len(validated_clips)
        succeeded = 0
        partial = 0
        failed = 0

        for index, parsed, error_code in validated_clips:
            if parsed is None:
                clips_output.append(
                    {
                        "clipId": None,
                        "sessionId": None,
                        "sourceId": None,
                        "timeSeconds": None,
                        "status": "error",
                        "frames": [],
                        "aggregate": build_aggregate([], []),
                        "pairs": [],
                        "failures": [{"code": error_code}],
                    }
                )
                failed += 1
                continue
            clip_id, session_id, source_id, time_seconds, path = parsed
            clip_result = process_clip(
                am, ffmpeg, ffprobe, dino, siglip, text_vectors, logit_scale, logit_bias,
                args.frames, clip_id, session_id, source_id, time_seconds, path,
            )
            clips_output.append(clip_result)
            if clip_result["status"] == "ok":
                succeeded += 1
            elif clip_result["status"] == "partial":
                partial += 1
            else:
                failed += 1

        output_doc = {
            "schemaVersion": 1,
            "tool": TOOL_ID,
            "prompts": [{"promptIndex": idx, "text": text} for idx, text in PROMPTS],
            "modelNamespaces": {
                "dinov2": dino.namespace,
                "siglip2": siglip.namespace,
            },
            "featureSemantics": FEATURE_SEMANTICS,
            "modelScoreSemantics": MODEL_SCORE_SEMANTICS,
            "counts": {
                "requested": requested,
                "succeeded": succeeded,
                "partial": partial,
                "failed": failed,
            },
            "clips": clips_output,
        }

        mode = "w" if args.overwrite else "x"
        try:
            with open(output_path, mode, encoding="utf-8") as f:
                json.dump(output_doc, f, allow_nan=False)
        except FileExistsError:
            raise EvalError("output_exists")
        except Exception:
            raise EvalError("output_write_failed")

    except EvalError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "requested": requested,
                "succeeded": succeeded,
                "partial": partial,
                "failed": failed,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
