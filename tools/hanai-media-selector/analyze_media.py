#!/usr/bin/env python3
"""Portable local media feature extraction (standard library + ffmpeg/ffprobe).

Emits JSON on stdout describing simple, unvalidated numeric proxies
(sharpness/exposure/perceptual distance) for images or video frames.
Does not perform selection, ranking, or scene/semantic analysis -
that is the responsibility of the Swift/Kotlin cores that consume this
output, or of the optional external JVM selection engine bridged via
--engine-home.

Optional embedding backends (--embedding-backend dinov2|siglip2) load a
local, user-installed model through the sibling learned_features module
(not owned by this file) and never download anything implicitly.
"""

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys

SCHEMA = "hanai-media-selector/1"
DESCRIPTOR_NAMESPACE = "perceptual-basic-v1"
CANVAS = 64
LEARNED_CANVAS = 512
TILE_GRID = 8
HIST_BINS = 8
MAX_IMAGES = 200
SUBPROCESS_TIMEOUT = 20
MAX_IMAGE_BYTES = CANVAS * CANVAS * 3
REFINE_OFFSETS = [-0.5, -0.4, -0.3, -0.2, -0.1, 0.1, 0.2, 0.3, 0.4, 0.5]
LEARNED_BACKENDS = ("dinov2", "siglip2")
ENGINE_MAIN_CLASS = "com.hanai.cli.MediaSelectionCliKt"
ENGINE_SUBPROCESS_TIMEOUT = 60


class SanitizedError(Exception):
    """Carries only a fixed error code, never raw subprocess text or paths."""


class SanitizedArgumentParser(argparse.ArgumentParser):
    """Never echoes supplied argument values (paths/queries) on parse errors."""

    def error(self, message):
        sys.stderr.write(json.dumps(error_result("invalid_arguments")) + "\n")
        self.exit(2)


def validate_local_media_path(path):
    """Rejects anything that is not an existing local regular file (including
    a symlink resolving to one). URLs, directories, and other non-regular
    paths never reach ffprobe/ffmpeg."""
    if not isinstance(path, str) or not path or "://" in path:
        raise SanitizedError("local_input_required")
    try:
        if not os.path.isfile(path):
            raise SanitizedError("local_input_required")
    except OSError:
        raise SanitizedError("local_input_required")
    return path


def find_tool(name, override):
    if override:
        return override
    found = shutil.which(name)
    if not found:
        raise SanitizedError("%s_not_found" % name)
    return found


def run_subprocess(args, timeout=SUBPROCESS_TIMEOUT):
    try:
        proc = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SanitizedError("subprocess_timeout")
    except OSError:
        raise SanitizedError("subprocess_launch_failed")
    if proc.returncode != 0:
        raise SanitizedError("subprocess_failed")
    return proc.stdout, proc.stderr


