# hanai-media-selector / analyze_media.py

Portable, **local-only** numeric feature extraction for photos and video
frames, implemented with the Python 3 standard library plus the `ffmpeg`
and `ffprobe` executables you already have installed. It does not use
any Apple/Vision framework, does not download or install anything, and
does not select, rank, or compare media beyond emitting raw numeric
proxies and pairwise distances as JSON. Every `--image`/`--video` input
must resolve to an existing local regular file (symlinks to one are
fine); directories, missing paths, and URLs are rejected before any
`ffprobe`/`ffmpeg` subprocess runs, and those subprocesses are further
restricted to the `file`/`pipe` protocols so a crafted local playlist
cannot trigger a network fetch.

This tool is a **feature extraction adapter**, not a selection engine.
Image diversity selection and video segment selection remain the
responsibility of the Swift/Kotlin cores (see
`Sources/HanAI/Image/ImageDiversitySelector.swift` and
`android/hanai-core/.../ImageDiversitySelector.kt`), which can consume
this JSON as one numeric input alongside their own logic. The existing
HanAI workflow already implements an important-scene/important-photo
workflow; the pieces described below (embedding backends, the optional
JVM engine bridge) add technical-quality numeric ranking and, with
`--query`, externally supplied text-relevance scoring - they are
additive quality/diversity refinements and a local feature path, not a
replacement for that existing workflow.

Three independent, optional layers can be combined:

1. **Feature-only (default)** - basic perceptual descriptor, as before.
2. **`--embedding-backend dinov2|siglip2`** - swaps the pairwise
   descriptor/distance for a local, user-installed pretrained model's
   embedding (via the sibling `learned_features.py`, not owned by this
   file). `siglip2` can optionally take `--query TEXT` for text-image
   relevance scoring.
3. **`--engine-home DIR`** - additionally bridges to an external JVM
   selection engine (a Gradle `installDist` output) that performs actual
   anchor/segment selection from the computed features and distances.

Layers 2 and 3 are independent: you can request an embedding backend
without an engine (JSON only has richer `distances`/`relevanceScore`),
or an engine with the basic descriptor (`--engine-home` without
`--embedding-backend`).

## Prerequisites

- Python 3.7+ (standard library only, no pip packages).
- `ffmpeg` and `ffprobe` already installed and on `PATH`, or pass
  `--ffmpeg /path/to/ffmpeg` / `--ffprobe /path/to/ffprobe` explicitly.
- Works the same way on macOS, Windows, and Linux (including a
  NAS/server with Python 3 + ffmpeg installed) — there is no dependency
  on Apple frameworks. This does **not** imply that iOS or Android can
  run Python subprocesses; this tool targets desktop/server/NAS
  environments. Native apps are expected to call this tool out-of-process
  (or a packaged equivalent) and consume its JSON output through their
  own adapters.
- Optional: a local, already-downloaded DINOv2 or SigLIP2 model
  directory for `--embedding-backend`, loaded by the sibling
  `learned_features.py` module (see its own companion requirements
  file for its model dependencies). This tool never downloads,
  uploads, or installs any model itself — only the exact `--model-dir`
  path you provide is read, with `local_files_only` loading and no
  implicit network access.
- Optional: a Java runtime (`java` on `PATH`, or `JAVA_HOME`, or
  `--engine-java /path/to/java`) and a built selection engine directory
  for `--engine-home` (a Gradle `installDist` output containing
  `lib/*.jar`), if you want this script to also invoke selection
  instead of only emitting features. The engine is a cross-platform
  installed application directory, not an Apple/Vision or mobile
  runtime dependency.

## Usage

### Images

```sh
python3 analyze_media.py images \
  --image /path/to/photo1.jpg \
  --image /path/to/photo2.heic \
  --image /path/to/photo3.png
```

- Up to 200 `--image` paths may be given (repeat the flag). Directories
  and auto-discovery (e.g. Photos library scanning) are intentionally
  not supported — the caller must pass explicit file paths.
- Output is a single JSON object on stdout; there is no file output
  option by design.

#### Shared optional flags (images and video)

