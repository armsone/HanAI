#!/usr/bin/env python3
"""Extract temporal features from video clips using X-CLIP and ffmpeg/ffprobe.

Standalone evaluation tool: scores exactly 8 or 32 ordered frames per clip against a
fixed set of 5 action prompts using temporal_features.TemporalFeatures.
Operates label-free on manifests without reading annotations or ground truth.

Output filesystem requirement: parent directory of the output file must support
same-directory hardlinks (os.link) for atomic publishing.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time

from analyze_media import (
    SanitizedArgumentParser,
    SanitizedError,
    decode_exact,
    find_tool,
    probe_video,
    validate_local_media_path,
)
from extract_evaluation_features import PROMPTS
from temporal_features import TemporalFeatures

SCHEMA_VERSION = 1
TOOL_ID = "hanai-extract-temporal-features-v1"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024  # 2 MiB
MAX_CLIPS = 1000
MAX_PIXELS_BOUND = 16_000_000
NUM_FRAMES = 8
PROMPT_COUNT = 5

ALLOWED_CLIP_KEYS = frozenset(
    {"clipId", "sessionId", "sourceId", "timeSeconds", "path"}
)

FEATURE_SEMANTICS = (
    "softmax compares only the fixed 5 relative prompts given in one call; "
    "not a calibrated action confidence or personal-importance score."
)
TIMESTAMP_SEMANTICS = (
    "requested seeks only; actual decoded PTS unknown"
)


def _emit_error_and_exit(code: str, status_code: int = 1) -> None:
    doc = {"status": "error", "error": code}
    sys.stderr.write(json.dumps(doc) + "\n")
    sys.exit(status_code)


def _resolve_output_path(
    output_arg: str, forbidden_paths: list[str]
) -> str:
    if not isinstance(output_arg, str) or not output_arg.strip():
        _emit_error_and_exit("invalid_arguments", 2)

    output_path = os.path.abspath(output_arg)
    parent = os.path.dirname(output_path)
    if not os.path.isdir(parent):
        _emit_error_and_exit("output_parent_missing", 1)

    if os.path.lexists(output_path):
        _emit_error_and_exit("output_exists", 1)

    target_real = os.path.realpath(output_path)
    target_parent_real = os.path.realpath(parent)

    for forbidden in forbidden_paths:
        if not forbidden:
            continue
        try:
            forb_real = os.path.realpath(forbidden)
            if forb_real == target_real:
                _emit_error_and_exit("output_conflicts_with_input", 1)

            if os.path.isdir(forb_real):
                try:
                    if os.path.commonpath([forb_real, target_real]) == forb_real:
                        _emit_error_and_exit("output_conflicts_with_input", 1)
                except ValueError:
                    pass

                try:
                    if os.path.commonpath([forb_real, target_parent_real]) == forb_real:
                        _emit_error_and_exit("output_conflicts_with_input", 1)
                except ValueError:
                    pass

            if os.path.exists(forb_real) and os.path.exists(output_path):
                if os.path.samefile(forb_real, output_path):
                    _emit_error_and_exit("output_conflicts_with_input", 1)
        except OSError:
            continue

    return output_path


def _read_manifest(manifest_path: str) -> dict:
    try:
        st = os.lstat(manifest_path)
    except OSError:
        _emit_error_and_exit("manifest_read_failed", 1)

    if os.path.islink(manifest_path) or not os.path.isfile(manifest_path):
        _emit_error_and_exit("manifest_read_failed", 1)

    if st.st_size <= 0 or st.st_size > MAX_MANIFEST_BYTES:
        _emit_error_and_exit("manifest_too_large", 1)

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        _emit_error_and_exit("manifest_read_failed", 1)

    if not isinstance(data, dict):
        _emit_error_and_exit("manifest_invalid", 1)
    return data


def _validate_manifest_structure(
    manifest_data: dict, session_filter: str | None
) -> tuple[list[dict], list[str]]:
    if manifest_data.get("schemaVersion") != SCHEMA_VERSION:
        _emit_error_and_exit("manifest_schema_unsupported", 1)

    clips = manifest_data.get("clips")
    if not isinstance(clips, list):
        _emit_error_and_exit("manifest_invalid", 1)

    if len(clips) > MAX_CLIPS:
        _emit_error_and_exit("manifest_too_large", 1)

    seen_clip_ids = set()
    validated_clips: list[dict] = []
    source_paths: list[str] = []
    found_session = False

    for entry in clips:
        if not isinstance(entry, dict):
            _emit_error_and_exit("clip_invalid", 1)

        # Reject unexpected entry keys (ensures label/pose-free contract)
        if not set(entry.keys()).issubset(ALLOWED_CLIP_KEYS):
            _emit_error_and_exit("clip_invalid", 1)

        clip_id = entry.get("clipId")
        session_id = entry.get("sessionId")
        source_id = entry.get("sourceId")
        time_seconds = entry.get("timeSeconds")
        path = entry.get("path")

        if (
            not isinstance(clip_id, int)
            or isinstance(clip_id, bool)
            or clip_id < 0
        ):
            _emit_error_and_exit("clip_invalid", 1)

        if clip_id in seen_clip_ids:
            _emit_error_and_exit("duplicate_clip_id", 1)
        seen_clip_ids.add(clip_id)

        if not isinstance(session_id, str) or not session_id or len(session_id) > 512:
            _emit_error_and_exit("clip_invalid", 1)

        if not isinstance(source_id, str) or not source_id or len(source_id) > 512:
            _emit_error_and_exit("clip_invalid", 1)

        if (
            not isinstance(time_seconds, (int, float))
            or isinstance(time_seconds, bool)
        ):
            _emit_error_and_exit("clip_invalid", 1)

        time_seconds_f = float(time_seconds)
        if not math.isfinite(time_seconds_f) or time_seconds_f < 0:
            _emit_error_and_exit("clip_invalid", 1)

        if not isinstance(path, str) or not path or len(path) > 4096:
            _emit_error_and_exit("clip_invalid", 1)

        try:
            validate_local_media_path(path)
        except SanitizedError:
            _emit_error_and_exit("invalid_media_path", 1)

        source_paths.append(path)

        if session_filter is not None:
            if session_id == session_filter:
                found_session = True
                validated_clips.append(
                    {
                        "clipId": clip_id,
                        "sessionId": session_id,
                        "sourceId": source_id,
                        "timeSeconds": time_seconds_f,
                        "path": path,
                    }
                )
        else:
            validated_clips.append(
                {
                    "clipId": clip_id,
                    "sessionId": session_id,
                    "sourceId": source_id,
                    "timeSeconds": time_seconds_f,
                    "path": path,
                }
            )

    if session_filter is not None and not found_session:
        _emit_error_and_exit("session_not_found", 1)

    return validated_clips, source_paths


def _calculate_sample_times(
    duration: float, window_seconds: float | None, num_frames: int = NUM_FRAMES
) -> tuple[float, list[float]]:
    center = duration / 2.0
    effective_window = min(window_seconds, duration) if window_seconds is not None else duration
    start = center - (effective_window / 2.0)
    step = effective_window / float(num_frames)
    requested = [start + step * (i + 0.5) for i in range(num_frames)]
    return effective_window, requested


def _process_single_clip(
    clip: dict,
    model: TemporalFeatures,
    prompt_texts: list[str],
    ffmpeg: str,
    ffprobe: str,
    window_seconds: float | None,
    num_frames: int = NUM_FRAMES,
) -> dict:
    clip_id = clip["clipId"]
    session_id = clip["sessionId"]
    source_id = clip["sourceId"]
    time_seconds = clip["timeSeconds"]
    path = clip["path"]

    base_res = {
        "clipId": clip_id,
        "sessionId": session_id,
        "sourceId": source_id,
        "timeSeconds": time_seconds,
    }

    pil_frames: list = []
    t0_decode = time.perf_counter()
    try:
        try:
            try:
                validate_local_media_path(path)
            except SanitizedError:
                return {**base_res, "status": "failed", "error": "invalid_media_path"}

            try:
                w, h, duration, rotation, _ = probe_video(ffprobe, path)
            except SanitizedError:
                return {**base_res, "status": "failed", "error": "probe_failed"}

            if rotation in (90, 270):
                orient_w, orient_h = h, w
            else:
                orient_w, orient_h = w, h

            if orient_w * orient_h > MAX_PIXELS_BOUND:
                return {**base_res, "status": "failed", "error": "pixel_limit"}

            effective_window, requested_seconds = _calculate_sample_times(
                duration, window_seconds, num_frames=num_frames
            )

            try:
                from PIL import Image

                for sec in requested_seconds:
                    raw_bytes = decode_exact(
                        ffmpeg, path, rotation, orient_w, orient_h, seek_seconds=sec
                    )
                    img = Image.frombytes("RGB", (orient_w, orient_h), raw_bytes)
                    pil_frames.append(img)
            except SanitizedError:
                return {**base_res, "status": "failed", "error": "decode_failed"}
            except Exception:
                return {**base_res, "status": "failed", "error": "decode_failed"}
        finally:
            decode_duration = time.perf_counter() - t0_decode

        t0_inf = time.perf_counter()
        try:
            try:
                out = model.classify(pil_frames, prompt_texts)
            except Exception:
                return {**base_res, "status": "failed", "error": "inference_failed"}

            logits = out.get("logits")
            probabilities = out.get("probabilities")
            model_meta = out.get("model")

            if (
                not isinstance(logits, list)
                or len(logits) != PROMPT_COUNT
                or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in logits)
                or not isinstance(probabilities, list)
                or len(probabilities) != PROMPT_COUNT
                or not all(
                    isinstance(p, (int, float)) and math.isfinite(p) and 0.0 <= p <= 1.0
                    for p in probabilities
                )
                or not isinstance(model_meta, dict)
            ):
                return {**base_res, "status": "failed", "error": "invalid_model_output"}
        finally:
            inference_duration = time.perf_counter() - t0_inf
    finally:
        for f in pil_frames:
            try:
                f.close()
            except Exception:
                pass
        pil_frames.clear()

    return {
        **base_res,
        "status": "ok",
        "durationSeconds": duration,
        "windowSeconds": effective_window,
        "requestedSeconds": requested_seconds,
        "actualSeconds": [None] * num_frames,
        "timestampStatus": "unknown",
        "logits": logits,
        "probabilities": probabilities,
        "model": model_meta,
        "decodeSeconds": decode_duration,
        "inferenceSeconds": inference_duration,
    }


def _write_atomic_output(output_path: str, doc: dict) -> None:
    parent = os.path.dirname(output_path)
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=parent, prefix=".extract_temporal_", suffix=".tmp"
        )
    except Exception:
        _emit_error_and_exit("output_write_failed", 1)

    try:
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, allow_nan=False, indent=2)
                f.write("\n")
        except Exception:
            _emit_error_and_exit("output_write_failed", 1)

        try:
            os.link(tmp_path, output_path)
        except FileExistsError:
            _emit_error_and_exit("output_exists", 1)
        except OSError:
            _emit_error_and_exit("output_atomic_publish_failed", 1)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def main(argv: list[str]) -> int:
    start_total = time.perf_counter()

    parser = SanitizedArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--session", default=None)
    parser.add_argument("--window-seconds", type=float, default=None)
    parser.add_argument("--ffmpeg", default=None)
    parser.add_argument("--ffprobe", default=None)

    args = parser.parse_args(argv)

    if args.window_seconds is not None:
        if not math.isfinite(args.window_seconds) or args.window_seconds <= 0:
            _emit_error_and_exit("invalid_arguments", 2)

    # 1. Entire manifest validation happens BEFORE model/media access
    manifest_data = _read_manifest(args.manifest)
    validated_clips, source_paths = _validate_manifest_structure(
        manifest_data, args.session
    )

    # 2. Check output path conflicts with manifest, model-dir, and input media
    forbidden = [args.manifest, args.model_dir] + source_paths
    output_path = _resolve_output_path(args.output, forbidden)

    # 3. Resolve tools
    try:
        ffmpeg_bin = find_tool("ffmpeg", args.ffmpeg)
        ffprobe_bin = find_tool("ffprobe", args.ffprobe)
    except SanitizedError as exc:
        _emit_error_and_exit(str(exc), 1)

    # 4. Initialize model once at runtime
    t0_model = time.perf_counter()
    try:
        model = TemporalFeatures(model_dir=args.model_dir)
    except Exception:
        _emit_error_and_exit("model_init_failed", 1)
    model_init_duration = time.perf_counter() - t0_model

    num_frames = getattr(model, "num_frames", NUM_FRAMES)
    if (
        isinstance(num_frames, bool)
        or not isinstance(num_frames, int)
        or num_frames not in (8, 32)
    ):
        _emit_error_and_exit("model_init_failed", 1)

    # Fixed 5 prompts validation and transform
    if len(PROMPTS) != PROMPT_COUNT:
        _emit_error_and_exit("prompts_invalid", 1)
    prompt_texts = [p[1] for p in PROMPTS]
    root_prompts = [{"promptIndex": p[0], "text": p[1]} for p in PROMPTS]

    # 5. Process clips
    results = []
    succeeded_count = 0
    failed_count = 0

    for clip in validated_clips:
        clip_res = _process_single_clip(
            clip,
            model,
            prompt_texts,
            ffmpeg_bin,
            ffprobe_bin,
            args.window_seconds,
            num_frames=num_frames,
        )
        results.append(clip_res)
        if clip_res["status"] == "ok":
            succeeded_count += 1
        else:
            failed_count += 1

    total_elapsed = time.perf_counter() - start_total

    output_doc = {
        "schemaVersion": SCHEMA_VERSION,
        "tool": TOOL_ID,
        "numFrames": num_frames,
        "prompts": root_prompts,
        "featureSemantics": FEATURE_SEMANTICS,
        "timestampSemantics": TIMESTAMP_SEMANTICS,
        "windowSecondsRequested": args.window_seconds,
        "counts": {
            "requested": len(validated_clips),
            "succeeded": succeeded_count,
            "failed": failed_count,
        },
        "clips": results,
        "modelInitSeconds": model_init_duration,
        "elapsedSeconds": total_elapsed,
    }

    _write_atomic_output(output_path, output_doc)

    report_status = "ok" if failed_count == 0 else ("partial" if succeeded_count > 0 else "failed")
    summary = {
        "status": report_status,
        "requested": len(validated_clips),
        "succeeded": succeeded_count,
        "failed": failed_count,
    }
    sys.stdout.write(json.dumps(summary) + "\n")

    return 0 if succeeded_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
