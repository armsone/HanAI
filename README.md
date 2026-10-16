# 한양 (HanAI)

한양은 영상·사진에서 "기억할 순간"을 찾는 재사용 가능한 판정 엔진이다. 특정 앱에 종속되지 않으며,
플랫폼 카메라·오디오·관절 추출은 adapter가 맡고 판정 코어는 **순수 수치 입력 → 판정 출력**을 담당한다. 선택형 네이티브 이미지 adapter는 별도 모듈이다.

제품의 공식 명칭은 **한양**이다. 기존 앱과 패키지 호환을 위한 기술 식별자만 `HanAI`를 유지한다.

- 제품 버전: `0.4.0`
- 골프 판정 모델 계보(AiShot): `0.7.0` (`GolfModelVersion.current`)
- 첫 기능: **AiShot** — 골프 스윙 자동 촬영(충격음 + 화면 움직임 + 자세 결합)과 무음 퍼팅 안전망

## 저장소 구조

```
HanAI/
├── Package.swift                 # 순수 코어 + 선택형 Apple adapter
├── Sources/HanAI/                # 플랫폼 중립 판정 코어 (Swift)
│   ├── HanAIVersion.swift
│   ├── Audio/AudioImpactClassifier.swift
│   └── Golf/…                    # 모델 버전, 상태기계 3종, 융합 정책
├── Tests/HanAITests/             # XCTest — Fixtures/를 읽는다
├── android/                      # 독립 Gradle 프로젝트 (Kotlin/JVM, Android SDK 없음)
│   └── hanai-core/               # com.hanai.core — Swift와 같은 계약
├── Fixtures/golf/*.json          # 두 플랫폼이 공유하는 검증 fixture
├── docs/
│   ├── architecture.md           # 계층, 계약, adapter 책임
│   ├── integration.md            # Apple/Android 연결과 단계적 이관 절차
│   └── learning/                 # 익명화된 학습 provenance 요약
├── Inbox/   (Git 제외, 로컬 전용)  # 원본 영상·사진 투입함
└── Work/    (Git 제외, 로컬 전용)  # 분석 중간물(프레임, 관절 좌표, 오디오 창)
```

`Inbox/`와 `Work/`는 필요할 때 로컬에서 직접 만든다. `.gitignore`가 두 폴더와 일반 영상·사진·오디오 확장자를 차단하며,
저장소에는 익명화된 JSON fixture와 수치 요약만 들어간다.

## 개인정보 경계

- 원본 영상·사진·프레임·오디오·관절 좌표는 **기기 또는 로컬에서만** 처리한다.
- 저장소에는 수치 특징(정규화된 움직임·손 위치·오디오 지표), 라벨, 검증 fixture, 통계 요약만 남긴다.
- 파일명·해시·촬영 시각·사람 식별 정보는 문서와 fixture 어디에도 넣지 않는다.
- 자세한 규칙은 [PROJECT_RULES.md](PROJECT_RULES.md)를 따른다.

## 빌드와 테스트

```bash
# Swift
swift build
swift test

# Kotlin/JVM (android/ 아래)
cd android && ./gradlew :hanai-core:test
```

Kotlin 테스트는 Gradle이 넘기는 `hanai.fixturesDir` 시스템 속성으로 저장소 루트 `Fixtures/`를 읽고,
Swift 테스트는 `#filePath` 기준 상대 경로로 같은 폴더를 읽는다.

## 핵심 public API (두 플랫폼 공통)

| 영역 | 타입 | 역할 |
| --- | --- | --- |
| 버전 | `HanAIVersion`, `GolfModelVersion` | 제품 버전, 모델 계보·기능 플래그·롤백 지점 |
| 이미지 | `ImageSimilaritySelector` | 앱이 추출한 익명 특징 거리와 선명도로 근접 중복 사진의 대표 프레임 선택 |
| 이미지 | `ImageDiversitySelector` | 품질 순서로 엄격한 근접 중복 묶음을 만들고, 서로 다른 묶음의 대표 사진부터 지정 장수 선택 |
| 영상 | `VideoHighlightSelector` | 기존 중요도와 품질을 받아 시간 간격·국소 중복·총 길이를 고려해 프레임과 구간 재선택 |
| 오디오 | `AudioImpactMetrics`, `AudioImpactClassifier.detectImpact` | 실시간 충격음 판정(준비 음성 억제 포함) |
| 화면 움직임 | `GolfSwingMotionAnalyzer` | 정지 → 백스윙 → 다운스윙 상태기계, 전역 변화 기록 |
| 자세 스윙 | `GolfSwingPoseAnalyzer` | 정지 → 손 이동 → 빠른 복귀 → 임팩트 창 |
| 무음 퍼팅 | `GolfPuttStrokeAnalyzer` | 정지 → 작은 백스윙 → 전진 스트로크 → 팔로스루 확정, 아이언 오탐 방지 |
| 퍼터 후보 탐지 | `GolfPutterDetector` | 포즈 스트림과 이동창에서 짧은 횡방향 스트로크 후보를 회수하고 실제 중심 시각을 산출하며, 겹친 창은 한 스트로크로 병합 |
| 융합 | `GolfSwingFusionPolicy`, `GolfPuttFusionPolicy` | 최종 촬영 여부 결정 |