```sh
python3 analyze_media.py images --image a.jpg --image b.jpg \
  --embedding-backend siglip2 --model-dir /path/to/local/siglip2 \
  --query "a dog running on a beach" \
  --engine-home /path/to/engine/installDist --count 5 --threshold 0.35
```

- `--embedding-backend basic|dinov2|siglip2` (default `basic`): selects
  the pairwise descriptor source. `basic` is the feature-only
  perceptual descriptor described below; `dinov2`/`siglip2` delegate to
  a local model via the sibling `learned_features.py` module.
- `--model-dir PATH`: **required** when `--embedding-backend` is
  `dinov2` or `siglip2`. Must be a path to an already-downloaded,
  user-installed model directory; never fetched by this tool.
- `--query TEXT`: optional, and only accepted with
  `--embedding-backend siglip2` (text-image relevance). Query text is
  processed locally and is **never printed** to stdout/stderr by this
  tool, including on argument errors.
- `--engine-home DIR`: optional path to a built JVM selection engine
  (`installDist` directory). When given, `--count` and `--threshold`
  become required (positive count; finite, non-negative threshold —
  there is no universal threshold value, it is namespace-specific and
  must be calibrated by the caller/engine for the active descriptor
  namespace).
- `--engine-java PATH`: optional explicit `java` executable; otherwise
  resolved from `PATH`/`JAVA_HOME` via the same mechanism as `ffmpeg`.
- Argument parsing errors never echo the supplied path/query/flag
  values; stderr gets a single sanitized `{"error": "invalid_arguments"}`
  JSON line instead of argparse's default raw-value message.

### Video

```sh
python3 analyze_media.py video \
  --video /path/to/clip.mov \
  --max-frames 30 \
  --interval 1.0 \
  --refine 60
```

- `--max-frames` (1-300, default 30): maximum number of coarse samples.
  Samples are spaced to cover the **full duration** of the video (not
  just the first `max-frames` seconds): if `duration / interval` fits
  within `max-frames`, that fixed interval is used; otherwise the tool
  falls back to an evenly spaced schedule across the whole duration.
- `--interval` (seconds, default 1.0): desired coarse sampling interval.
- `--refine` (0-300, default 60): a bounded budget for additional
  frames sampled near the most promising coarse samples (by the same
  proxy quality score), at offsets of ±0.1s up to ±0.5s. If the budget
  is 0, refinement is skipped and `video.refinement.enabled` is `false`
  in the output.
- `--importance-weight WEIGHT` (optional float in `[0.0, 1.0]`, default `None`):
  controls the trade-off between technical quality (`sharpness * exposureScore`)
  and text query relevance (`importanceScore`) during coarse refinement anchor
  selection and external JVM engine selection.
  - When omitted (`None`), resolves to `1.0` if `--embedding-backend siglip2` is
    used with a non-empty, stripped `--query`; otherwise resolves to legacy `0.3`.
  - When no query is provided (or query is whitespace-only), `importanceScore`
    remains `null`, so ranking operates in technical quality-only mode regardless
    of the resolved weight.
  - Callers wishing to restore the legacy weight when running SigLIP 2 queries
    can explicitly pass `--importance-weight 0.3`.
  - Values outside `[0.0, 1.0]`, `NaN`, and infinities are rejected immediately
    with sanitized error `{"error": "invalid_importance_weight"}` before any tools
    or media are accessed.
- `--budget SECONDS` (default `30`): only used when `--engine-home` is
  given; passed through to the engine as the segment/anchor retention
  time budget. It does not change local sampling/refinement.
- See "Shared optional flags" above for `--embedding-backend`,
  `--model-dir`, `--query`, `--engine-home`, `--engine-java`, `--count`,
  `--threshold`.

## Output schema

Top-level object:

```json
{
  "schema": "hanai-media-selector/1",
  "descriptorNamespace": "perceptual-basic-v1",
  "rankingScope": "technical-quality-only | text-guided-relevance",
  "mode": "images | video",
  "status": "ok | partial | error",
  "counts": {"requested": 0, "succeeded": 0, "failed": 0},
  "candidates": [ ... ],      // images mode only
  "video": { ... },           // video mode only
  "distances": [ ... ],
  "selection": { ... }        // only present when --engine-home was given
}
```

