#!/usr/bin/env python3
import argparse
import json
import math
import shutil
import subprocess
import sys
from array import array
from itertools import chain
from pathlib import Path

SAMPLE_RATE = 22050
BLOCK_SAMPLES = 1024
BUCKET_COUNT = 192
PROBE_TIMEOUT = 30
DECODE_TIMEOUT = 120
MAX_DECODE_SECONDS = 3600


def fail(message):
    sys.stderr.write(message + "\n")
    sys.exit(1)


def check_tools():
    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        fail("required external tools ffprobe/ffmpeg were not found on PATH")


def load_manifest(manifest_path):
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        fail("manifest could not be read")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        fail("manifest is not valid JSON")

    if not isinstance(data, dict) or "clips" not in data or not isinstance(data["clips"], list):
        fail("manifest must be an object with a 'clips' array")

    clips = []
    seen_ids = set()
    for row in data["clips"]:
        if not isinstance(row, dict):
            fail("manifest contains an invalid clip entry")
        clip_id = row.get("clipId")
        path = row.get("path")
        is_valid_int_id = type(clip_id) is int and clip_id >= 0
        is_valid_str_id = isinstance(clip_id, str) and clip_id.strip()
        if not (is_valid_int_id or is_valid_str_id):
            fail("manifest contains a clip with a missing or empty clipId")
        if clip_id in seen_ids:
            fail("manifest contains a duplicate clipId")
        seen_ids.add(clip_id)
        if not isinstance(path, str) or not path.strip():
            fail("manifest contains a clip with a missing or empty path")
        if "://" in path:
            fail("manifest contains a clip path that is not a local filesystem path")
        clips.append({"clipId": clip_id, "path": path})
    return clips


def resolve_path(manifest_parent, raw_path):
    p = Path(raw_path)
    if not p.is_absolute():
        p = manifest_parent / p
    return p.resolve()