def ffprobe_json(ffprobe, path, extra_args, include_format=False):
    args = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,duration,rotation,start_time",
        "-show_entries",
        "stream_tags=rotate",
        "-show_entries",
        "stream_side_data_list",
        "-protocol_whitelist",
        "file,pipe",
    ]
    if include_format:
        args += ["-show_entries", "format=start_time"]
    args += extra_args + [path]
    stdout, _ = run_subprocess(args)
    try:
        data = json.loads(stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SanitizedError("probe_json_invalid")
    streams = data.get("streams") or []
    if not streams:
        raise SanitizedError("no_video_stream")
    if include_format:
        return streams[0], (data.get("format") or {})
    return streams[0]


def finite_positive(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return None
    return f


def finite_number(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def finite_unit_interval(value):
    f = finite_number(value)
    if f is None or f < 0.0 or f > 1.0:
        return None
    return f


def extract_rotation(stream):
    # tags.rotate's sign convention relative to this file's internal
    # CW-positive rotation_filter() is unverified against real codec
    # evidence; its existing (unflipped) handling is preserved as-is.
    rotation = stream.get("rotation")
    if rotation is None:
        tags = stream.get("tags") or {}
        rotation = tags.get("rotate")
    side_rotation = None
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            side_rotation = side.get("rotation")
    if side_rotation is not None:
        # ffprobe's side_data display matrix rotation is counter-clockwise
        # positive; negate to match this file's CW-positive convention,
        # and prefer it over tags.rotate/legacy rotation when present.
        try:
            rotation = -float(side_rotation)
        except (TypeError, ValueError):
            side_rotation = None
    try:
        rotation = int(float(rotation)) % 360
    except (TypeError, ValueError):
        return 0
    # Only axis-aligned rotation is supported; anything else is ignored
    # (documented limitation, see README).
    if rotation in (90, -90, 270, -270):
        return 90 if rotation in (90, -270) else 270
    if rotation in (180, -180):
        return 180
    return 0


def probe_image(ffprobe, path):
    stream = ffprobe_json(ffprobe, path, [])
    width = stream.get("width")
    height = stream.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise SanitizedError("invalid_dimensions")
    if width <= 0 or height <= 0:
        raise SanitizedError("invalid_dimensions")
    rotation = extract_rotation(stream)
    return width, height, rotation


def probe_video(ffprobe, path):
    stream, fmt = ffprobe_json(ffprobe, path, [], include_format=True)
    width = stream.get("width")
    height = stream.get("height")
    duration = finite_positive(stream.get("duration"))
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise SanitizedError("invalid_dimensions")
    if duration is None:
        raise SanitizedError("invalid_duration")
    rotation = extract_rotation(stream)
    # Prefer the stream's own start_time (PTS offset); fall back to the
    # container/format start_time. If neither is present, pts_time values
    # are treated as already source-relative (documented limitation).
    start_time = finite_number(stream.get("start_time"))
    if start_time is None:
        start_time = finite_number(fmt.get("start_time"))
    if start_time is None:
        start_time = 0.0
    return width, height, duration, rotation, start_time


def rotation_filter(rotation):
    if rotation == 90:
        return "transpose=1,"
    if rotation == 270:
        return "transpose=2,"
    if rotation == 180:
        return "hflip,vflip,"
    return ""


def content_dims(src_w, src_h, rotation, canvas):
    if rotation in (90, 270):
        src_w, src_h = src_h, src_w
    scale = min(canvas / src_w, canvas / src_h)
    content_w = min(canvas, max(1, round(src_w * scale)))
    content_h = min(canvas, max(1, round(src_h * scale)))
    return content_w, content_h


def content_box(src_w, src_h, rotation):
    content_w, content_h = content_dims(src_w, src_h, rotation, CANVAS)
    x0 = (CANVAS - content_w) // 2
    y0 = (CANVAS - content_h) // 2
    # Safety margin to compensate for ffmpeg scale/pad rounding differences.
    if content_w > 2 and content_h > 2:
        x0 += 1
        y0 += 1
        content_w -= 2
        content_h -= 2
    return x0, y0, content_w, content_h


def decode_canvas(ffmpeg, path, rotation, seek_seconds=None, extra_args=None):
    vf = (
        rotation_filter(rotation)
        + "scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,format=rgb24"
        % (CANVAS, CANVAS, CANVAS, CANVAS)
    )
    args = [ffmpeg, "-nostdin", "-loglevel", "error", "-noautorotate"]
    if seek_seconds is not None:
        args += ["-ss", "%.3f" % seek_seconds]
    args += ["-protocol_whitelist", "file,pipe", "-i", path]
    if extra_args:
        args += extra_args
    args += ["-frames:v", "1", "-vf", vf, "-f", "rawvideo", "pipe:1"]
    stdout, _ = run_subprocess(args)
    if len(stdout) != MAX_IMAGE_BYTES:
        raise SanitizedError("unexpected_output_size")
    return stdout


def decode_canvas_with_pts(ffmpeg, path, rotation, planned_seconds):
    vf = (
        "showinfo,"
        + rotation_filter(rotation)
        + "scale=%d:%d:force_original_aspect_ratio=decrease,"
        "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=black,format=rgb24"
        % (CANVAS, CANVAS, CANVAS, CANVAS)
    )
    args = [
        ffmpeg,
        "-nostdin",
        "-loglevel",
        "info",
        "-noautorotate",
        "-ss",
        "%.3f" % planned_seconds,
        "-copyts",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        path,
        "-frames:v",
        "1",
        "-vf",
        vf,
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    stdout, stderr = run_subprocess(args)
    if len(stdout) != MAX_IMAGE_BYTES:
        raise SanitizedError("unexpected_output_size")
    actual = None
    match = re.search(rb"pts_time:(-?[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?)", stderr)
    if match:
        actual = finite_number(match.group(1))
    return stdout, actual


def decode_exact(ffmpeg, path, rotation, content_w, content_h, seek_seconds=None):
    """Decodes directly from the source at exactly content_w x content_h
    (aspect-preserving, no letterbox padding, no intermediate CANVAS-sized
    upscale/downscale step) for feeding a learned encoder."""
    vf = rotation_filter(rotation) + "scale=%d:%d,format=rgb24" % (content_w, content_h)
    args = [ffmpeg, "-nostdin", "-loglevel", "error", "-noautorotate"]
    if seek_seconds is not None:
        args += ["-ss", "%.3f" % seek_seconds]
    args += ["-protocol_whitelist", "file,pipe", "-i", path, "-frames:v", "1", "-vf", vf, "-f", "rawvideo", "pipe:1"]
    stdout, _ = run_subprocess(args)
    if len(stdout) != content_w * content_h * 3:
        raise SanitizedError("unexpected_output_size")
    return stdout


def luma(r, g, b):
    return 0.299 * r + 0.587 * g + 0.114 * b


def compute_descriptor(raw, box):
    x0, y0, w, h = box
    # Tile grid of average luma, content-region only (padding excluded).
    tiles = []
    for ty in range(TILE_GRID):
        ry0 = y0 + (ty * h) // TILE_GRID
        ry1 = y0 + ((ty + 1) * h) // TILE_GRID
        ry1 = max(ry1, ry0 + 1)
        for tx in range(TILE_GRID):
            rx0 = x0 + (tx * w) // TILE_GRID
            rx1 = x0 + ((tx + 1) * w) // TILE_GRID
            rx1 = max(rx1, rx0 + 1)
            total = 0.0
            count = 0
            for yy in range(ry0, min(ry1, y0 + h)):
                row = (yy * CANVAS) * 3
                for xx in range(rx0, min(rx1, x0 + w)):
                    off = row + xx * 3
                    total += luma(raw[off], raw[off + 1], raw[off + 2])
                    count += 1
            tiles.append((total / count / 255.0) if count else 0.0)

    hist = [0] * (HIST_BINS * 3)
    pixel_count = 0
    luma_values = []
    for yy in range(y0, y0 + h):
        row = (yy * CANVAS) * 3
        for xx in range(x0, x0 + w):
            off = row + xx * 3
            r, g, b = raw[off], raw[off + 1], raw[off + 2]
            luma_values.append(luma(r, g, b))
            pixel_count += 1
            hist[min(HIST_BINS - 1, r * HIST_BINS // 256)] += 1
            hist[HIST_BINS + min(HIST_BINS - 1, g * HIST_BINS // 256)] += 1
            hist[2 * HIST_BINS + min(HIST_BINS - 1, b * HIST_BINS // 256)] += 1
    if pixel_count == 0:
        raise SanitizedError("empty_content_region")
    hist_norm = [count / pixel_count for count in hist]

    # Sharpness: normalized average local luma gradient magnitude.
    grad_total = 0.0
    grad_count = 0
    for yy in range(y0, y0 + h - 1):
        for xx in range(x0, x0 + w - 1):
            l0 = luma_values[(yy - y0) * w + (xx - x0)]
            l_right = luma_values[(yy - y0) * w + (xx - x0 + 1)]
            l_down = luma_values[(yy - y0 + 1) * w + (xx - x0)]
            grad_total += abs(l0 - l_right) + abs(l0 - l_down)
            grad_count += 1
    avg_gradient = (grad_total / grad_count) if grad_count else 0.0
    sharpness = max(0.0, min(1.0, avg_gradient / 64.0))

    # Exposure: crude clipped-pixel penalty (not a validated exposure metric).
    clipped = sum(1 for lv in luma_values if lv < 8 or lv > 247)
    exposure = max(0.0, min(1.0, 1.0 - (clipped / pixel_count)))

    return sharpness, exposure, tiles, hist_norm


def descriptor_distance(a, b):
    tiles_a, hist_a = a
    tiles_b, hist_b = b
    tile_diff = sum(abs(x - y) for x, y in zip(tiles_a, tiles_b)) / len(tiles_a)
    hist_diff = sum(abs(x - y) for x, y in zip(hist_a, hist_b)) / 2.0
    return max(0.0, min(1.0, 0.6 * tile_diff + 0.4 * hist_diff))


def l2_normalize(vector):
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return None
    return [v / norm for v in vector]


def embedding_distance(a, b):
    """1 - dot(L2-normalized a, b), clamped to [0, 2]; None (rejected) on
    missing/non-finite/mismatched-length/zero vectors."""
    if a is None or b is None:
        return None
    if not a or not b or len(a) != len(b):
        return None
    if not all(isinstance(v, (int, float)) and v == v and v not in (float("inf"), float("-inf")) for v in a):
        return None
    if not all(isinstance(v, (int, float)) and v == v and v not in (float("inf"), float("-inf")) for v in b):
        return None
    na = l2_normalize(a)
    nb = l2_normalize(b)
    if na is None or nb is None:
        return None
    dot = sum(x * y for x, y in zip(na, nb))
    if dot != dot:
        return None
    return max(0.0, min(2.0, 1.0 - dot))


def build_distances_generic(items, distance_fn):
    distances = []
    n = len(items)
    for i in range(n):
        if items[i] is None:
            continue
        for j in range(i + 1, n):
            if items[j] is None:
                continue
            d = distance_fn(items[i], items[j])
            if d is None:
                continue
            distances.append({"firstIndex": i, "secondIndex": j, "distance": d})
    return distances


def build_distances(descriptors):
    return build_distances_generic(descriptors, descriptor_distance)


def build_embedding_distances(vectors):
    return build_distances_generic(vectors, embedding_distance)


# --- Optional learned embedding backend (dinov2 / siglip2) -----------------
#
# Loaded only from the sibling learned_features.py module (owned by a
# different task); this file only imports and calls its documented
# contract, never modifies it. No implicit download/upload occurs here;
# the module itself is responsible for local_files_only loading.


def load_learned_features_class():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    module_path = os.path.join(base_dir, "learned_features.py")
    if not os.path.isfile(module_path):
        raise SanitizedError("learned_module_missing")
    spec = importlib.util.spec_from_file_location("hanai_learned_features", module_path)
    if spec is None or spec.loader is None:
        raise SanitizedError("learned_module_missing")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        raise SanitizedError("learned_module_load_failed")
    cls = getattr(module, "LearnedFeatures", None)
    if cls is None:
        raise SanitizedError("learned_module_invalid")
    return cls


def init_learned(args):
    cls = load_learned_features_class()
    try:
        instance = cls(backend=args.embedding_backend, model_dir=args.model_dir, query=args.query)
    except Exception:
        raise SanitizedError("learned_backend_init_failed")
    namespace = getattr(instance, "namespace", None)
    if not isinstance(namespace, str) or not namespace:
        raise SanitizedError("learned_backend_invalid_namespace")
    return instance, namespace


def encode_with_learned(learned, rgb_bytes, width, height):
    try:
        vector, relevance = learned.encode(rgb_bytes, width, height)
    except Exception:
        raise SanitizedError("learned_backend_encode_failed")
    if not isinstance(vector, list) or not vector:
        raise SanitizedError("learned_backend_invalid_vector")
    for v in vector:
        if not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            raise SanitizedError("learned_backend_invalid_vector")
    if relevance is not None:
        relevance = finite_number(relevance)
    return vector, relevance


def ranking_scope(args, relevance):
    if args.embedding_backend == "siglip2" and args.query and relevance is not None:
        return "text-guided-relevance"
    return "technical-quality-only"


# --- Optional external JVM selection engine bridge --------------------------
#
# Expects --engine-home to be a Gradle `installDist` output directory
# (lib/*.jar + a launch class on the classpath). This file never invokes
# selection/MMR/segment logic itself; it only shells out to the engine
# with a plain TSV protocol and merges its JSON output.


def resolve_java(override):
    if override:
        return override
    found = shutil.which("java")
    if not found:
        raise SanitizedError("missing_java")
    return found


def validate_engine_home(engine_home):
    lib_dir = os.path.join(engine_home, "lib")
    if not os.path.isdir(lib_dir):
        raise SanitizedError("missing_engine")
    try:
        jars = [name for name in os.listdir(lib_dir) if name.endswith(".jar")]
    except OSError:
        raise SanitizedError("missing_engine")
    if not jars:
        raise SanitizedError("missing_engine")
    return lib_dir


def json_value_is_bounded(value):
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return value == value and value not in (float("inf"), float("-inf"))
    if isinstance(value, list):
        return all(json_value_is_bounded(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and json_value_is_bounded(v) for k, v in value.items())
    return False


def run_engine(java_bin, lib_dir, stdin_text, timeout):
    args = [java_bin, "-cp", os.path.join(lib_dir, "*"), ENGINE_MAIN_CLASS]
    try:
        proc = subprocess.run(
            args,
            input=stdin_text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise SanitizedError("engine_timeout")
    except OSError:
        raise SanitizedError("engine_launch_failed")
    if proc.returncode != 0:
        raise SanitizedError("engine_failed")
    try:
        data = json.loads(proc.stdout.decode("utf-8", "strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise SanitizedError("malformed_engine_output")
    if not isinstance(data, dict) or not json_value_is_bounded(data):
        raise SanitizedError("malformed_engine_output")
    return data


def tsv_number(value):
    return "" if value is None else repr(float(value))


def build_engine_stdin_images(candidates, distances, count, threshold):
    lines = ["PHOTO\t%d\t%r" % (count, float(threshold))]
    for c in candidates:
        if c.get("status") != "ok":
            continue
        lines.append(
            "I\t%d\t%r\t%r\t%d\t%s"
            % (c["index"], c["sharpness"], c["exposureScore"], c["pixelCount"], tsv_number(c.get("aestheticsScore")))
        )
    for d in distances:
        lines.append("D\t%d\t%d\t%r" % (d["firstIndex"], d["secondIndex"], d["distance"]))
    return "\n".join(lines) + "\n"


def build_engine_stdin_video(candidates, duration, budget, threshold, count, distances, importance_weight=0.3):
    timestamped = [
        c for c in candidates
        if c.get("status") == "ok" and c["timeSeconds"].get("status") == "measured"
    ]
    if not timestamped:
        raise SanitizedError("no_known_timestamps")
    weight_val = 0.3 if importance_weight is None else float(importance_weight)
    lines = [
        "VIDEO\t%d\t%r\t%r\t%r\t%r" % (count, float(threshold), float(duration), float(budget), weight_val)
    ]
    for c in timestamped:
        quality = c["sharpness"] * c["exposureScore"]
        lines.append(
            "F\t%d\t%r\t%r\t%s"
            % (c["index"], c["timeSeconds"]["actual"], quality, tsv_number(c.get("importanceScore")))
        )
    for d in distances:
        lines.append("D\t%d\t%d\t%r" % (d["firstIndex"], d["secondIndex"], d["distance"]))
    return "\n".join(lines) + "\n"


def invoke_engine(args, stdin_text):
    java_bin = resolve_java(args.engine_java)
    lib_dir = validate_engine_home(args.engine_home)
    timeout = ENGINE_SUBPROCESS_TIMEOUT + int(getattr(args, "budget", 0) or 0)
    return run_engine(java_bin, lib_dir, stdin_text, timeout)


def analyze_images(args):
    paths = args.image
    if not paths:
        return error_result("no_images_provided")
    if len(paths) > MAX_IMAGES:
        return error_result("too_many_images")
    try:
        ffmpeg = find_tool("ffmpeg", args.ffmpeg)
        ffprobe = find_tool("ffprobe", args.ffprobe)
        learned = None
        namespace = DESCRIPTOR_NAMESPACE
        if args.embedding_backend in LEARNED_BACKENDS:
            learned, namespace = init_learned(args)
    except SanitizedError as exc:
        return error_result(str(exc))

    candidates = []
    descriptors = []
    vectors = []
    relevances = []
    succeeded = 0
    failed = 0
    for index, path in enumerate(paths):
        try:
            validate_local_media_path(path)
            width, height, rotation = probe_image(ffprobe, path)
            raw = decode_canvas(ffmpeg, path, rotation)
            box = content_box(width, height, rotation)
            sharpness, exposure, tiles, hist = compute_descriptor(raw, box)
            relevance = None
            if learned is not None:
                learned_w, learned_h = content_dims(width, height, rotation, LEARNED_CANVAS)
                learned_rgb = decode_exact(ffmpeg, path, rotation, learned_w, learned_h)
                vector, relevance = encode_with_learned(learned, learned_rgb, learned_w, learned_h)
                vectors.append(vector)
            else:
                vectors.append(None)
            candidates.append(
                {
                    "index": index,
                    "status": "ok",
                    "sharpness": sharpness,
                    "exposureScore": exposure,
                    "pixelCount": width * height,
                    "aestheticsScore": None,
                    "relevanceScore": relevance,
                }
            )
            descriptors.append((tiles, hist))
            relevances.append(relevance)
            succeeded += 1
        except SanitizedError as exc:
            candidates.append({"index": index, "status": "failed", "error": str(exc)})
            descriptors.append(None)
            vectors.append(None)
            relevances.append(None)
            failed += 1

    if learned is not None:
        distances = build_embedding_distances(vectors)
    else:
        distances = build_distances(descriptors)
    status = "ok" if failed == 0 else ("partial" if succeeded > 0 else "error")
    result = {
        "schema": SCHEMA,
        "descriptorNamespace": namespace,
        "rankingScope": ranking_scope(args, next((r for r in relevances if r is not None), None)),
        "mode": "images",
        "status": status,
        "counts": {"requested": len(paths), "succeeded": succeeded, "failed": failed},
        "candidates": candidates,
        "distances": distances,
    }

    if status != "error" and args.engine_home:
        try:
            stdin_text = build_engine_stdin_images(candidates, distances, args.count, args.threshold)
            result["selection"] = invoke_engine(args, stdin_text)
        except SanitizedError as exc:
            result["status"] = "error"
            result["error"] = str(exc)

    return result


def compute_coarse_timestamps(duration, interval, max_frames):
    if duration <= 0 or max_frames <= 0:
        return []
    if not math.isfinite(interval) or interval <= 0:
        return []
    n_by_interval = math.ceil(duration / interval)
    if n_by_interval <= max_frames:
        timestamps = [round(i * interval, 3) for i in range(n_by_interval)]
    else:
        step = duration / max_frames
        timestamps = [round(i * step, 3) for i in range(max_frames)]
    return [t for t in timestamps if 0 <= t < duration]


def select_anchors(coarse_results, refine_budget, importance_weight=0.3):
    if refine_budget <= 0 or not REFINE_OFFSETS:
        return []
    max_anchors = max(1, refine_budget // len(REFINE_OFFSETS))
    ok_results = [r for r in coarse_results if r.get("status") == "ok"]
    if not ok_results:
        return []

    w = 0.3 if importance_weight is None else float(importance_weight)
    all_have_valid_importance = True
    for r in ok_results:
        imp = r.get("importanceScore")
        if imp is None or finite_unit_interval(imp) is None:
            all_have_valid_importance = False
            break

    # Deterministic ranking: score descending, tie-breaking by original coarse order / planned time.
    # Python's list.sort is stable; sorting by score reverse=True preserves original coarse order on ties.
    if all_have_valid_importance:
        ok_results.sort(
            key=lambda r: (1.0 - w) * (r["sharpness"] * r["exposureScore"]) + w * float(r["importanceScore"]),
            reverse=True,
        )
    else:
        ok_results.sort(
            key=lambda r: r["sharpness"] * r["exposureScore"],
            reverse=True,
        )
    return ok_results[:max_anchors]


def analyze_video(args):
    path = args.video
    try:
        ffmpeg = find_tool("ffmpeg", args.ffmpeg)
        ffprobe = find_tool("ffprobe", args.ffprobe)
        validate_local_media_path(path)
        width, height, duration, rotation, start_time = probe_video(ffprobe, path)
        learned = None
        namespace = DESCRIPTOR_NAMESPACE
        if args.embedding_backend in LEARNED_BACKENDS:
            learned, namespace = init_learned(args)
    except SanitizedError as exc:
        return error_result(str(exc))

    box = content_box(width, height, rotation)
    planned_times = compute_coarse_timestamps(duration, args.interval, args.max_frames)

    candidates = []
    descriptors = []
    vectors = []
    relevances = []
    sampled_times = set()

    def sample(planned):
        try:
            raw, raw_pts = decode_canvas_with_pts(ffmpeg, path, rotation, planned)
            actual = None
            if raw_pts is not None:
                normalized = raw_pts - start_time
                # Only trust the normalized source-relative pts if it falls
                # within the probed stream duration; otherwise it cannot be
                # reliably attributed to this source and is left unknown
                # rather than substituted with the planned seek time.
                if 0.0 <= normalized <= duration:
                    actual = normalized
            sharpness, exposure, tiles, hist = compute_descriptor(raw, box)
            relevance = None
            if learned is not None:
                learned_w, learned_h = content_dims(width, height, rotation, LEARNED_CANVAS)
                learned_rgb = decode_exact(ffmpeg, path, rotation, learned_w, learned_h, seek_seconds=planned)
                vector, relevance = encode_with_learned(learned, learned_rgb, learned_w, learned_h)
                vectors.append(vector)
            else:
                vectors.append(None)
            if actual is not None:
                time_info = {"planned": planned, "actual": actual, "status": "measured"}
            else:
                time_info = {"planned": planned, "actual": None, "status": "unknown"}
            # importanceScore here is text-query relevance (siglip2 + --query),
            # NOT a personal-importance/scene-significance probability.
            text_relevance = (
                relevance
                if args.embedding_backend == "siglip2" and args.query and relevance is not None
                else None
            )
            result = {
                "status": "ok",
                "timeSeconds": time_info,
                "sharpness": sharpness,
                "exposureScore": exposure,
                "pixelCount": width * height,
                "aestheticsScore": None,
                "importanceScore": text_relevance,
                "relevanceScore": relevance,
            }
            descriptors.append((tiles, hist))
            relevances.append(relevance)
        except SanitizedError as exc:
            result = {
                "status": "failed",
                "error": str(exc),
                "timeSeconds": {"planned": planned, "actual": None, "status": "unknown"},
            }
            descriptors.append(None)
            vectors.append(None)
            relevances.append(None)
        return result

    coarse_results = []
    for t in planned_times:
        r = sample(t)
        coarse_results.append(r)
        sampled_times.add(round(t, 2))

    resolved_weight, weight_source = resolve_importance_weight(args)

    refine_requested = args.refine
    anchors = select_anchors(coarse_results, refine_requested, importance_weight=resolved_weight)
    refine_performed = 0
    refine_results = []
    if anchors:
        budget_remaining = refine_requested
        for anchor in anchors:
            planned_anchor = anchor["timeSeconds"]["planned"]
            for offset in REFINE_OFFSETS:
                if budget_remaining <= 0:
                    break
                t = round(planned_anchor + offset, 2)
                if t < 0 or t >= duration:
                    continue
                if t in sampled_times:
                    continue
                sampled_times.add(t)
                refine_results.append(sample(t))
                refine_performed += 1
                budget_remaining -= 1
            if budget_remaining <= 0:
                break

    all_results = coarse_results + refine_results
    for index, r in enumerate(all_results):
        r["index"] = index
    succeeded = sum(1 for r in all_results if r["status"] == "ok")
    failed = sum(1 for r in all_results if r["status"] == "failed")
    if learned is not None:
        distances = build_embedding_distances(vectors)
    else:
        distances = build_distances(descriptors)

    # Refinement anchor ranking metadata
    coarse_ok = [r for r in coarse_results if r.get("status") == "ok"]
    coarse_all_have_importance = (
        bool(coarse_ok)
        and all(
            r.get("importanceScore") is not None and finite_unit_interval(r.get("importanceScore")) is not None
            for r in coarse_ok
        )
    )
    refinement_effective_weight = resolved_weight if coarse_all_have_importance else 0.0
    refinement_mode = "weighted" if (coarse_all_have_importance and resolved_weight > 0.0) else "uniform-quality-only"

    policy_meta = {
        "importanceWeight": resolved_weight,
        "weightSource": weight_source,
        "effectiveImportanceWeight": None,
        "missingImportancePolicy": "uniform-quality-only",
        "minimumQualityGate": 0.25,
        "mode": "not-run",
        "evaluation": "not_run",
    }

    status = "ok" if failed == 0 else ("partial" if succeeded > 0 else "error")
    result = {
        "schema": SCHEMA,
        "descriptorNamespace": namespace,
        "rankingScope": ranking_scope(args, next((r for r in relevances if r is not None), None)),
        "mode": "video",
        "status": status,
        "counts": {"requested": len(all_results), "succeeded": succeeded, "failed": failed},
        "video": {
            "durationSeconds": duration,
            "width": width,
            "height": height,
            "refinement": {
                "enabled": refine_requested > 0,
                "requested": refine_requested,
                "performed": refine_performed,
                "effectiveImportanceWeight": refinement_effective_weight,
                "mode": refinement_mode,
            },
            "policy": policy_meta,
            "candidates": all_results,
        },
        "distances": distances,
    }

    if status != "error" and args.engine_home:
        try:
            stdin_text = build_engine_stdin_video(
                all_results, duration, args.budget, args.threshold, args.count, distances, importance_weight=resolved_weight
            )
            selection_output = invoke_engine(args, stdin_text)
            result["selection"] = selection_output
            used_importance = selection_output.get("usedImportanceScores")
            if used_importance is True:
                policy_meta["effectiveImportanceWeight"] = resolved_weight
                policy_meta["mode"] = "weighted" if resolved_weight > 0.0 else "uniform-quality-only"
                policy_meta["evaluation"] = "engine_verified"
            elif used_importance is False:
                policy_meta["effectiveImportanceWeight"] = 0.0
                policy_meta["mode"] = "uniform-quality-only"
                policy_meta["evaluation"] = "engine_verified"
            else:
                policy_meta["effectiveImportanceWeight"] = None
                policy_meta["mode"] = "unknown"
                policy_meta["evaluation"] = "unknown"
        except SanitizedError as exc:
            result["status"] = "error"
            result["error"] = str(exc)

    return result


def error_result(code):
    return {
        "schema": SCHEMA,
        "descriptorNamespace": DESCRIPTOR_NAMESPACE,
        "status": "error",
        "error": code,
        "counts": {"requested": 0, "succeeded": 0, "failed": 0},
        "candidates": [],
        "distances": [],
    }


def add_shared_args(sub_parser):
    sub_parser.add_argument(
        "--embedding-backend", choices=("basic", "dinov2", "siglip2"), default="basic"
    )
    sub_parser.add_argument("--model-dir", default=None)
    sub_parser.add_argument("--query", default=None)
    sub_parser.add_argument("--engine-home", default=None)
    sub_parser.add_argument("--engine-java", default=None)
    sub_parser.add_argument("--count", type=int, default=None)
    sub_parser.add_argument("--threshold", type=float, default=None)


def resolve_importance_weight(args):
    configured = getattr(args, "importance_weight", None)
    if configured is not None:
        return float(configured), "explicit"
    query = getattr(args, "query", None)
    has_nonempty_query = isinstance(query, str) and bool(query.strip())
    if getattr(args, "embedding_backend", None) == "siglip2" and has_nonempty_query:
        return 1.0, "query_default"
    return 0.3, "legacy_default"


def build_parser():
    parser = SanitizedArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=SanitizedArgumentParser)

    images_p = sub.add_parser("images", help="Analyze a set of still images")
    images_p.add_argument("--image", action="append", default=[], help="Path to an image (repeatable, max %d)" % MAX_IMAGES)
    images_p.add_argument("--ffmpeg", default=None)
    images_p.add_argument("--ffprobe", default=None)
    add_shared_args(images_p)
    images_p.set_defaults(func=analyze_images)

    video_p = sub.add_parser("video", help="Analyze sampled frames from a video")
    video_p.add_argument("--video", required=True)
    video_p.add_argument("--importance-weight", type=float, default=None)
    video_p.add_argument("--max-frames", type=int, default=30)
    video_p.add_argument("--interval", type=float, default=1.0)
    video_p.add_argument("--refine", type=int, default=60)
    video_p.add_argument("--budget", type=float, default=30.0)
    video_p.add_argument("--ffmpeg", default=None)
    video_p.add_argument("--ffprobe", default=None)
    add_shared_args(video_p)
    video_p.set_defaults(func=analyze_video)

    return parser


def validate_shared_args(args):
    if args.embedding_backend in LEARNED_BACKENDS and not args.model_dir:
        return "model_dir_required"
    if args.query and args.embedding_backend != "siglip2":
        return "query_not_supported"
    if args.engine_home:
        if args.count is None or args.count <= 0:
            return "count_required"
        if args.threshold is None or finite_number(args.threshold) is None or args.threshold < 0:
            return "threshold_required"
    return None


def main(argv):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "video":
        if args.importance_weight is not None and finite_unit_interval(args.importance_weight) is None:
            print(json.dumps(error_result("invalid_importance_weight")))
            return 1
        if not (0 < args.max_frames <= 300):
            print(json.dumps(error_result("invalid_max_frames")))
            return 1
        if not math.isfinite(args.interval) or not (args.interval > 0):
            print(json.dumps(error_result("invalid_interval")))
            return 1
        if not (0 <= args.refine <= 300):
            print(json.dumps(error_result("invalid_refine")))
            return 1
        if finite_positive(args.budget) is None:
            print(json.dumps(error_result("invalid_budget")))
            return 1

    shared_error = validate_shared_args(args)
    if shared_error:
        print(json.dumps(error_result(shared_error)))
        return 1

    result = args.func(args)
    print(json.dumps(result))
    return 0 if result.get("status") in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