`descriptorNamespace` is `"perceptual-basic-v1"` for the default basic
backend, or whatever namespace the loaded `learned_features.py` module
declares for `dinov2`/`siglip2` (each namespace has its own distance
range/calibration; see below). `rankingScope` is
`"text-guided-relevance"` only when `--embedding-backend siglip2` was
used with a `--query` and at least one candidate produced a finite
relevance score; otherwise `"technical-quality-only"`.

Each image candidate:

```json
{
  "index": 0,
  "status": "ok | failed",
  "error": "sanitized_code",      // only present when status == "failed"
  "sharpness": 0.0,
  "exposureScore": 0.0,
  "pixelCount": 0,
  "aestheticsScore": null,
  "relevanceScore": null
}
```

Video output nests candidates under `video`:

```json
{
  "video": {
    "durationSeconds": 12.3,
    "width": 1920,
    "height": 1080,
    "refinement": {
      "enabled": true,
      "requested": 60,
      "performed": 40,
      "effectiveImportanceWeight": 1.0,
      "mode": "weighted"
    },
    "policy": {
      "importanceWeight": 1.0,
      "weightSource": "query_default",
      "effectiveImportanceWeight": 1.0,
      "missingImportancePolicy": "uniform-quality-only",
      "minimumQualityGate": 0.25,
      "mode": "weighted",
      "evaluation": "engine_verified"
    },
    "candidates": [
      {
        "index": 0,
        "status": "ok",
        "timeSeconds": {"planned": 1.0, "actual": 1.02, "status": "measured"},
        "sharpness": 0.0,
        "exposureScore": 0.0,
        "pixelCount": 0,
        "aestheticsScore": null,
        "importanceScore": null,
        "relevanceScore": null
      }
    ]
  }
}
```

#### Ranking policy and fallback behavior

- `video.policy` provides transparent disclosure of ranking parameters:
  - `importanceWeight`: resolved requested weight (`0.0` - `1.0`).
  - `weightSource`: `"explicit"`, `"query_default"` (SigLIP 2 + non-empty query => `1.0`), or `"legacy_default"` (`0.3`).
  - `effectiveImportanceWeight`: the weight actually applied. Before engine execution or when no engine is configured, this is `null`. After successful engine execution reporting an exact boolean `usedImportanceScores`, it reflects the verified weight (`resolvedWeight` if `true`, `0.0` if `false`). If the engine flag is absent or of invalid type, this is `null`.
  - `missingImportancePolicy`: `"uniform-quality-only"`. If any candidate lacks a valid finite importance score, core engine selection falls back to quality only (`sharpness * exposureScore`). No importance scores are ever fabricated.
  - `minimumQualityGate`: `0.25`. Candidates below this gate are excluded from engine selection.
  - `mode`: `"not-run"` prior to engine execution or without an engine; `"weighted"` or `"uniform-quality-only"` when verified by engine response; `"unknown"` if the engine response flag is missing or malformed.
  - `evaluation`: `"not_run"` prior to engine execution or when no engine is configured; `"engine_verified"` only when an external engine returns an exact boolean `usedImportanceScores`; `"unknown"` if the engine flag is missing or not an exact boolean.
- `video.refinement` independently reports its own `effectiveImportanceWeight` and `mode` based on directly verified coarse candidate scores prior to refinement anchor selection.

#### Engine bridge protocol: optional 6th TSV field

When bridging to the external JVM engine (`--engine-home`), the `VIDEO` TSV header sends the resolved importance weight as an optional sixth column:
```tsv
VIDEO\t<count>\t<threshold>\t<duration>\t<budget>\t<weight>
```
Existing engine callers preserve default `0.3` source compatibility when the sixth column is omitted. The analyzer explicitly passes its resolved requested weight. The photo protocol (`PHOTO`) is unchanged.

#### Evidence limitations

Calibration choices (such as the default importance weight `1.0` under SigLIP 2 with query) are supported strictly by retrospective benchmark calibration data. This query-conditioned adapter ranking does not replace or measure the existing dedicated golf/putter expert engine; its calibration results concern this adapter mode only. Static text matching can mistake preparation, walking, or moving scenes for target actions and is not confirmation that an action occurred. They do not constitute evidence of generic or all-purpose video accuracy gains across arbitrary domains. No claims of full-source motion understanding or unseen held-out generalization are made. Model outputs reflect standard SigLIP sigmoid probability / logit scale calibrations rather than dynamic physical event detection or motion necessity.

