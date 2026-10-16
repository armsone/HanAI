# hanai-onnx

Bounded optional module for DINOv2 image feature extraction using ONNX Runtime.

## Runtime Dependency Selection

This module declares ONNX Runtime as `compileOnly("com.microsoft.onnxruntime:onnxruntime:1.24.2")` to avoid bundling a native desktop library into an Android app or forcing a specific platform runtime.

The host application must provide the appropriate runtime dependency:
- **Android host**:
  ```kotlin
  implementation("com.microsoft.onnxruntime:onnxruntime-android:1.24.2")
  ```
- **JVM / Desktop host**:
  ```kotlin
  implementation("com.microsoft.onnxruntime:onnxruntime:1.24.2")
  ```

Choose ONLY the correct native platform runtime for the host target. The host application must separately verify minSDK and ABI compatibility (e.g. `arm64-v8a`, `armeabi-v7a`, `x86_64`).

## Security & Pinned Model Hash

The pinned older runtime version (1.24.2) was chosen by TM to avoid newer Android provider telemetry initialization. Because later security fixes are not all included, this module is strictly intended for **ONLY known hashed graphs**, never as a general-purpose model loader:
- No arbitrary model loading or native parsing of unverified paths.
- No network operations or model downloader functionality.
- Input models are verified strictly by file size (88,400,292 bytes) and streaming SHA-256 (`6b3e37fdea1393dda0ea0312f5f19b20829ca96447ab5b8b3c92a36777fc431d`).
- Exact model bytes are held in memory before passing to the runtime to avoid time-of-check to time-of-use (TOCTOU) file substitution.

## Privacy & Network

- **Own module boundary**: This module performs no intentional network I/O, does not upload photos or extracted feature vectors, and mutates no environment variables or private configs.
- **Runtime telemetry & platform differences**:
  - In official ONNX Runtime 1.24.2, Android and POSIX builds compile with base telemetry stubbed to false (no new 1DS telemetry events).
  - On Windows runtime builds, telemetry defaults to enabled. Before creating our session in `open`, we explicitly call `OrtEnvironment.getEnvironment().setTelemetry(false)` to disable it.
  - Calling this API does not prove zero Windows events occurred prior to environment initialization. We make no universal all-platform telemetry-free claim; platform/runtime diagnostics or external host app policies remain outside this module's control.
  - `setTelemetry(false)` mutates a shared process-wide `OrtEnvironment` singleton, altering telemetry settings for any other ORT components in the same process (requiring host coordination). A host or third-party component could also later re-enable telemetry or perform global initialization outside our control.
- **Input snapshot & integrity**: `infer` snapshots the input `FloatArray` once before range/finite validation and reuses the exact same buffer snapshot for tensor construction, preventing caller mutation between validation and native parsing.

## Image Preprocessing Pipeline

Use `DinoImagePreprocessor.normalizedInput(rgb, width, height)` on oriented sRGB RGB8 pixels. It implements the pinned Pillow/Transformers arithmetic: 256-short-edge bicubic resize, 224 center crop, Double rescale followed by Float32 ImageNet normalization, and NCHW planar output. Platform default bicubic functions are not interchangeable. The helper limits input size and computes only the required crop instead of allocating an arbitrarily long resized image.

Pass its 150,528 Float values to `DinoImageFeatures.infer`. Decode EXIF orientation and color space in the host, process one image at a time off the UI thread, retain vectors only for the current batch, and honor cancellation between decode/inference calls. If any image fails, use the existing policy uniformly for the batch rather than mixing feature namespaces.

Output: exactly 384 L2-normalized float embedding values (`[-1.0, 1.0]`).

## Memory & Performance Considerations

- **No Bitmap Handling**: This module performs no Bitmap or image path decoding; callers should perform decoding and inference off the UI thread (background thread).
- **Transient Memory**: Opening a model allocates a transient ~88.4 MB byte array for cryptographic verification and session initialization. Actual runtime RSS and memory overhead must be measured on the host device.
- **Distance Thresholds**: Distance and similarity thresholds must be calibrated against the specific model revision and preprocessing pipeline; do not inherit old or uncalibrated thresholds.
- **Verified scope**: Host Mac Swift/Core ML and Kotlin/ORT1.24.2 outputs matched within 9.39e-7 on 48 existing inputs. Swift/Kotlin preprocessing matched Pillow bit for bit on those 48 plus 7 synthetic RGB inputs. These results do not verify Android device decoding, latency, memory, or mobile execution. See [native comparison](../../docs/research/native-parity-2026-10-17.md).

## Licenses

- ONNX Runtime: MIT License
- DINOv2 Model: Apache License 2.0

The JAR contains neither model weights nor a platform runtime. Distribute the [complete notices](../../THIRD_PARTY_NOTICES.md) and applicable license files with the host app and model.
