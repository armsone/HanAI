"""Optional public-pretrained temporal adapter for HanAI media selector.

Wraps a pinned, local copy of an X-CLIP (microsoft/xclip-base-patch32 or
microsoft/xclip-base-patch16-zero-shot, MIT-licensed) model to score a fixed set
of exactly 8 or 32 ordered video frames against a caller-provided list of text
prompts using X-CLIP's native video-text matching head.

Important interpretation note: the returned ``probabilities`` are a softmax
distribution over ONLY the prompts supplied in a given call. They are a
relative comparison within that prompt set, not a calibrated confidence of
any action, event, or personal importance, and they carry no training or
accuracy guarantee. This module performs no training; it only runs
inference with pretrained, publicly released weights.

All model artifacts are supplied by the caller via ``model_dir`` at runtime
and are validated against a fixed, pinned identity before use. This module
never downloads, writes, or mutates model files, and never logs raw file
paths, prompts, or exception details.
"""

from __future__ import annotations

import hashlib
import json
import math
import os

BACKEND = "xclip"
REPO_ID = "microsoft/xclip-base-patch32"
REVISION = "a2e27a78a2b5d802e894b8a1ef14f3a8ce490963"
LICENSE = "mit"
WEIGHTS_FILENAME = "model.safetensors"
WEIGHTS_SIZE = 786414772
WEIGHTS_SHA256 = "abf286e8cdd0612761c3e42d3a55eca998382dfa67a04a0f3fdcdfa4f150cdbb"

PRESETS = {
    "microsoft/xclip-base-patch32": {
        "repo_id": "microsoft/xclip-base-patch32",
        "revision": "a2e27a78a2b5d802e894b8a1ef14f3a8ce490963",
        "license": "mit",
        "weights_size": 786414772,
        "weights_sha256": "abf286e8cdd0612761c3e42d3a55eca998382dfa67a04a0f3fdcdfa4f150cdbb",
        "num_frames": 8,
        "patch_size": 32,
    },
    "microsoft/xclip-base-patch16-zero-shot": {
        "repo_id": "microsoft/xclip-base-patch16-zero-shot",
        "revision": "6746d6a5202bf24300cd9bf36f457f174483d41b",
        "license": "mit",
        "weights_size": 779838804,
        "weights_sha256": "0057077dd2bb6a5852fc9150f6e08f866448f4928f0acd7b267007f22a6adaf7",
        "num_frames": 32,
        "patch_size": 16,
    },
}

MANIFEST_FILENAME = "hanai-model.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_MANIFEST_FILES = 64

NUM_FRAMES = 8
IMAGE_SIZE = 224
PATCH_SIZE = 32
MAX_POSITION_EMBEDDINGS = 77
MAX_PROMPTS = 64
MAX_PROMPT_CHARS = 512
MAX_PIXELS_PER_FRAME = 32_000_000

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)

_REQUIRED_PLAIN_FILES = ("config.json", "preprocessor_config.json")
_TOKENIZER_CANDIDATE_SETS = (
    ("vocab.json", "merges.txt", "tokenizer_config.json", "special_tokens_map.json"),
    ("tokenizer.json",),
)


