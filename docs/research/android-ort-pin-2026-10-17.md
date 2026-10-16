# Android ORT 고정판: 초기 telemetry를 피하는 최소 경로
확인: 2026-10-17 07:38 KST. 공개 원문·메타데이터 조사이며 실행·설치·소스 변경은 없다.

**판단:** `com.microsoft.onnxruntime:onnxruntime-android:1.24.2`는 새 Android1DS 초기화를 포함하지 않는 고정 기준판으로 확인했다. 1.23.2까지 내릴 이유는 찾지 못했다. 다만 최신 보안 수정까지 갖춘 판본이라는 의미는 아니며, 무조건 안전하다고 보증하지 않는다.

| 실제 Maven AAR | bytes | manifest | ABI |
| --- | --- | --- | --- |
| [1.24.2](https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.24.2/onnxruntime-android-1.24.2.aar) | 40,822,946 | minSDK24, telemetry provider·INTERNET/ACCESS_NETWORK_STATE 선언 없음 | arm64-v8a/armeabi-v7a/x86/x86_64 |
| [1.23.2](https://repo.maven.apache.org/maven2/com/microsoft/onnxruntime/onnxruntime-android/1.23.2/onnxruntime-android-1.23.2.aar) | 30,241,654 | minSDK24, 동일하게 provider·network permission 없음 | 같은4ABI |

두 AAR는 전체 다운로드 없이 HTTP Range로 central directory와 실제 AndroidManifest.xml만 각각2,864/2,702bytes 읽었다. 전체 바이너리 해시 검증·disassembly는 수행하지 않았다. manifest 부재만으로 결론 내리지 않고 아래 공식 문서·소스를 함께 확인했다.

- [v1.24.2 Privacy.md](https://github.com/microsoft/onnxruntime/blob/v1.24.2/docs/Privacy.md)는 당시 telemetry 구현이 Windows에만 있고 official Windows에서 기본ON이라고 명시한다. 이 문구를 모든 플랫폼 telemetry-free로 확대하지 않는다.
- [v1.24.2 POSIX Env](https://github.com/microsoft/onnxruntime/blob/v1.24.2/onnxruntime/core/platform/posix/env.cc)는 `Telemetry telemetry_provider_`를 사용한다. [동일 tag telemetry.cc](https://github.com/microsoft/onnxruntime/blob/v1.24.2/onnxruntime/core/platform/telemetry.cc)의 `IsEnabled()`는 false, `LogProcessInfo`·Enable/Disable은 빈 stub이다. 즉 Android의 새로운1DS 초기 event를 막으려고 JNI 환경변수 조작/provider 제거를 추가할 필요가 없는 코드 경로다.
- v1.24.2/v1.23.2의 실제 Git tree를 전체 조회했고 truncated=false였다. `android-telemetry`/`TelemetryInitializer`/`PosixTelemetry` 파일 경로는 없었다. SDK/운영체제/앱 자체 통신까지 없다는 주장은 아니다.
- [Java API](https://github.com/microsoft/onnxruntime/blob/v1.24.2/java/src/main/java/ai/onnxruntime/OrtEnvironment.java)의 `setTelemetry(false)`는 존재한다. 정책을 명시하는 호출은 가능하지만 이 Android판에서 초기 event를 뒤늦게 차단하는 해결책이 필요한 것은 아니다.

## 이후 보안 수정과의 교환관계

[공식1.24.2 release](https://github.com/microsoft/onnxruntime/releases/tag/v1.24.2)는 SparseTensor 경계 검사와 ArrayFeatureExtractor의 out-of-bounds read 수정을 포함한다. 1.23.2보다 이 수정이 있는 기준판을 우선한다. 그러나 이 한 release 비교로 모든 보안 차이를 열거한 것은 아니다.

[공식1.29 release](https://github.com/microsoft/onnxruntime/releases/tag/v1.29.0)가 POSIX/Android/iOS1DS 도입을 명시한다. 동시에 CPU TensorScatter·입력 rank/shape·LayerNorm 등 검증 보강도 포함한다. [1.31 release](https://github.com/microsoft/onnxruntime/releases/tag/v1.31.0)의 Security & Reliability 절에는 초기화 데이터 길이/그래프·함수 검증, Loop/Scan OOB, LayerNormalization axis·Resize scale 검사, Arm GELU/Erf 처리 개선 등이 추가된다. DINO 관련 연산 이름이 있다는 것만으로 현재 정상 모델이 취약하다고 단정하지 않지만, 구버전 고정 시 이 후속 수정이 빠진다는 사실도 숨기지 않는다.

따라서 **정확한 출처·SHA256을 고정한 자체 제공 모델만 로드하는 제한된 추론 경로**에서1.24.2 CPU를 검증 기준으로 삼는 안을 권고한다. 임의 ONNX import·모델 패키지 옵션 실행·신뢰하지 않는 external-data 경로를 허용하는 범용 로더와 같은 판단으로 취급하지 않는다. 최신 hardening이 필요한 범위라면 최신 ORT의 `--no_telemetry` 자체 build가 장기 대안이며, 초기화 이후 API disable만으로1DS 전체 차단을 대신하지 않는다.1.24.2가 최신 안정판이라고 주장하지 않는다.

## opset17 FP32 encoder 조건

[공식 compatibility](https://onnxruntime.ai/docs/reference/compatibility.html)는 이전 opset 지원을 명시하고1.12부터 opset17을 표에 포함한다. 따라서1.24.2 CPU의 표준 ONNX opset17 FP32 encoder라는 선택은 버전 범위상 가능하다. 다만 **opset과 ONNX IR version은 별개**이며 사용된 IR·모든 operator/domain/dtype·입출력shape가 이 runtime에서 지원되어야 한다. ONNX 파일의 opset17 표기만으로 전체 지원을 증명하지 않는다. fixed `[1,3,224,224]`→384차원 모델의 session 생성·실제 추론·기준 벡터 대조는 이 조사에서 수행하지 않았고, 후속 완료 조건이다. 새 runtime으로 재저장한 ORT 전용 format을 구버전에 읽힐 것으로 가정하지 않는다.

조사: 실제 Exa검색1회 → 일반 웹 교차검색1회 → Exa 원문 fetch1회(4URLs), 이후 공식 tag 소스/API·Maven HTTP Range 메타데이터 확인. 파일목록·manifest·stub·Privacy·릴리즈 근거를 결합했으며 전체 AAR나 모델은 다운로드하지 않았다.