`distances` is a flat pairwise list over all successful candidates (by
`index`, in the order they appear in `candidates`):

```json
{"firstIndex": 0, "secondIndex": 1, "distance": 0.0}
```

With the basic backend, `distance` is in `[0, 1]`. With a learned
backend, `distance` is `1 - dot(a, b)` over L2-normalized embeddings,
clamped to `[0, 2]`; pairs with a missing, non-finite, mismatched-length,
or zero embedding are omitted from `distances` rather than fabricated.

All numeric scores are in `[0, 1]` for the basic backend.
`aestheticsScore` is always `null` — neither this tool nor its optional
embedding backends are an aesthetic model. `relevanceScore` is an
optional, per-candidate relevance value (only meaningful for `siglip2`
with a `--query`); it is `null` whenever no embedding backend was
used, the backend is `dinov2` (no text query support), or the backend
did not return a finite relevance for that candidate.

For **video** candidates only, `importanceScore` is set to the same
value as `relevanceScore` (and is otherwise `null`) when
`--embedding-backend siglip2` was used with a `--query` and a finite
relevance was returned for that frame. This is a **text-query
relevance score, not a measurement of personal importance,
scene-significance, or a crop/composition guarantee** — it is only
populated here because the external JVM engine's video protocol reads
`importanceScore` as its text/semantic-relevance input column; the
name is kept for that existing protocol, not because this tool scores
"importance". For **image** candidates, `importanceScore` is always
`null` and is not fed to the engine's photo protocol at all: the
`--engine-home` image-selection path (and
`ImageDiversitySelector`/`ImageDiversitySelector.kt`) rank by
`sharpness`/`exposureScore`/`aestheticsScore`/pairwise distance only
and do **not** currently consume `relevanceScore`, so a `--query` on
images only annotates candidates with relevance for the caller's own
use — it does not change which photos are selected. No performance
numbers for any backend are claimed or measured by this document.

### Optional selection engine output (`selection`)

When `--engine-home` is given, this tool also writes `selection`,
containing exactly the JSON object printed by the external JVM engine
on its stdout (parsed and validated to be a JSON object of
strings/finite numbers/booleans/nulls/nested arrays-or-objects only;
otherwise the run fails with `malformed_engine_output`). This script
does not reimplement or duplicate the engine's selection/MMR/segment
logic — whatever counts, metrics, or per-candidate failures the engine
reports are passed through under `selection` unchanged, including the
engine's own `usedImportanceScores` flag (whatever the Kotlin/JVM core
reports for whether it actually used the `importanceScore` column it
was given — never re-derived or fabricated by this script). For
video, treat the engine's selected anchors/frames and any
budget-retained segments as the actual selection output; this tool's
own `video.candidates` are only the sampled numeric features that
were fed to the engine, not a selection. `importanceScore` on video
candidates is text-query relevance only (see "Output schema" above),
not personal-importance scoring; any personal-importance/scene
ranking remains the existing Swift/Kotlin important-scene workflow's
responsibility, which this is additive to, not a replacement for.

### Timestamp handling

For video, `timeSeconds.planned` is the requested sample time.
`timeSeconds.actual` is the decoder-reported presentation timestamp
parsed from ffmpeg's `showinfo` filter (`pts_time`) for the frame that
was actually returned after seeking with `-copyts`, normalized to be
relative to the source's own start time rather than the raw container
PTS. The `pts_time` value itself is parsed as a signed decimal with
optional scientific notation (e.g. `-1.0`, `1.5e-2`); this tool probes
the video stream's (falling back to the container's) `start_time` via
`ffprobe` and subtracts it from the raw `pts_time` before reporting
`actual`. If `showinfo` output could not be parsed, or the
start-time-normalized value falls outside `[0, durationSeconds]` (so
it cannot be reliably attributed as a source-relative timestamp for
this file), `actual` is `null` and `status` is `"unknown"` — the tool
never substitutes the planned seek time or otherwise fabricates a
timestamp. `status` is `"measured"` only when a decoder-reported pts
was successfully parsed, normalized, and found in-bounds. Because
seeking uses `-ss` before the input is opened (fast seek) for speed
within the 300-sample bound, the actually decoded frame may land on a
nearby keyframe rather than exactly on `planned`; that is exactly why
`actual` is reported separately instead of assuming `planned` is
exact. This tool does not attempt scene-boundary, motion, or event
detection of any kind; sampling is purely time-based.