class TemporalFeatures:
    """Zero-shot video/text matching adapter backed by a pinned X-CLIP model.

    This is a thin inference wrapper around a publicly released, MIT-licensed
    checkpoint. It does not train, fine-tune, or otherwise improve the model,
    and its scores are only valid as a relative comparison among the prompts
    passed to a single ``classify`` call.
    """

    def __init__(self, model_dir: str) -> None:
        self._model_dir = self._resolve_model_dir(model_dir)
        manifest = self._load_manifest(self._model_dir)
        preset = self._verify_manifest_identity(manifest)
        self._preset = preset
        files = self._verify_declared_files(self._model_dir, manifest)
        self._verify_pinned_weights(files, preset)

        config = self._load_json(os.path.join(self._model_dir, "config.json"))
        self._verify_config(config, preset)
        preprocessor = self._load_json(
            os.path.join(self._model_dir, "preprocessor_config.json")
        )
        self._verify_preprocessor(preprocessor)

        torch, transformers = self._lazy_import_model_stack()
        self._torch = torch

        try:
            transformers.logging.set_verbosity_error()
        except Exception:
            pass

        try:
            self._tokenizer = transformers.AutoTokenizer.from_pretrained(
                self._model_dir,
                local_files_only=True,
                trust_remote_code=False,
            )
            self._model = transformers.XCLIPModel.from_pretrained(
                self._model_dir,
                local_files_only=True,
                trust_remote_code=False,
                use_safetensors=True,
            )
        except Exception:
            raise RuntimeError("E_MODEL_LOAD_FAILED") from None

        self._model = self._model.to("cpu").eval()

        self._metadata = {
            "backend": BACKEND,
            "repo_id": preset["repo_id"],
            "revision": preset["revision"],
            "license": preset["license"],
            "weights_sha256": preset["weights_sha256"],
            "input_frames": preset["num_frames"],
            "patch_size": preset["patch_size"],
            "resolution": IMAGE_SIZE,
            "device": "cpu",
            "model_class": "XCLIPModel",
            "preprocessing": (
                "shortest-edge resize to 224 (bilinear) then 224x224 center "
                "crop, ImageNet mean/std normalization"
            ),
            "task": (
                "zero-shot video-text matching; not a trained event detector"
            ),
            "score_semantics": (
                "softmax compares only the prompts given in one call; not a "
                "calibrated action confidence or personal-importance score"
            ),
        }

    @property
    def num_frames(self) -> int:
        return self._preset["num_frames"]

    # -- manifest / file validation -----------------------------------

    @staticmethod
    def _resolve_model_dir(model_dir: str) -> str:
        if not isinstance(model_dir, str) or not model_dir:
            raise ValueError("E_BAD_MODEL_DIR")
        resolved = os.path.realpath(model_dir)
        if not os.path.isdir(resolved):
            raise RuntimeError("E_MODEL_DIR_MISSING")
        return resolved

    @staticmethod
    def _load_manifest(model_dir: str) -> dict:
        manifest_path = os.path.join(model_dir, MANIFEST_FILENAME)
        try:
            if os.path.getsize(manifest_path) > MAX_MANIFEST_BYTES:
                raise RuntimeError("E_MANIFEST_TOO_LARGE")
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("E_MANIFEST_MISSING_OR_CORRUPT") from None
        if not isinstance(manifest, dict):
            raise RuntimeError("E_MANIFEST_MISSING_OR_CORRUPT")
        return manifest

    @staticmethod
    def _verify_manifest_identity(manifest: dict) -> dict:
        if manifest.get("backend") != BACKEND:
            raise RuntimeError("E_MANIFEST_IDENTITY_MISMATCH")
        repo_id = manifest.get("repo_id")
        preset = PRESETS.get(repo_id)
        if not preset:
            raise RuntimeError("E_MANIFEST_IDENTITY_MISMATCH")
        if (
            manifest.get("revision") != preset["revision"]
            or manifest.get("license") != preset["license"]
        ):
            raise RuntimeError("E_MANIFEST_IDENTITY_MISMATCH")
        files = manifest.get("files")
        if not isinstance(files, dict) or not files or len(files) > MAX_MANIFEST_FILES:
            raise RuntimeError("E_MANIFEST_FILES_INVALID")
        return preset

    @classmethod
    def _verify_declared_files(cls, model_dir: str, manifest: dict) -> dict:
        files = manifest["files"]
        seen_basenames = set()
        for name, meta in files.items():
            if (
                not isinstance(name, str)
                or not name
                or os.path.basename(name) != name
                or name in (".", "..")
            ):
                raise RuntimeError("E_MANIFEST_FILES_INVALID")
            if not isinstance(meta, dict):
                raise RuntimeError("E_MANIFEST_FILES_INVALID")
            size = meta.get("size")
            sha256 = meta.get("sha256")
            if not isinstance(size, int) or size < 0 or not isinstance(sha256, str):
                raise RuntimeError("E_MANIFEST_FILES_INVALID")
            seen_basenames.add(name)

            file_path = os.path.join(model_dir, name)
            resolved = os.path.realpath(file_path)
            if os.path.dirname(resolved) != model_dir:
                raise RuntimeError("E_MANIFEST_FILES_INVALID")
            if not os.path.isfile(resolved) or os.path.islink(file_path):
                raise RuntimeError("E_MODEL_FILE_MISSING")

            actual_sha256, actual_size = cls._compute_sha256_and_size(resolved)
            if actual_size != size or actual_sha256.lower() != sha256.lower():
                raise RuntimeError("E_MODEL_FILE_HASH_MISMATCH")

        for required in _REQUIRED_PLAIN_FILES + (WEIGHTS_FILENAME,):
            if required not in seen_basenames:
                raise RuntimeError("E_MANIFEST_FILES_INVALID")

        if not any(
            all(name in seen_basenames for name in candidate_set)
            for candidate_set in _TOKENIZER_CANDIDATE_SETS
        ):
            raise RuntimeError("E_MANIFEST_FILES_INVALID")

        if WEIGHTS_FILENAME.endswith(".bin") or WEIGHTS_FILENAME.endswith(".pkl"):
            raise RuntimeError("E_MANIFEST_FILES_INVALID")

        return files

    @staticmethod
    def _verify_pinned_weights(files: dict, preset: dict) -> None:
        weights_meta = files.get(WEIGHTS_FILENAME)
        if (
            not isinstance(weights_meta, dict)
            or weights_meta.get("size") != preset["weights_size"]
            or str(weights_meta.get("sha256", "")).lower() != preset["weights_sha256"].lower()
        ):
            raise RuntimeError("E_WEIGHTS_IDENTITY_MISMATCH")

    @staticmethod
    def _compute_sha256_and_size(file_path: str) -> tuple:
        hasher = hashlib.sha256()
        size = 0
        with open(file_path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
                size += len(chunk)
        return hasher.hexdigest(), size

    @staticmethod
    def _load_json(path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            raise RuntimeError("E_CONFIG_MISSING_OR_CORRUPT") from None
        if not isinstance(data, dict):
            raise RuntimeError("E_CONFIG_MISSING_OR_CORRUPT")
        return data

    @staticmethod
    def _verify_config(config: dict, preset: dict) -> None:
        vision_config = config.get("vision_config")
        text_config = config.get("text_config")
        if not isinstance(vision_config, dict) or not isinstance(text_config, dict):
            raise RuntimeError("E_CONFIG_UNSUPPORTED")
        ok = (
            config.get("model_type") == "xclip"
            and vision_config.get("num_frames") == preset["num_frames"]
            and vision_config.get("image_size") == IMAGE_SIZE
            and vision_config.get("patch_size") == preset["patch_size"]
            and text_config.get("max_position_embeddings") == MAX_POSITION_EMBEDDINGS
        )
        if not ok:
            raise RuntimeError("E_CONFIG_UNSUPPORTED")

    @staticmethod
    def _verify_preprocessor(preprocessor: dict) -> None:
        def close(a, b, tol=1e-3):
            return abs(float(a) - float(b)) <= tol

        size = preprocessor.get("size")
        if isinstance(size, dict):
            size_ok = size.get("shortest_edge") == IMAGE_SIZE
        else:
            size_ok = size == IMAGE_SIZE

        mean = preprocessor.get("image_mean")
        std = preprocessor.get("image_std")
        mean_ok = (
            isinstance(mean, list)
            and len(mean) == 3
            and all(close(m, e) for m, e in zip(mean, _IMAGENET_MEAN))
        )
        std_ok = (
            isinstance(std, list)
            and len(std) == 3
            and all(close(s, e) for s, e in zip(std, _IMAGENET_STD))
        )

        ok = (
            preprocessor.get("do_resize") is True
            and preprocessor.get("do_center_crop") is True
            and preprocessor.get("do_normalize") is True
            and preprocessor.get("resample") == 2
            and size_ok
            and mean_ok
            and std_ok
        )
        if not ok:
            raise RuntimeError("E_PREPROCESSOR_UNSUPPORTED")

    @staticmethod
    def _lazy_import_model_stack():
        try:
            import torch
            import transformers
        except ImportError:
            raise RuntimeError("E_RUNTIME_DEPENDENCY_MISSING") from None
        return torch, transformers

    # -- inference ------------------------------------------------------

    def _preprocess_frame(self, frame) -> "object":
        torch = self._torch
        from PIL import Image

        if not isinstance(frame, Image.Image) or frame.mode != "RGB":
            raise ValueError("E_BAD_FRAME")
        width, height = frame.size
        if (
            not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
            or width * height > MAX_PIXELS_PER_FRAME
        ):
            raise ValueError("E_BAD_FRAME")

        short, long_ = (width, height) if width <= height else (height, width)
        new_short = IMAGE_SIZE
        new_long = int(IMAGE_SIZE * long_ / short)
        if width <= height:
            new_width, new_height = new_short, new_long
        else:
            new_width, new_height = new_long, new_short

        if new_long > 65536 or new_width * new_height > 16_000_000:
            raise ValueError("E_BAD_FRAME")

        resized = frame.resize((new_width, new_height), Image.Resampling.BILINEAR)

        left = (new_width - IMAGE_SIZE) // 2
        top = (new_height - IMAGE_SIZE) // 2
        cropped = resized.crop((left, top, left + IMAGE_SIZE, top + IMAGE_SIZE))

        raw = bytearray(cropped.tobytes())
        buf = torch.frombuffer(raw, dtype=torch.uint8)
        tensor = buf.reshape(IMAGE_SIZE, IMAGE_SIZE, 3).permute(2, 0, 1).clone()
        tensor = tensor.to(torch.float32) / 255.0

        mean = torch.tensor(_IMAGENET_MEAN, dtype=torch.float32).reshape(3, 1, 1)
        std = torch.tensor(_IMAGENET_STD, dtype=torch.float32).reshape(3, 1, 1)
        tensor = (tensor - mean) / std
        return tensor

    def classify(self, frames: list, prompts: list) -> dict:
        from PIL import Image

        if not isinstance(frames, list) or len(frames) != self.num_frames:
            raise ValueError("E_BAD_FRAMES")
        if not isinstance(prompts, list) or not (1 <= len(prompts) <= MAX_PROMPTS):
            raise ValueError("E_BAD_PROMPTS")
        for prompt in prompts:
            if (
                not isinstance(prompt, str)
                or isinstance(prompt, bool)
                or not (1 <= len(prompt) <= MAX_PROMPT_CHARS)
            ):
                raise ValueError("E_BAD_PROMPTS")

        torch = self._torch
        frame_tensors = [self._preprocess_frame(f) for f in frames]
        pixel_values = torch.stack(frame_tensors, dim=0).unsqueeze(0)

        text_inputs = self._tokenizer(
            list(prompts),
            truncation=True,
            max_length=MAX_POSITION_EMBEDDINGS,
            padding=True,
            return_tensors="pt",
        )

        with torch.inference_mode():
            outputs = self._model(**text_inputs, pixel_values=pixel_values)
            logits_per_video = outputs.logits_per_video

        if logits_per_video.shape[0] != 1 or logits_per_video.shape[1] != len(prompts):
            raise RuntimeError("E_MODEL_OUTPUT_SHAPE")

        logits = logits_per_video[0]
        probabilities = torch.softmax(logits, dim=-1)

        logits_list = [float(x) for x in logits.detach().cpu().tolist()]
        probabilities_list = [float(x) for x in probabilities.detach().cpu().tolist()]

        if not all(math.isfinite(x) for x in logits_list):
            raise RuntimeError("E_MODEL_OUTPUT_INVALID")
        if not all(math.isfinite(x) for x in probabilities_list):
            raise RuntimeError("E_MODEL_OUTPUT_INVALID")

        return {
            "logits": logits_list,
            "probabilities": probabilities_list,
            "model": dict(self._metadata),
        }