상세 계약과 상수는 [docs/architecture.md](docs/architecture.md), 앱 연결은 [docs/integration.md](docs/integration.md)를 본다.

## 사진·영상 고도화 (0.4.0)

중요한 영상 장면을 찾고 순위를 매기는 기존 역할을 고도화한다. 새 코어는 원본이나 OS 전용 API를 받지 않으며,
Swift와 Kotlin에서 같은 수치 계약을 사용한다. 기존 사진 선택·골프 API는 유지한다.

- 사진 묶음은 모든 구성원 사이의 거리를 확인한다. A와 B, B와 C가 비슷하더라도 A와 C가 다르면 세 장을 한 묶음으로 합치지 않는다.
- 사진 선택은 다른 묶음의 좋은 대표부터 고르되 사용자가 지정한 장수는 평가 가능한 입력 수 안에서 유지한다.
- 영상은 품질 하한을 먼저 적용하고, 가까운 시간의 중복을 줄인다. 멀리 떨어진 같은 장면의 재등장은 허용한다.
- 하이라이트 구간은 영상 경계·선택적 전환점·총 길이 제한을 지킨다. 중심 프레임 전후의 여유 구간이며 사건의 의미적 완결성을 자동 보장하지 않는다.
- 기기별 미디어 디코더·특징 추출기는 adapter다. 공개 모델도 이 계층에 연결하며, 공통 코어에 플랫폼·학습 런타임 의존성을 넣지 않는다.

공개 기술 조사와 적용 한계: [사진 연구](docs/research/photo-selection-2026-10-17.md), [영상 연구](docs/research/video-selection-2026-10-17.md).
로컬 미디어 실행 경로와 준비 방법: [미디어 선택 도구](tools/hanai-media-selector/README.md).
모델 설치·사진 선택·한국어 설명 기반 영상 선택: [한양 0.4.0 사용 안내](docs/media-selection-upgrade.md).
[기존 자료 실제 비교](docs/research/media-evaluation-2026-10-17.md): 새 질의 순위 개선과 기존 퍼팅 전용 최종 엔진의 성능을 구분한다.
코드 추가·빌드, 실제 자료의 개선, 개별 앱 반영과 공개 상태를 구분한다. [같은 사진의 네이티브 비교](docs/research/native-parity-2026-10-17.md)에 수치와 한계를 기록했다. 기존 전용 영상 순위는 새 모델로 교체하지 않는다.

## 현재 상태

- 골프 모델 계보는 `0.7.0`을 유지한다. 앱의 직접 공유와 별도 포트는 각각 확인해야 하며 패키지 버전만으로 모든 앱의 모델·동작 일치를 보장하지 않는다.
- HanAI·HanClip Apple·HanClip Android는 하나의 동기화 그룹으로 등록되어 있지만 한양은 먼저 독립적으로 학습하고 성장한다. `마무리`는 HanAI 소스 검토·검증·커밋·푸시까지이며, 대표님의 `릴리즈` 지시가 있을 때만 검증된 기능을 두 앱의 adapter·호출 경로·테스트에 적용한다.
- 무음 퍼팅 안전망은 대표 퍼팅 클립으로 검증되지 않은 **보수적 설계 상태**다. 학습 근거는 [docs/learning/README.md](docs/learning/README.md)에 있다.


## 선택형 네이티브 이미지 adapter

Apple의 `HanAIApple`과 JVM의 `hanai-onnx`는 고정 DINOv2-small 모델을 로컬에서 실행한다. 공통 코어에 Core ML·Android SDK·ONNX Runtime을 넣지 않는다. Apple은 iOS17/macOS14/Mac Catalyst17 이상, JVM 어댑터는 Java17을 기준으로 한다. Android 호스트는 `onnxruntime-android:1.24.2`를 명시하고 데스크톱용 native runtime을 함께 넣지 않는다.

RGB8 전처리는 양쪽 `DinoImagePreprocessor`를 재사용한다. 디코더의 방향·색공간·기기 메모리와 취소는 앱 책임이다. 모델·추론 실패에는 배치 전체에 기존 분석을 사용하고 원본을 삭제하지 않는다. [런타임 안내](android/hanai-onnx/README.md)와 [전체 고지](THIRD_PARTY_NOTICES.md)를 함께 배포한다. 모델 파일은 소스 패키지나 JAR에 자동 포함되지 않는다.

기존 대표 프레임의 선명도와 시각 정확도를 보존한 [영상32개 비교](docs/research/representative-frame-comparison-2026-10-17.md)에서는16개 개선·16개 유지·관측 악화0이었다. 사건 검출 정확도 향상률은 아니다.