## What the descriptor is (and isn't)

- For the `basic` descriptor and the `sharpness`/`exposureScore`
  quality proxies (used regardless of `--embedding-backend`), each
  decoded frame/image is fit (aspect-preserving, with padding) into a
  64x64 canvas. Padding pixels are excluded from all metrics by
  computing a content-only sub-rectangle from the probed source
  dimensions.
- When `--embedding-backend dinov2|siglip2` is used, the learned
  encoder is given a **separate** decode straight from the source
  file, aspect-preserving and letterbox-free, scaled to fit within a
  512x512 bound (`scale=w:h` to the exact computed content
  width/height, never a 64x64 canvas upscaled afterward — that would
  discard resolution the model could otherwise use and is explicitly
  avoided). This is a plain, deterministic `ffmpeg` decode/scale, not
  a quality- or accuracy-optimized resize; whatever further
  resize/normalization the loaded DINOv2/SigLIP2 model's own
  processor applies internally (its native input resolution) is that
  model's responsibility and is unaffected by this 512-bound decode.
  No accuracy or quality benchmark is claimed for this choice. The
  64x64 quality-proxy decode and the up-to-512x512 learned-encoder
  decode are two independent `ffmpeg` invocations against the same
  source frame/time; the basic backend only ever performs the 64x64
  decode.
- The perceptual descriptor used for `distances` is a small 8x8
  average-luma tile grid plus an 8-bin-per-channel RGB histogram over
  the content region. **This is a simple spatial low-frequency
  descriptor, not a semantic embedding.** It is not equivalent to
  Vision/ML embeddings, is not a robust duplicate/crop matcher, and
  has no guaranteed relationship to human-perceived similarity.
- `sharpness` is a normalized average local luma-gradient magnitude.
  `exposureScore` is a crude clipped-pixel penalty (fraction of
  near-black/near-white pixels, inverted). Both are **unvalidated
  quality proxies** with arbitrary normalization constants chosen for
  a roughly `[0, 1]` spread, not calibrated or benchmarked quality
  metrics.
- Rotation handling only supports axis-aligned 90/180/270-degree
  rotation metadata (EXIF/display rotation / video rotation side
  data). Arbitrary affine display-matrix rotation is not applied; such
  inputs are decoded unrotated (documented limitation, not silently
  "fixed"). FFmpeg autorotate is disabled and rotation is applied
  manually using a clockwise-positive convention; side-data display
  matrix rotation (counter-clockwise positive) is negated to match it
  and takes priority when present, while the legacy `tags.rotate`
  sign convention is unverified against real codec evidence and is
  preserved as-is.
- There is no universal distance threshold (e.g. no claim that `0.12`
  means "duplicate" or similar). Any threshold used for selection is
  namespace- and use-case-specific and must be calibrated by the
  caller (Swift/Kotlin core) for its own purpose; this tool does not
  select or threshold anything itself.

## Error handling and privacy

- All decoding and probing happens via `subprocess` with explicit
  argument lists (no `shell=True`), `-nostdin`, and bounded timeouts.
  Raw subprocess output (stderr, file paths) is never included in the
  JSON; only fixed sanitized error codes are (e.g.
  `"decode_failed"`, `"invalid_dimensions"`, `"subprocess_timeout"`,
  `"probe_json_invalid"`, `"unexpected_output_size"`).
- Learned-backend failures are explicit and sanitized, with **no
  silent fallback to the basic descriptor**: `"learned_module_missing"`,
  `"learned_module_load_failed"`, `"learned_module_invalid"`,
  `"learned_backend_init_failed"`, `"learned_backend_invalid_namespace"`,
  `"learned_backend_encode_failed"`, `"learned_backend_invalid_vector"`.
  Flag-combination errors (e.g. missing `--model-dir`, `--query` without
  `siglip2`, missing `--count`/`--threshold` with `--engine-home`) are
  likewise sanitized codes (`"model_dir_required"`,
  `"query_not_supported"`, `"count_required"`, `"threshold_required"`),
  and raw argparse parsing errors never echo the offending argument
  value (`"invalid_arguments"`).
