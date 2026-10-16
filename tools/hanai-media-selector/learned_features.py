"""Bounded local pretrained feature adapter for HanAI media selector.

Supports local feature extraction and optional SigLIP relevance scoring using
pinned pretrained weights (DINOv2, SigLIP 2).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any, Tuple

MANIFEST_FILENAME = "hanai-model.json"

SUPPORTED_BACKENDS = {
    "dinov2": {
        "repo_id": "facebook/dinov2-small",
        "revision": "ed25f3a31f01632728cabb09d1542f84ab7b0056",
        "expected_model_type": "dinov2",
        "output_dim": 384,
        "files": {
            "config.json": None,
            "preprocessor_config.json": None,
            "model.safetensors": {
                "size": 88249960,
                "sha256": "ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1",
            },
        },
    },
    "siglip2": {
        "repo_id": "google/siglip2-base-patch16-224",
        "revision": "75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2",
        "expected_model_type": "siglip",
        "output_dim": 768,
        "files": {
            "config.json": None,
            "preprocessor_config.json": None,
            "model.safetensors": {
                "size": 1500800904,
                "sha256": "612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b",
            },
            "tokenizer.json": None,
            "tokenizer_config.json": None,
            "special_tokens_map.json": None,
            "tokenizer.model": None,
        },
    },
}


def _compute_sha256_and_size(file_path: str) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size


def _lazy_import_pillow():
    try:
        from PIL import Image
        return Image
    except ImportError as err:
        raise RuntimeError(
            "Pillow is required for image processing in LearnedFeatures. "
            "Install with 'pip install Pillow==12.3.0'."
        ) from err


def _lazy_import_torch_transformers():
    try:
        import torch
        import transformers
        return torch, transformers
    except ImportError as err:
        raise RuntimeError(
            "PyTorch and Transformers are required for LearnedFeatures inference. "
            "Install with 'pip install torch==2.14.1 transformers==5.20.1 safetensors==0.8.0'."
        ) from err


def _extract_pooler_or_tensor(features: Any) -> Any:
    """Robust helper accepting Tensor or ModelOutput with .pooler_output attribute."""
    import torch

    if isinstance(features, torch.Tensor):
        return features
    if hasattr(features, "pooler_output") and isinstance(features.pooler_output, torch.Tensor):
        return features.pooler_output
    raise RuntimeError(
        f"Unexpected feature output type: expected Tensor or ModelOutput with 'pooler_output', "
        f"got {type(features).__name__}."
    )


class LearnedFeatures:
    """Bounded local pretrained feature adapter for DINOv2 and SigLIP2."""

    def __init__(self, backend: str, model_dir: str, query: str | None = None) -> None:
        if backend not in SUPPORTED_BACKENDS:
            raise ValueError(
                f"Unsupported backend '{backend}'. Supported backends: {sorted(SUPPORTED_BACKENDS.keys())}."
            )

        if not os.path.isdir(model_dir):
            raise RuntimeError(f"Model directory does not exist or is not a directory.")

        if backend == "dinov2" and query is not None and query.strip() != "":
            raise ValueError("DINOv2 backend does not support query conditioning.")

        self.backend = backend
        self.backend_info = SUPPORTED_BACKENDS[backend]
        self.query = query

        self._validate_manifest_and_files(model_dir)

        # Lazy load libraries
        self.torch, self.transformers = _lazy_import_torch_transformers()
        self.Image = _lazy_import_pillow()

        self.device = self.torch.device("cpu")
        self._load_model_and_processor(model_dir)

        # Precompute query representation if query is provided (SigLIP2)
        self.query_text_embedding = None
        if self.backend == "siglip2" and self.query is not None and self.query.strip() != "":
            self._precompute_query_embedding()

        # Compute namespace without raw paths or raw query
        self.namespace = self._compute_namespace(model_dir)

    def _validate_manifest_and_files(self, model_dir: str) -> None:
        manifest_path = os.path.join(model_dir, MANIFEST_FILENAME)
        if not os.path.isfile(manifest_path):
            raise RuntimeError(
                f"Missing manifest file '{MANIFEST_FILENAME}' in model directory."
            )

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as err:
            raise RuntimeError(f"Failed to read manifest '{MANIFEST_FILENAME}'.") from err

        if manifest.get("backend") != self.backend:
            raise RuntimeError("Manifest backend mismatch.")
        if manifest.get("repo_id") != self.backend_info["repo_id"]:
            raise RuntimeError("Manifest repo_id mismatch.")
        if manifest.get("revision") != self.backend_info["revision"]:
            raise RuntimeError("Manifest revision mismatch.")

        declared_files = manifest.get("files", {})
        expected_files = self.backend_info["files"]

        # Check all required files are present and match hashes
        for filename, pinned_meta in expected_files.items():
            file_path = os.path.join(model_dir, filename)
            if not os.path.isfile(file_path):
                raise RuntimeError(f"Required model file missing: '{filename}'.")

            sha256, size = _compute_sha256_and_size(file_path)

            if pinned_meta is not None:
                if size != pinned_meta["size"] or sha256 != pinned_meta["sha256"]:
                    raise RuntimeError(f"Integrity check failed for pinned weight file: '{filename}'.")

            manifest_entry = declared_files.get(filename)
            if not manifest_entry or manifest_entry.get("sha256") != sha256 or manifest_entry.get("size") != size:
                raise RuntimeError(f"Manifest entry mismatch or corruption for file: '{filename}'.")

        # Verify config.json model_type matches expected
        config_path = os.path.join(model_dir, "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = json.load(f)
        except Exception as err:
            raise RuntimeError("Failed to read model config.json.") from err

        config_model_type = config_data.get("model_type")
        expected_type = self.backend_info["expected_model_type"]
        if config_model_type != expected_type:
            raise RuntimeError(
                f"Model config model_type mismatch: expected '{expected_type}', got '{config_model_type}'."
            )

    def _load_model_and_processor(self, model_dir: str) -> None:
        try:
            if self.backend == "dinov2":
                self.processor = self.transformers.AutoImageProcessor.from_pretrained(
                    model_dir,
                    local_files_only=True,
                    trust_remote_code=False,
                    use_fast=False,
                )
                self.model = self.transformers.AutoModel.from_pretrained(
                    model_dir,
                    local_files_only=True,
                    trust_remote_code=False,
                    use_safetensors=True,
                    attn_implementation="eager",
                )
            elif self.backend == "siglip2":
                if self.query is not None and self.query.strip() != "":
                    self.processor = self.transformers.AutoProcessor.from_pretrained(
                        model_dir,
                        local_files_only=True,
                        trust_remote_code=False,
                    )
                else:
                    self.processor = self.transformers.AutoImageProcessor.from_pretrained(
                        model_dir,
                        local_files_only=True,
                        trust_remote_code=False,
                    )
                self.model = self.transformers.AutoModel.from_pretrained(
                    model_dir,
                    local_files_only=True,
                    trust_remote_code=False,
                    use_safetensors=True,
                    attn_implementation="eager",
                )
            else:
                raise RuntimeError(f"Unhandled backend: {self.backend}")
        except Exception as err:
            raise RuntimeError(
                f"Failed to load local model or processor from model directory."
            ) from err

        self.model.to(self.device)
        self.model.eval()

    def _precompute_query_embedding(self) -> None:
        max_pos = getattr(
            getattr(self.model.config, "text_config", None),
            "max_position_embeddings",
            64,
        )
        text_inputs = self.processor(
            text=[self.query],
            padding="max_length",
            truncation=True,
            max_length=max_pos,
            return_tensors="pt",
        )
        with self.torch.inference_mode():
            text_features = self.model.get_text_features(**text_inputs)
            text_tensor = _extract_pooler_or_tensor(text_features)
            # Validate finite nonzero query features immediately
            if not self.torch.isfinite(text_tensor).all() or (text_tensor == 0).all():
                raise RuntimeError("Extracted query features are non-finite or all zeros.")
            text_tensor = self.torch.nn.functional.normalize(text_tensor, p=2, dim=-1)
            self.query_text_embedding = text_tensor

    def _compute_namespace(self, model_dir: str) -> str:
        proc_config_path = os.path.join(model_dir, "preprocessor_config.json")
        proc_hash, _ = _compute_sha256_and_size(proc_config_path)
        metric = "cosine"
        return f"{self.backend_info['repo_id']}@{self.backend_info['revision']}:{proc_hash[:16]}:{metric}"

    def encode(self, rgb: bytes, width: int, height: int) -> tuple[list[float], float | None]:
        if not isinstance(rgb, (bytes, bytearray)):
            raise ValueError("RGB data must be bytes.")
        # Reject bool (since bool is a subclass of int) and ensure dimensions are non-bool int
        if type(width) is not int or type(height) is not int:
            raise TypeError("Width and height must be non-bool integers.")
        if width <= 0 or height <= 0:
            raise ValueError("Width and height must be positive integers.")
        expected_len = width * height * 3
        if len(rgb) != expected_len:
            raise ValueError(
                f"RGB byte length mismatch: expected {expected_len} bytes ({width}x{height}x3), "
                f"got {len(rgb)} bytes."
            )

        image = self.Image.frombytes("RGB", (width, height), bytes(rgb))

        with self.torch.inference_mode():
            inputs = self.processor(images=image, return_tensors="pt")
            inputs = {k: v.to(self.device) for k, v in inputs.items()}

            if self.backend == "dinov2":
                outputs = self.model(**inputs)
                cls_token = outputs.last_hidden_state[:, 0, :]
                norm_features = self.torch.nn.functional.normalize(cls_token, p=2, dim=-1)
                relevance = None
            elif self.backend == "siglip2":
                image_features = self.model.get_image_features(**inputs)
                img_tensor = _extract_pooler_or_tensor(image_features)
                norm_features = self.torch.nn.functional.normalize(img_tensor, p=2, dim=-1)

                if self.query_text_embedding is not None:
                    sim = self.torch.matmul(norm_features, self.query_text_embedding.T)
                    logit_scale = self.model.logit_scale.exp()
                    logit_bias = self.model.logit_bias
                    logits = sim * logit_scale + logit_bias
                    prob = self.torch.sigmoid(logits).squeeze()
                    raw_relevance = float(prob.item())
                    # Reject nonfinite relevance before any min/max clamp (NaN must not turn into 1)
                    import math
                    if not math.isfinite(raw_relevance):
                        raise RuntimeError(f"Computed relevance score is non-finite: {raw_relevance}")
                    # clamp to [0.0, 1.0]
                    relevance = max(0.0, min(1.0, raw_relevance))
                else:
                    relevance = None
            else:
                raise RuntimeError(f"Unsupported backend {self.backend}")

            flat = norm_features.squeeze(0).tolist()

        # Validate finite and non-zero features
        norm_sq = sum(x * x for x in flat)
        import math
        if norm_sq == 0.0 or any(not math.isfinite(x) for x in flat):
            raise RuntimeError("Extracted features are non-finite or all zeros.")

        return flat, relevance


DOWNLOAD_MARKER_FILENAME = "hanai-download.json"


def download_cmd(args: argparse.Namespace) -> None:
    backend = args.backend
    destination = os.path.abspath(args.destination)

    if backend not in SUPPORTED_BACKENDS:
        sys.exit(f"Error: Unknown backend '{backend}'.")

    backend_info = SUPPORTED_BACKENDS[backend]
    repo_id = backend_info["repo_id"]
    revision = backend_info["revision"]
    required_files = backend_info["files"]

    marker_path = os.path.join(destination, DOWNLOAD_MARKER_FILENAME)
    manifest_path = os.path.join(destination, MANIFEST_FILENAME)
    temp_manifest_path = manifest_path + ".tmp"

    allowed_top_level = set(required_files.keys()) | {
        DOWNLOAD_MARKER_FILENAME,
        MANIFEST_FILENAME,
        os.path.basename(temp_manifest_path),
        ".cache",
    }

    # Inspect destination directory safely
    if os.path.islink(destination):
        sys.exit("Error: Destination cannot be a symlink.")
    if os.path.exists(destination) and not os.path.isdir(destination):
        sys.exit("Error: Destination exists and is not a directory.")

    is_new_or_empty = False
    if not os.path.exists(destination):
        try:
            os.makedirs(destination, exist_ok=True)
        except Exception:
            sys.exit("Error: Failed to create destination directory.")
        is_new_or_empty = True
    else:
        entries = os.listdir(destination)
        if not entries:
            is_new_or_empty = True
        else:
            # Destination is non-empty: check for symlinks and unexpected files
            for entry in entries:
                entry_path = os.path.join(destination, entry)
                if os.path.islink(entry_path):
                    sys.exit(f"Error: Destination contains unexpected symlink: '{entry}'.")
                if entry not in allowed_top_level:
                    sys.exit(
                        f"Error: Destination contains unknown file or directory '{entry}'. "
                        "Aborting download to protect existing files."
                    )

            # Validate identity marker
            if not os.path.isfile(marker_path) or os.path.islink(marker_path):
                sys.exit(
                    "Error: Non-empty destination missing valid download identity marker. "
                    "Aborting download to protect existing files."
                )
            try:
                with open(marker_path, "r", encoding="utf-8") as f:
                    marker_data = json.load(f)
            except Exception:
                sys.exit(
                    "Error: Download identity marker is malformed. "
                    "Aborting download to protect existing files."
                )

            if (
                not isinstance(marker_data, dict)
                or marker_data.get("schema_version") != 1
                or marker_data.get("backend") != backend
                or marker_data.get("repo_id") != repo_id
                or marker_data.get("revision") != revision
            ):
                sys.exit(
                    "Error: Download identity marker does not match expected backend, repo_id, or revision. "
                    "Aborting download to protect existing files."
                )

    # If new or empty, write download identity marker before any network/download
    if is_new_or_empty:
        marker_data = {
            "schema_version": 1,
            "backend": backend,
            "repo_id": repo_id,
            "revision": revision,
        }
        temp_marker_path = marker_path + ".tmp"
        try:
            with open(temp_marker_path, "w", encoding="utf-8") as f:
                json.dump(marker_data, f, indent=2)
            os.replace(temp_marker_path, marker_path)
        except Exception:
            sys.exit("Error: Failed to write download identity marker.")

    # Check if bundle is already complete and valid
    if os.path.isfile(manifest_path) and not os.path.islink(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception:
            sys.exit("Error: Existing manifest file is corrupted or unreadable.")

        if (
            not isinstance(manifest, dict)
            or manifest.get("backend") != backend
            or manifest.get("repo_id") != repo_id
            or manifest.get("revision") != revision
        ):
            sys.exit("Error: Existing manifest metadata does not match expected backend or revision.")

        declared_files = manifest.get("files", {})
        bundle_corrupted = False

        for filename, pinned_meta in required_files.items():
            dest_file = os.path.join(destination, filename)
            if not os.path.isfile(dest_file) or os.path.islink(dest_file):
                bundle_corrupted = True
                break
            sha256, size = _compute_sha256_and_size(dest_file)
            if pinned_meta is not None:
                if size != pinned_meta["size"] or sha256 != pinned_meta["sha256"]:
                    bundle_corrupted = True
                    break
            manifest_entry = declared_files.get(filename)
            if not manifest_entry or manifest_entry.get("sha256") != sha256 or manifest_entry.get("size") != size:
                bundle_corrupted = True
                break

        if bundle_corrupted:
            sys.exit("Error: Completed bundle is corrupted or incomplete.")

        print(f"{backend} model bundle is already complete and verified at {revision}.")
        return

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        sys.exit(
            "Error: huggingface_hub is required for download. "
            "Install with 'pip install huggingface_hub==2.2.0'."
        )

    cache_dir = os.path.join(destination, ".cache", "huggingface")

    for filename in required_files.keys():
        try:
            hf_hub_download(
                repo_id=repo_id,
                filename=filename,
                revision=revision,
                local_dir=destination,
                cache_dir=cache_dir,
                token=False,
            )
        except Exception:
            print("Model download failed; repeat the same download command to resume.", file=sys.stderr)
            sys.exit(1)

    files_manifest: dict[str, dict[str, Any]] = {}

    for filename, pinned_meta in required_files.items():
        dest_file = os.path.join(destination, filename)
        if not os.path.isfile(dest_file) or os.path.islink(dest_file):
            print("Model download failed; repeat the same download command to resume.", file=sys.stderr)
            sys.exit(1)

        sha256, size = _compute_sha256_and_size(dest_file)

        if pinned_meta is not None:
            if size != pinned_meta["size"] or sha256 != pinned_meta["sha256"]:
                print("Model download failed; repeat the same download command to resume.", file=sys.stderr)
                sys.exit(1)

        files_manifest[filename] = {
            "size": size,
            "sha256": sha256,
        }

    manifest_data = {
        "backend": backend,
        "repo_id": repo_id,
        "revision": revision,
        "files": files_manifest,
    }

    try:
        with open(temp_manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)
        os.replace(temp_manifest_path, manifest_path)
    except Exception:
        sys.exit("Error: Failed to write model manifest.")

    print(f"Successfully downloaded and verified {backend} model bundle ({revision}).")


def _onnx_export_identity(path: str) -> tuple[int, int]:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino)


def _onnx_export_rollback(path: str, identity: tuple[int, int]) -> None:
    """Remove path only if it is still the same, non-symlinked regular file
    whose identity was captured right after this run published it."""
    try:
        st = os.lstat(path)
    except OSError:
        return
    import stat as _stat

    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode):
        return
    if (st.st_dev, st.st_ino) != identity:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _onnx_export_publish(built_path: str, final_path: str) -> None:
    """Exclusively publish a freshly written file into place via hardlink;
    fails without touching the destination if it already exists."""
    os.link(built_path, final_path)


def export_onnx_cmd(args: argparse.Namespace) -> None:
    import tempfile
    from pathlib import Path

    backend = args.backend
    model_dir = os.path.abspath(args.model_dir)
    output = os.path.abspath(args.output)
    metadata_path = str(Path(str(output) + ".json"))

    if backend != "dinov2":
        sys.exit(f"Error: ONNX export is currently only supported for 'dinov2', got '{backend}'.")

    # Reject pre-existing outputs, including dangling symlinks
    if os.path.lexists(output):
        sys.exit("Error: Output file already exists. Overwrite prohibited.")
    if os.path.lexists(metadata_path):
        sys.exit("Error: Metadata file already exists. Overwrite prohibited.")

    output_dir = os.path.dirname(output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir, exist_ok=True)

    # Build everything in a unique staging directory on the same filesystem
    # as the final output so publishing can use an atomic, exclusive link.
    staging_dir = tempfile.mkdtemp(prefix=".hanai-onnx-export-", dir=output_dir or ".")

    import shutil

    try:
        try:
            import onnx
        except ImportError:
            sys.exit("Error: 'onnx' is required for export-onnx. Install with 'pip install onnx==1.23.2'.")

        # Construct LearnedFeatures('dinov2', model_dir) to enforce every file and pinned weight integrity
        # without duplicate model load, reusing its .model and .torch
        try:
            adapter = LearnedFeatures(backend="dinov2", model_dir=model_dir)
        except Exception:
            sys.exit("Error validating model or loading adapter from the given model directory.")

        torch = adapter.torch
        model = adapter.model
        model.eval()

        # Read preprocessor_config.json from local model_dir to obtain preprocessing params and hash
        proc_config_path = os.path.join(model_dir, "preprocessor_config.json")
        if not os.path.isfile(proc_config_path):
            sys.exit("Error: Missing 'preprocessor_config.json' in model directory.")

        try:
            with open(proc_config_path, "r", encoding="utf-8") as f:
                proc_config = json.load(f)
        except Exception:
            sys.exit("Error reading preprocessor_config.json from model directory.")

        proc_sha256, _ = _compute_sha256_and_size(proc_config_path)

        # Extract preprocessing parameters
        # Expected defaults for dinov2-small: size {"shortest_edge": 256}, crop_size {"height": 224, "width": 224}, resample 3
        resize_size = proc_config.get("size", {}).get("shortest_edge", 256) if isinstance(proc_config.get("size"), dict) else 256
        crop_size = proc_config.get("crop_size", {}).get("height", 224) if isinstance(proc_config.get("crop_size"), dict) else 224
        resample_mode = proc_config.get("resample", 3)
        image_mean = proc_config.get("image_mean", [0.485, 0.456, 0.406])
        image_std = proc_config.get("image_std", [0.229, 0.224, 0.225])
        do_rescale = proc_config.get("do_rescale", True)
        rescale_factor = proc_config.get("rescale_factor", 1.0 / 255.0 if do_rescale else 1.0)

        class DinoV2ExportWrapper(torch.nn.Module):
            def __init__(self, base_model):
                super().__init__()
                self.model = base_model

            def forward(self, pixel_values):
                outputs = self.model(pixel_values=pixel_values)
                cls_token = outputs.last_hidden_state[:, 0, :]
                return torch.nn.functional.normalize(cls_token, p=2, dim=-1)

        export_module = DinoV2ExportWrapper(model)
        export_module.eval()

        dummy_input = torch.zeros(1, 3, 224, 224, dtype=torch.float32)

        staged_output = os.path.join(staging_dir, os.path.basename(output))

        try:
            torch.onnx.export(
                export_module,
                dummy_input,
                staged_output,
                opset_version=17,
                dynamo=False,
                input_names=["pixel_values"],
                output_names=["embedding"],
                do_constant_folding=True,
            )
        except Exception:
            sys.exit("Error during ONNX export.")

        # Compute ONNX file SHA256 and size
        try:
            onnx_sha256, onnx_size = _compute_sha256_and_size(staged_output)
        except Exception:
            sys.exit("Error computing ONNX hash.")

        metadata = {
            "schema_version": 1,
            "backend": backend,
            "repo_id": adapter.backend_info["repo_id"],
            "revision": adapter.backend_info["revision"],
            "processor_sha256": proc_sha256,
            "onnx_sha256": onnx_sha256,
            "opset_version": 17,
            "input": {
                "name": "pixel_values",
                "dtype": "float32",
                "shape": [1, 3, 224, 224],
                "layout": "NCHW/RGB",
                "value_semantics": "already preprocessed and normalized",
            },
            "output": {
                "name": "embedding",
                "dtype": "float32",
                "shape": [1, 384],
                "distance_metric": "cosine",
                "normalization": "L2",
            },
            "preprocessing": {
                "resize": {
                    "shortest_edge": resize_size,
                },
                "center_crop": {
                    "height": crop_size,
                    "width": crop_size,
                },
                "resample": resample_mode,
                "resample_filter": "PIL.Image.BICUBIC (3)",
                "rescale_factor": rescale_factor,
                "image_mean": image_mean,
                "image_std": image_std,
            },
        }

        staged_metadata = os.path.join(staging_dir, os.path.basename(metadata_path))
        try:
            with open(staged_metadata, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)
        except Exception:
            sys.exit("Error writing metadata JSON.")

        # Publish both final files exclusively; neither replaces a
        # pre-existing or concurrently published artifact. Each link is its
        # own exclusive, sequential publication window (no cross-file
        # atomicity claim). Identities of the staged files are captured here,
        # before either link is attempted, so a KeyboardInterrupt landing
        # right after a successful os.link (but before any later statement
        # runs) still leaves both identities available for rollback.
        # `committed` only flips once both links have succeeded, so any exit
        # in between - including KeyboardInterrupt or SystemExit - still
        # rolls back this run's own staged identities. A losing concurrent
        # run's staged inode differs from the winner's, so rollback can never
        # delete the winner's published file.
        output_identity = _onnx_export_identity(staged_output)
        metadata_identity = _onnx_export_identity(staged_metadata)
        committed = False
        try:
            try:
                _onnx_export_publish(staged_output, output)
            except Exception:
                sys.exit("Error: Output file already exists. Overwrite prohibited.")

            try:
                _onnx_export_publish(staged_metadata, metadata_path)
            except Exception:
                sys.exit("Error: Metadata file already exists. Overwrite prohibited.")

            committed = True
        finally:
            if not committed:
                _onnx_export_rollback(output, output_identity)
                _onnx_export_rollback(metadata_path, metadata_identity)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    print(f"Successfully exported DINOv2 ONNX model to {output}")
    print(f"Metadata written to {metadata_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="HanAI Learned Features CLI: download pretrained models and export ONNX graphs."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download command
    p_dl = subparsers.add_parser("download", help="Download and verify official pinned model weights.")
    p_dl.add_argument("--backend", choices=list(SUPPORTED_BACKENDS.keys()), required=True)
    p_dl.add_argument("--destination", required=True, help="Path to empty or new directory for model files.")
    p_dl.set_defaults(func=download_cmd)

    # export-onnx command
    p_exp = subparsers.add_parser("export-onnx", help="Export pretrained model to ONNX format.")
    p_exp.add_argument("--backend", choices=["dinov2"], required=True)
    p_exp.add_argument("--model-dir", required=True, help="Path to local validated model directory.")
    p_exp.add_argument("--output", required=True, help="Destination .onnx file path.")
    p_exp.set_defaults(func=export_onnx_cmd)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
