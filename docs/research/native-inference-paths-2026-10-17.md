# DINOv2-small 현지 추론: Catalyst를 포함하는 최소 경로

확인: 2026-10-17 07:36 KST. 공개 자료·패키지 메타데이터만 조사했다. 소스 변경·패키지 설치·모델 변환·실행은 하지 않았다. 입력은 전처리가 끝난 FP32 NCHW `[1,3,224,224]`, 출력은384차원 정규화 특징이라는 비민감 형식만 조사에 사용했다.

## 결론과 2안 비교

| 안 | Apple iOS17 / Mac Catalyst / Mac | Android | 판단 |
| --- | --- | --- | --- |
| **1안 권고** | 기본 Core ML + 고정224 TensorType 모델, 같은 외부 RGB 전처리 | 공식 ORT Android CPU + 기존 ONNX | Catalyst 지원이 있는 시스템 framework를 사용해 별도 C++ 런타임 배포를 줄인다. DINO Core ML 변환·동일성 확인은 아직 필요하다. |
| 2안 | ORT 소스에서 Catalyst 포함 XCFramework를 직접 빌드하고 C/ObjC adapter 연결. 일반 macOS slice도 포함해야 함 | 공식 ORT Android CPU | 같은 ONNX를 보존하지만 자체 binary build·Swift wrapper·아키텍처·배포 관리가 커진다. 현재 SwiftPM을 그대로 추가하는 것만으로는 완성되지 않는다. |