- The optional JVM engine bridge uses its own sanitized codes:
  `"missing_java"`, `"missing_engine"` (engine-home/`lib/*.jar` not
  found), `"engine_timeout"`, `"engine_launch_failed"`,
  `"engine_failed"` (non-zero exit), `"malformed_engine_output"`, and
  `"no_known_timestamps"` (video only: raised if none of the sampled
  frames has a decoder-measured timestamp to report to the engine,
  rather than fabricating one from a planned seek time).
- Per-item failures do not abort the whole run: `status` is `"ok"` if
  every item succeeded, `"partial"` if some succeeded, `"error"` if
  none did (or if a required tool was missing). Failed items are kept
  as placeholders with their `index` and sanitized `error` code so
  callers can align with their original input list.
- Candidates are identified only by numeric `index`; no filenames or
  paths are ever written to stdout.
- All processing is local and in-memory. No images or frames are
  exported to disk, no raw pixel data or embeddings are included in
  the output, and the source media files are never modified.
- This tool does not scan, discover, or enumerate any media library on
  its own; it only processes the exact file paths passed on the
  command line.

## Integration notes

- Intended consumption: a desktop/NAS automation step invokes this
  script, and a Swift or Kotlin core adapter parses the JSON and feeds
  the numeric fields (`sharpness`, `exposureScore`, `pixelCount`,
  `distances`, `timeSeconds`) into its own selection/ranking logic
  alongside (or in place of) platform-native signals.
- This script has no knowledge of, and makes no claims about, any
  specific selection runner; wiring it into a particular pipeline is
  left to whichever core owns that integration.

## Temporal video research tools (experimental)

`temporal_features.py` wraps the pinned MIT X-CLIP base/patch32 model locally.
`extract_temporal_features.py` analyzes a caller-provided clip manifest with
8 ordered frames and the same five fixed golf/action comparison prompts as
`extract_evaluation_features.py`. These are evaluation tools, not a new
production golf detector or an enabled mobile feature. Whole-clip and
1.6-second-window fusion experiments have not met the adoption conditions;
see `docs/research/fusion-comparison-2026-10-17.md`.

The model must already be present with its `hanai-model.json` integrity
manifest. The expected checkpoint is `microsoft/xclip-base-patch32`, revision
`a2e27a78a2b5d802e894b8a1ef14f3a8ce490963`; its safetensors file is
786,414,772 bytes, SHA256
`abf286e8cdd0612761c3e42d3a55eca998382dfa67a04a0f3fdcdfa4f150cdbb`.
The adapter never fetches model files, and the optional model dependencies
are listed in `requirements-models.txt`. An inference success on this Mac
is not evidence of Windows, Linux, iOS or Android device execution.

```sh
python3 extract_temporal_features.py \
  --manifest /path/to/local-clips.json \
  --model-dir /path/to/pinned-xclip \
  --output /path/to/new-results.json \
  --session session-1 \
  --window-seconds 1.6
```

A manifest has `schemaVersion: 1` and `clips` records with `clipId` (integer),
`sessionId`, `sourceId` (opaque IDs), `timeSeconds` and a local file `path`.
Keep IDs anonymous when sharing derived results. Output omits media paths,
pixels and embeddings. It includes requested sampling times; actual decoded
PTS remains unknown. Omitting `--window-seconds` samples the entire clip.
Softmax compares only the five supplied prompts and is not a calibrated
probability of a shot or of personal importance.

Existing output is never replaced. Atomic output publication requires
same-directory hardlink support; an unsupported filesystem returns
`output_atomic_publish_failed` and removes the temporary file. A partial
batch preserves per-clip failures instead of pretending all clips succeeded.

`calibrate_fusion.py` compares numeric, calibration-only expert/semantic
rankings. Its `--experiment blocked-logistic` mode reports the predeclared
within-block criteria and falls back to expert-only when they fail. It does
not train X-CLIP or alter original media, labels, existing app scores or apps.