def probe_clip(abs_path):
    if not abs_path.is_file():
        return None, False, False

    cmd = [
        "ffprobe",
        "-v", "error",
        "-protocol_whitelist", "file,pipe",
        "-print_format", "json",
        "-show_entries", "format=duration:stream=codec_type",
        str(abs_path),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=PROBE_TIMEOUT
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, False, False

    if result.returncode != 0:
        return None, False, False

    try:
        info = json.loads(result.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None, False, False

    has_audio = any(
        isinstance(s, dict) and s.get("codec_type") == "audio"
        for s in info.get("streams", [])
    )

    duration = None
    fmt = info.get("format")
    if isinstance(fmt, dict):
        raw_duration = fmt.get("duration")
        try:
            value = float(raw_duration)
        except (TypeError, ValueError):
            value = None
        if value is not None and math.isfinite(value) and value >= 0:
            duration = value

    return duration, has_audio, True


def decode_pcm(abs_path, max_bytes):
    decode_seconds_cap = MAX_DECODE_SECONDS + (1.0 / SAMPLE_RATE)
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-v", "error",
        "-protocol_whitelist", "file,pipe",
        "-i", str(abs_path),
        "-map", "0:a:0",
        "-vn",
        "-ac", "1",
        "-ar", str(SAMPLE_RATE),
        "-t", str(decode_seconds_cap),
        "-c:a", "pcm_s16le",
        "-f", "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=DECODE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return None, "decode_timeout"
    except OSError:
        return None, "decode_failed"

    if result.returncode != 0:
        return None, "decode_failed"

    pcm = result.stdout
    if len(pcm) % 2 != 0:
        return None, "decode_failed"
    if len(pcm) > max_bytes:
        return None, "decode_limit"
    return pcm, None


def compute_blocks(samples):
    blocks = []
    n = len(samples)
    prev_sample = None
    i = 0
    while i < n:
        block = samples[i:i + BLOCK_SAMPLES]
        count = len(block)

        sum_sq = 0.0
        peak = 0.0
        for s in block:
            norm = s / 32768.0
            sum_sq += norm * norm
            abs_norm = abs(norm)
            if abs_norm > peak:
                peak = abs_norm
        rms = math.sqrt(sum_sq / count) if count > 0 else 0.0

        zero_crossings = 0
        compare_seq = list(chain([prev_sample], block)) if prev_sample is not None else block
        for a, b in zip(compare_seq, compare_seq[1:]):
            sign_a = a >= 0
            sign_b = b >= 0
            if sign_a != sign_b:
                zero_crossings += 1

        blocks.append({
            "offset": i,
            "count": count,
            "rms": rms,
            "peak": peak,
            "zero_crossings": zero_crossings,
        })

        if count > 0:
            prev_sample = block[-1]
        i += BLOCK_SAMPLES

    return blocks


def build_frames(blocks, duration):
    bucket_rms_sum = [0.0] * BUCKET_COUNT
    bucket_rms_n = [0] * BUCKET_COUNT
    bucket_peak = [0.0] * BUCKET_COUNT
    bucket_crossings = [0] * BUCKET_COUNT
    bucket_samples = [0] * BUCKET_COUNT

    for b in blocks:
        if b["count"] == 0:
            continue
        midpoint_time = (b["offset"] + b["count"] / 2) / SAMPLE_RATE
        if duration > 0:
            bucket_index = int((midpoint_time / duration) * BUCKET_COUNT)
        else:
            bucket_index = 0
        if bucket_index < 0:
            bucket_index = 0
        if bucket_index >= BUCKET_COUNT:
            bucket_index = BUCKET_COUNT - 1

        bucket_rms_sum[bucket_index] += b["rms"]
        bucket_rms_n[bucket_index] += 1
        if b["peak"] > bucket_peak[bucket_index]:
            bucket_peak[bucket_index] = b["peak"]
        bucket_crossings[bucket_index] += b["zero_crossings"]
        bucket_samples[bucket_index] += b["count"]

    frames = []
    for idx in range(BUCKET_COUNT):
        time = (idx + 0.5) * duration / BUCKET_COUNT
        rms = bucket_rms_sum[idx] / bucket_rms_n[idx] if bucket_rms_n[idx] > 0 else 0.0
        peak = bucket_peak[idx]
        zcr = (bucket_crossings[idx] / bucket_samples[idx]) if bucket_samples[idx] > 0 else 0.0
        frames.append({
            "time": time,
            "rms": rms,
            "peak": peak,
            "zeroCrossingRate": zcr,
        })
    return frames


def process_clip(manifest_parent, clip):
    try:
        abs_path = resolve_path(manifest_parent, clip["path"])
        duration, has_audio, probe_ok = probe_clip(abs_path)
    except (OSError, RuntimeError, ValueError):
        return {"clipId": clip["clipId"], "duration": None, "frames": [], "status": "probe_failed"}

    if not probe_ok:
        return {"clipId": clip["clipId"], "duration": None, "frames": [], "status": "probe_failed"}

    if not has_audio:
        return {"clipId": clip["clipId"], "duration": duration, "frames": [], "status": "no_audio"}

    if duration is not None and duration > MAX_DECODE_SECONDS:
        return {"clipId": clip["clipId"], "duration": duration, "frames": [], "status": "decode_limit"}

    max_decode_bytes = int(MAX_DECODE_SECONDS * SAMPLE_RATE * 2)
    pcm, decode_error = decode_pcm(abs_path, max_decode_bytes)
    if decode_error is not None:
        return {"clipId": clip["clipId"], "duration": duration, "frames": [], "status": decode_error}

    if len(pcm) == 0:
        return {"clipId": clip["clipId"], "duration": duration, "frames": [], "status": "empty_audio"}

    sample_count = len(pcm) // 2
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    del pcm

    if duration is not None and duration > 0:
        effective_duration = duration
        duration_basis = "probed_media"
    else:
        effective_duration = sample_count / SAMPLE_RATE
        duration_basis = "decoded_audio"

    if effective_duration <= 0:
        return {"clipId": clip["clipId"], "duration": duration, "frames": [], "status": "empty_audio"}

    blocks = compute_blocks(samples)
    frames = build_frames(blocks, effective_duration)

    return {
        "clipId": clip["clipId"],
        "duration": duration if duration is not None else effective_duration,
        "durationBasis": duration_basis,
        "frames": frames,
        "status": "ok",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Extract per-bucket audio evaluation metrics from local clips referenced in a manifest."
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-clips", type=int, default=None)
    args = parser.parse_args()

    if args.max_clips is not None and args.max_clips <= 0:
        fail("--max-clips must be a positive integer")

    check_tools()

    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        fail("manifest path does not exist or is not a file")
    manifest_parent = manifest_path.resolve().parent

    clips = load_manifest(manifest_path)
    if args.max_clips is not None:
        clips = clips[: args.max_clips]

    output_path = Path(args.output)
    if output_path.exists():
        fail("output path already exists; refusing to overwrite")
    if not output_path.parent.is_dir():
        fail("output directory does not exist")

    results = []
    status_counts = {}
    for clip in clips:
        result = process_clip(manifest_parent, clip)
        status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1
        results.append(result)

    output_doc = {
        "schemaVersion": 1,
        "method": "legacy_audio_decoder_approximation",
        "sampleRate": SAMPLE_RATE,
        "pcmFormat": "s16le_mono",
        "blockSamples": BLOCK_SAMPLES,
        "bucketCount": BUCKET_COUNT,
        "aggregation": {
            "rms": "mean_block_rms",
            "peak": "max_block_peak",
            "zeroCrossingRate": "crossings_per_sample",
            "blockAssignment": "midpoint",
        },
        "decoderCompatibility": "ffmpeg approximation; not exact reproduction of the previous platform decoder",
        "maxDecodeSeconds": MAX_DECODE_SECONDS,
        "decodeLimitBehavior": "clips whose probed duration exceeds maxDecodeSeconds, or whose decoded PCM exceeds the byte budget derived from it, are reported with status 'decode_limit' and empty frames instead of being truncated",
        "clips": results,
    }

    try:
        with open(output_path, "x", encoding="utf-8") as f:
            json.dump(output_doc, f, allow_nan=False, ensure_ascii=False, indent=2)
    except FileExistsError:
        fail("output path already exists; refusing to overwrite")
    except OSError:
        fail("output could not be written")

    sys.stdout.write("processed %d clip(s): %s\n" % (len(results), status_counts))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        fail("audio extraction failed")
