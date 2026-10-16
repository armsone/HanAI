"""Standalone CLI: export the local, pinned DINOv2-small adapter to Apple CoreML.

Position-encoding interpolation is baked once from the original, unmodified
``Dinov2Embeddings.interpolate_pos_encoding`` method (run at 224x224) into a
fixed buffer, because tracing the original bicubic interpolation blocks
CoreML conversion. No approximation or alternate formula is substituted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MAX_ABS_PARITY_TOLERANCE = 1e-5
FIXED_SPATIAL_SIZE = 224
OUTPUT_DIM = 384


def _resolve(path: str) -> str:
    return os.path.realpath(os.path.abspath(path))


def _reject_if_nested(path_a: str, label_a: str, path_b: str, label_b: str) -> None:
    a = path_a.rstrip(os.sep)
    b = path_b.rstrip(os.sep)
    if a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep):
        raise RuntimeError(f"{label_a} and {label_b} overlap or are parent/child paths.")


def _reject_if_exists(raw_path: str, path: str, label: str) -> None:
    if os.path.lexists(raw_path):
        raise RuntimeError(f"{label} already exists. Refusing to overwrite.")
    if os.path.lexists(path):
        raise RuntimeError(f"{label} already exists. Refusing to overwrite.")
    resolved = os.path.realpath(path)
    if os.path.exists(resolved):
        raise RuntimeError(f"{label} resolves to an existing path. Refusing to overwrite.")


def _identity(path: str) -> tuple[int, int]:
    st = os.lstat(path)
    return (st.st_dev, st.st_ino)


def _rmtree_if_owned(path: str, identity: tuple[int, int]) -> None:
    """Remove path only if it is still the same, non-symlinked directory
    whose identity was captured right after we created it."""
    try:
        st = os.lstat(path)
    except OSError:
        return
    import stat as _stat

    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISDIR(st.st_mode):
        return
    if (st.st_dev, st.st_ino) != identity:
        return
    shutil.rmtree(path, ignore_errors=True)


def _publish_directory(built_dir: str, final_path: str) -> tuple[int, int]:
    """Exclusively reserve final_path as a brand-new directory, then populate
    it from built_dir. Never renames over or replaces an existing directory."""
    os.mkdir(final_path)
    identity = _identity(final_path)
    try:
        for name in os.listdir(built_dir):
            os.rename(os.path.join(built_dir, name), os.path.join(final_path, name))
    except Exception:
        _rmtree_if_owned(final_path, identity)
        raise
    return identity


def _publish_file(built_path: str, final_path: str) -> None:
    """Atomically link a freshly written file into place; fails if the
    destination already exists."""
    os.link(built_path, final_path)


def _run_export(args: argparse.Namespace) -> None:
    from learned_features import LearnedFeatures, _compute_sha256_and_size, MANIFEST_FILENAME

    model_dir = _resolve(args.model_dir)
    output = _resolve(args.output)
    metadata_output = _resolve(args.metadata_output)

    if not os.path.isdir(model_dir):
        raise RuntimeError("Model directory does not exist or is not a directory.")

    _reject_if_nested(output, "output", model_dir, "model directory")
    _reject_if_nested(metadata_output, "metadata output", model_dir, "model directory")
    _reject_if_nested(output, "output", metadata_output, "metadata output")

    _reject_if_exists(args.output, output, "Output")
    _reject_if_exists(args.metadata_output, metadata_output, "Metadata output")

    if not output.endswith(".mlpackage"):
        raise RuntimeError("Output path must end with '.mlpackage'.")
    if not metadata_output.endswith(".json"):
        raise RuntimeError("Metadata output path must end with '.json'.")

    output_parent = os.path.dirname(output) or "."
    metadata_parent = os.path.dirname(metadata_output) or "."
    if not os.path.isdir(output_parent):
        raise RuntimeError("Output parent directory does not exist.")
    if not os.path.isdir(metadata_parent):
        raise RuntimeError("Metadata output parent directory does not exist.")

    adapter = LearnedFeatures(backend="dinov2", model_dir=model_dir)

    import torch
    import coremltools as ct
    import numpy as np

    model = adapter.model
    model.eval()
    embeddings_module = model.embeddings

    if not hasattr(embeddings_module, "interpolate_pos_encoding"):
        raise RuntimeError("Loaded model has no interpolate_pos_encoding method; cannot bake position encoding.")

    original_interpolate = embeddings_module.interpolate_pos_encoding

    class _EmbeddingWrapper(torch.nn.Module):
        def __init__(self, base_model: "torch.nn.Module") -> None:
            super().__init__()
            self.model = base_model

        def forward(self, pixel_values: "torch.Tensor") -> "torch.Tensor":
            outputs = self.model(pixel_values=pixel_values)
            cls_token = outputs.last_hidden_state[:, 0, :]
            return torch.nn.functional.normalize(cls_token, p=2, dim=-1)

    reference_module = _EmbeddingWrapper(model).eval()

    zero_input = torch.zeros((1, 3, FIXED_SPATIAL_SIZE, FIXED_SPATIAL_SIZE), dtype=torch.float32)
    generator = torch.Generator().manual_seed(1234)
    random_input = (
        torch.rand((1, 3, FIXED_SPATIAL_SIZE, FIXED_SPATIAL_SIZE), generator=generator, dtype=torch.float32) * 2.0
        - 1.0
    )

    captured_pos_encoding: dict[str, "torch.Tensor"] = {}

    def _capturing_interpolate(*call_args: Any, **call_kwargs: Any) -> "torch.Tensor":
        result = original_interpolate(*call_args, **call_kwargs)
        captured_pos_encoding["value"] = result.detach().clone()
        return result

    embeddings_module.interpolate_pos_encoding = _capturing_interpolate
    try:
        with torch.no_grad():
            reference_output_zero = reference_module(zero_input).detach().clone()
    except Exception as err:
        raise RuntimeError("Original interpolate_pos_encoding call failed at 224x224.") from err
    finally:
        embeddings_module.interpolate_pos_encoding = original_interpolate

    if "value" not in captured_pos_encoding:
        raise RuntimeError("Original interpolate_pos_encoding did not produce a value to bake.")
    baked_pos_encoding = captured_pos_encoding["value"]

    with torch.no_grad():
        reference_output_random = reference_module(random_input).detach().clone()

    embeddings_module.register_buffer("hanai_fixed_pos_encoding", baked_pos_encoding, persistent=False)

    def _fixed_interpolate(self: "torch.nn.Module", *call_args: Any, **call_kwargs: Any) -> "torch.Tensor":
        return self.hanai_fixed_pos_encoding

    import types

    embeddings_module.interpolate_pos_encoding = types.MethodType(_fixed_interpolate, embeddings_module)

    baked_module = _EmbeddingWrapper(model).eval()

    with torch.no_grad():
        baked_output_zero = baked_module(zero_input)
        baked_output_random = baked_module(random_input)

    def _check_parity(reference: "torch.Tensor", baked: "torch.Tensor", label: str) -> float:
        if tuple(reference.shape) != (1, OUTPUT_DIM) or tuple(baked.shape) != (1, OUTPUT_DIM):
            raise RuntimeError(f"Unexpected output shape for {label} parity check.")
        if not torch.isfinite(reference).all() or not torch.isfinite(baked).all():
            raise RuntimeError(f"Non-finite values detected in {label} parity check.")
        max_abs = (reference - baked).abs().max().item()
        if max_abs > MAX_ABS_PARITY_TOLERANCE:
            raise RuntimeError(f"{label} parity check exceeded tolerance: maxabs={max_abs}.")
        return max_abs

    parity_zero = _check_parity(reference_output_zero, baked_output_zero, "zero-input")
    parity_random = _check_parity(reference_output_random, baked_output_random, "seeded-random-input")

    try:
        with torch.no_grad():
            traced = torch.jit.trace(baked_module, zero_input, strict=True)
    except Exception as err:
        raise RuntimeError("Failed to trace the baked CoreML export module.") from err

    try:
        mlmodel = ct.convert(
            traced,
            convert_to="mlprogram",
            inputs=[ct.TensorType(name="pixel_values", shape=zero_input.shape, dtype=np.float32)],
            outputs=[ct.TensorType(name="embedding", dtype=np.float32)],
            minimum_deployment_target=ct.target.iOS17,
            compute_precision=ct.precision.FLOAT32,
            compute_units=ct.ComputeUnit.CPU_ONLY,
            skip_model_load=True,
        )
    except Exception as err:
        raise RuntimeError("CoreML conversion failed.") from err

    staging_dir = tempfile.mkdtemp(prefix=".hanai-coreml-export-", dir=output_parent)
    try:
        staged_package = os.path.join(staging_dir, os.path.basename(output))
        mlmodel.save(str(staged_package))

        manifest_path = os.path.join(model_dir, MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        config_entry = manifest.get("files", {}).get("config.json", {})
        preprocessor_entry = manifest.get("files", {}).get("preprocessor_config.json", {})
        weight_entry = adapter.backend_info["files"]["model.safetensors"]

        import numpy
        import transformers

        metadata = {
            "schemaVersion": 1,
            "backend": "dinov2",
            "repoId": adapter.backend_info["repo_id"],
            "revision": adapter.backend_info["revision"],
            "sourceWeights": {
                "file": "model.safetensors",
                "sha256": weight_entry["sha256"],
                "size": weight_entry["size"],
            },
            "license": "Apache-2.0",
            "configSha256": config_entry.get("sha256"),
            "configSize": config_entry.get("size"),
            "preprocessorConfigSha256": preprocessor_entry.get("sha256"),
            "preprocessorConfigSize": preprocessor_entry.get("size"),
            "toolchain": {
                "coremltools": ct.__version__,
                "torch": torch.__version__,
                "transformers": transformers.__version__,
                "numpy": numpy.__version__,
            },
            "input": {
                "name": "pixel_values",
                "shape": [1, 3, FIXED_SPATIAL_SIZE, FIXED_SPATIAL_SIZE],
                "dtype": "float32",
                "layout": "NCHW/RGB",
            },
            "output": {
                "name": "embedding",
                "shape": [1, OUTPUT_DIM],
                "dtype": "float32",
                "normalization": "L2",
            },
            "precision": "FLOAT32",
            "computeUnits": "CPU_ONLY",
            "minimumDeploymentTarget": "iOS17",
            "positionInterpolation": {
                "method": "baked_from_original_interpolate_pos_encoding",
                "bakedAtSpatialSize": FIXED_SPATIAL_SIZE,
                "note": (
                    "The exact constant buffer computed by the original, unmodified "
                    "Dinov2Embeddings.interpolate_pos_encoding method at 224x224 was captured and is "
                    "reused in the traced export graph in place of that method, which cannot itself be "
                    "traced for CoreML conversion. The same model instance is reused for export; its "
                    "weights are unmodified and the original interpolation formula is neither changed "
                    "nor approximated."
                ),
            },
            "syntheticParity": {
                "zeroInputMaxAbs": parity_zero,
                "seededRandomInputMaxAbs": parity_random,
                "tolerance": MAX_ABS_PARITY_TOLERANCE,
            },
            "nativeNumericalParity": "pending",
            "preprocessing": {
                "description": (
                    "Original RGB image resized by shortest edge to 256 using bicubic resampling, "
                    "then center-cropped to 224x224, then normalized with ImageNet mean/std. "
                    "The model input tensor is expected to already be preprocessed and normalized NCHW float32."
                ),
                "resizeShortestEdge": 256,
                "resample": "bicubic",
                "centerCrop": [224, 224],
                "imageMean": [0.485, 0.456, 0.406],
                "imageStd": [0.229, 0.224, 0.225],
            },
            "supportedInputSize": "224x224 only",
        }

        staged_metadata = os.path.join(staging_dir, os.path.basename(metadata_output) + ".tmp")
        with open(staged_metadata, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

        output_identity = _publish_directory(staged_package, output)
        try:
            _publish_file(staged_metadata, metadata_output)
        except Exception:
            _rmtree_if_owned(output, output_identity)
            raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    print("CoreML export completed successfully.")
    print(f"Synthetic parity maxabs: zero={parity_zero}, seeded-random={parity_random}")
    print("nativeNumericalParity=pending (no on-device/native execution was performed).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export the local pinned DINOv2-small adapter to a CoreML mlpackage (224x224 fixed input)."
    )
    parser.add_argument("--model-dir", required=True, help="Path to local validated DINOv2-small model directory.")
    parser.add_argument("--output", required=True, help="Destination .mlpackage path (must not already exist).")
    parser.add_argument(
        "--metadata-output", required=True, help="Destination metadata .json path (must not already exist)."
    )
    args = parser.parse_args()

    try:
        _run_export(args)
    except Exception:
        print("Error: CoreML export failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