**확정된 플랫폼 경로:** Apple 공식 `MLModel` availability에는 Mac Catalyst13.1+, iOS11+, macOS10.13+가 실제로 포함된다. 이 사실은 DINO 모델 변환 성공이나 앱 실행 성공과 다르다. iOS17용 ML program을 같은 Apple 계열 배포 대상으로 컴파일하는 최소 경로는 Core ML이다. [Apple MLModel](https://developer.apple.com/documentation/coreml/mlmodel), [실제 DocC JSON metadata](https://developer.apple.com/tutorials/data/documentation/coreml/mlmodel.json).

## ORT SwiftPM: 실제 archive에 Catalyst가 없음

공식 최신 release API는 [SwiftPM1.24.2](https://github.com/microsoft/onnxruntime-swift-package-manager/releases/tag/1.24.2), 2026-02-25 배포를 반환했다. [Package.swift](https://github.com/microsoft/onnxruntime-swift-package-manager/blob/1.24.2/Package.swift)는 iOS15/macOS14, 정적 `OnnxRuntimeBindings`, MIT, 아래 binary target을 지정한다.

- [공식 pod archive](https://download.onnxruntime.ai/pod-archive-onnxruntime-c-1.24.2.zip): HEAD52,138,597bytes.
- manifest SHA256: `f7100a992d2a8135168c8afd831e6a58b465349101982aa58b3e11d36e600b54`. 전체 archive를 받지 않아 자체 해시 재검증을 했다는 뜻은 아니다.
- **실제 XCFramework Info.plist를 HTTP Range로 읽은 결과:** `ios-arm64`(ios), `ios-arm64_x86_64-simulator`(ios/simulator), `macos-arm64_x86_64`(macos). `SupportedPlatformVariant=maccatalyst`는 없다.
- zip central directory+Info.plist에 전송된 bytes는11,504였다. framework 바이너리 본문은 내려받지 않았다.

[공식 issue15](https://github.com/microsoft/onnxruntime-swift-package-manager/issues/15)는 아직 open이다. 반면 [ORT Catalyst build PR19534](https://github.com/microsoft/onnxruntime/pull/19534)는 merged이며 실제 [v1.31.0 full-ios build settings](https://github.com/microsoft/onnxruntime/blob/v1.31.0/tools/ci_build/github/apple/default_full_ios_framework_build_settings.json)는 `macabi:arm64/x86_64`, `--macos=Catalyst`, deploy14.0를 포함한다. 즉 **소스 빌드 지원과 SwiftPM 배포 artifact의 지원이 다르다.** 일반 full-apple settings는 macOS/iOS만 있으므로 파일 이름이 비슷하다고 Catalyst까지 포함한다고 가정하지 않는다. 커스텀 구축을 선택한다면 Catalyst+iOS+nativeMac slice와 notices·wrapper를 한 번에 관리해야 한다.

## Core ML 변환의 공식 지원과 DINO의 남은 문제

공식 [CoreMLTools 최신 안정판9.0](https://github.com/apple/coremltools/releases/tag/9.0)은 PyTorch2.7·Python3.13 지원을 명시한다. [9.0 의존성 원문](https://github.com/apple/coremltools/blob/9.0/coremltools/_deps/__init__.py)의 `_TORCH_MAX_VERSION="2.7.0"`도 확인했다. PyTorch2.14 환경에서 지원·변환 성공을 미리 단정하면 안 된다. 변환용 Python3.12/Torch2.7/CoreMLTools9.0 환경을 기존 런타임과 분리하는 것이 제안이다. Transformers와 그 버전 조합의 실제 로딩 성공은 별도 확인한다.

Apple [PyTorch workflow](https://apple.github.io/coremltools/docs-guides/source/convert-pytorch-workflow.html)는 eval `nn.Module` → `torch.jit.trace` → `ct.convert`를 성숙한 권고 경로로 설명하고 ViT 등 변환 사례를 제시한다. 그러나 Apple 공식 문서/9.0 소스 tree에서 **DINOv2-small·224·384CLS 완성 사례를 확인하지 못했다.** ViT 지원이 DINO 전체 변환 보증은 아니다.

특히 원 저자 저장소 [DINOv2 issue102](https://github.com/facebookresearch/dinov2/issues/102)는 small224 trace의 bicubic 위치 임베딩 보간 오류와 후속 수정/커뮤니티 변환을 기록한다. 9.0 공식 [torch ops converter](https://github.com/apple/coremltools/blob/9.0/coremltools/converters/mil/frontend/torch/ops.py)에는 layer_norm/GELU/scaled_dot_product_attention/linalg_vector_norm lowering이 있지만 bicubic 명칭은 없었다. 이것은 관련 기본 연산 지원 확인이며 전체 graph 변환 성공의 증거는 아니다. community의 nearest 보간 대체를 그대로 적용하면 원 모델 특징이 달라지므로 권고하지 않는다.

가장 작은 **변환 실험 제안**:

1. 공식 로컬 safetensors를 일반 Transformers 모델로 로드하고 eager/eval/고정224, Tensor 출력 CLS+L2 정규화 wrapper를 사용한다. hub 원격 Python 실행은 필요 없다.
2. 사전에 끝난 RGB 정규화를 `TensorType(float32,[1,3,224,224])`로 받아 동일 전처리를 유지한다. [Apple input/output 문서](https://apple.github.io/coremltools/docs-guides/source/model-input-and-output-types.html)는 기본 ML program I/O가 fp16일 수 있다고 명시하므로 input·output FP32와 내부 `compute_precision=FLOAT32`를 명시한다. 초기 비교는 CPU_ONLY로 한다. 이후 가속·FP16은 별도 수치/선택 결과 검증 뒤 판단한다.
3. 고정224에서도 위치 임베딩 interpolation이 변환 graph에 남아 막히면 원 모델과 같은 bicubic 계산으로 **고정 크기 위치 임베딩만 오프라인 상수화**하는 최소 수정이 가능할지 확인한다. nearest 변경·다른 해상도 모델 교체로 성공처럼 처리하지 않는다. trace/변환/원래 특징 벡터 비교와 iOS17·Catalyst·Mac 컴파일이 실제 완료 조건이다.

전처리는 EXIF방향·RGB/alpha·resize filter·center crop·채널 순서·mean/std를 플랫폼별로 고정한다. 고정224 tensor라는 사실만으로 서로 다른 resize 결과가 같아지지 않는다. 런타임·변환 precision·전처리 revision에 따라 feature namespace를 구분하고 기존 threshold를 무검증 승계하지 않는다.

## Android: 정확한 stable pin과 실제 artifact

Maven의 실제 [maven-metadata.xml](https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/maven-metadata.xml)은 `release=1.31.0`, lastUpdated20261009051249를 반환했다. 동적 latest가 아닌 `com.microsoft.onnxruntime:onnxruntime-android:1.31.0`이 조사 시점 최신 안정 pin이다.

공식 [AAR](https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.31.0/onnxruntime-android-1.31.0.aar)의 HEAD55,138,854bytes, central directory와 manifest만 HTTP Range3,502bytes 읽었다. 실제 `minSdkVersion=24`, ABI는 `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`이며 각 ORT/native JNI .so가 있다. 오래된 compatibility 페이지의 API21 문구보다 이 특정 artifact를 기준으로 한다. 최종 APK의 ABI split/기기별 증가 용량과 DINO의 실제 메모리·지연은 아직 미측정이다.

[공식 Java API](https://onnxruntime.ai/docs/get-started/with-java.html)의 `OrtEnvironment`→`OrtSession`→FP32 FloatBuffer+shape→run 경로를 사용하면 같은 ONNX graph를 CPU로 실행할 수 있다. result/tensor/session 수명을 닫아 native memory를 보존해야 한다. NNAPI를 등록하지 않은 CPU baseline으로 시작하는 것이 제안이다.

[공식 NNAPI EP](https://onnxruntime.ai/docs/execution-providers/NNAPI-ExecutionProvider.html)는 Android8.1+, 명시적 등록, 지원 연산의 부분 가속을 설명한다. NCHW 옵션은 API29+이며 NHWC보다 느릴 수 있고 FP16 relaxation은 정확도가 달라질 수 있다. NNAPI의 reference-CPU fallback과 ORT CPU kernels는 다르다. `NNAPI_FLAG_CPU_DISABLED`도 모델 전체의 CPU 사용 금지가 아니라 ORT kernels로 돌아갈 수 있다는 뜻이다. 따라서 DINO에서 NNAPI가 더 빠르다고 가정하지 않는다.

### 현지 추론과 runtime telemetry를 구분

**실제1.31.0 AAR manifest**는 INTERNET/ACCESS_NETWORK_STATE permission과 `ai.onnxruntime.TelemetryInitializer` provider를 포함한다. [공식 초기화 소스](https://github.com/microsoft/onnxruntime/blob/v1.31.0/java/src/main/android-telemetry/java/ai/onnxruntime/TelemetryInitializer.java)는 ContentProvider에서 ORT library를 로드한다. 따라서 Application.onCreate 이후 API disable만으로 완전 초기화 차단을 보장할 수 없다.

[v1.31.0 Privacy.md](https://github.com/microsoft/onnxruntime/blob/v1.31.0/docs/Privacy.md)는 공식 build telemetry 기본ON, 완전 차단은 ORT초기화 전 `ORT_DISABLE_TELEMETRY=1` 또는 `--no_telemetry` custom build, API disable은 일부 초기 event보다 늦을 수 있다고 명시한다. 모델 metadata는1DS로 전송하지 않는다고도 설명하므로 이를 원본 사진 업로드라고 과장하지 않는다. 채택 시 초기화 순서·merged manifest·process 전체 초기화 경로까지 함께 확인해야 한다. 이번 조사는 설정을 바꾸지 않았다.

조사 한계: CoreMLTools 잘못된 tutorial URL은 CRAWL_NOT_FOUND였고 공식 workflow/input-output 페이지로 복구했다. Apple JS-only MLModel 페이지는 공식 DocC JSON의 platform metadata로 보완했다. 공식 지원·artifact metadata·변환 제안·실제 실행 성공을 구분했으며, 이 조사에서 후자는 수행하지 않았다. 실제 Exa 검색2회·fetch3회, 일반 웹 교차확인과 공식 API/소스/HTTP Range 메타데이터 읽기를 사용했다.
