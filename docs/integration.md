# 앱 연결 (Integration)

이 문서는 한양 코어(기술 모듈 `HanAI`)를 HanClip Apple/Android(또는 다른 앱)에 연결하는 방법과, 기존 앱 구현을 유지한 채 무중단으로 옮기는 절차를 적는다.
**이번 이관 작업에서는 앱을 수정하지 않았다.** 아래는 다음 단계의 계획이다.

## 0. 한양 동기화 계약

- 제품의 공식 명칭은 `한양`이며, 저장소·Swift 모듈 등 기존 기술 식별자만 `HanAI`를 유지한다.
- HanAI, HanClip Apple, HanClip Android는 `project-sync`의 `hanclip` 그룹으로 묶는다.
- 한양의 모델·성능·기능 변경은 공통 fixture와 Swift/Kotlin 코어에서 먼저 독립적으로 성장시킨다. 이 단계에서는 두 앱 adapter와 호출 경로를 바꾸지 않는다.
- 패키지 직접 소비 전까지는 앱의 기존 구현에도 같은 변경을 옮긴다. 직접 소비 전환 뒤에는 HanAI 버전 상승과 adapter 호환성 검증으로 전달한다.
- 대표님의 `마무리`는 HanAI 소스 검토·검증·커밋·푸시까지다. `릴리즈`는 검증된 HanAI 기능을 두 앱에 반영하고 앱별 검증을 끝내는 단계다. 공개 배포는 별도 지시다.
- 어느 앱이든 미적용 또는 미검증이면 `한양 구현 완료 / 두 커플 적용 미완료`로 구분하고 한클립 적용 완료로 보고하지 않는다.

## 1. 원칙

- 앱 저장소에 HanAI의 **절대 경로 의존성을 만들지 않는다.** 배포된 패키지(태그된 Git 저장소 / Maven 아티팩트)만 참조한다.
- 패키지가 배포·검증되기 전까지 앱의 기존 구현(`AudioImpactClassifier.swift`, `AiShotMotionFusion.kt`)을 그대로 유지한다.
- adapter는 앱 저장소에 둔다. HanAI는 플랫폼 API를 모른다.
- 원본 프레임·관절 좌표·오디오는 adapter 안에서만 존재하고, HanAI에는 정규화 수치만 넘긴다.

## 2. Apple (Swift Package)

### 의존성 추가

Xcode → Package Dependencies → HanAI Git 저장소 URL + 태그(`0.2.0`).
개발 중 로컬 확인이 필요하면 Xcode "Add Local Package"를 쓰되, 프로젝트 파일에 남는 참조는 워크스페이스 기준 상대 경로여야 하며 절대 경로가 들어간 변경은 커밋하지 않는다.

```swift
// Package.swift 기반 앱이라면
.package(url: "<HanAI git url>", from: "0.2.0")
```

### Adapter 매핑

| 앱 기존 타입 | HanAI 타입 | 비고 |
| --- | --- | --- |
| `HanClipAiModelVersion` | `GolfModelVersion` | rawValue 동일 |
| `AudioImpactClassifier.currentModelVersion` | `GolfModelVersion.current` | 롤백 지점 |
| `AudioImpactSensitivity` | `AudioImpactSensitivity` | 케이스 이름 동일 |
| `AudioImpactMetrics/Decision` | 동일 이름 | 필드 동일 |
| `GolfSwingVisualSample` 등 | 동일 이름 | 필드 동일 |
| `GolfPuttFusionPolicy.shouldTrigger(hasRecentPose:isSceneStable:…)` | `shouldTrigger(secondsSinceLatestPose:secondsSinceLastGlobalChange:isReady:isTriggerPending:…)` | 불리언 → 초 단위. `GolfSwingMotionAnalyzer.lastGlobalChangeTime`로 장면 안정성 계산 |
| `GolfPuttStrokeAnalyzer` 반환값 직접 사용 | `latchedConfirmedStroke(at:)` + `consumeConfirmedStroke()` | 0.7.0 확정 신호는 0.60초 latch |

Vision 관절 → `GolfSwingPoseSample` 변환(손목 평균, 어깨·골반 중심, 어깨 폭 정규화)은 앱의 기존 코드를 그대로 adapter로 옮긴다.

사진 유사도 기능을 쓰는 앱은 Vision feature print 거리와 기기 내 선명도 점수를 adapter에서 만든 뒤 `ImageSimilaritySelector`에 전달한다. 코어에는 사진 바이트·파일명·사용자 식별자를 넘기지 않는다. 원본을 삭제하지 않고 업로드·분석용 사본만 대표 인덱스로 줄이는 방식을 권장한다.

### 이름 충돌

앱 내부 타입과 HanAI 타입 이름이 같으므로, 이관 단계에서는 `import HanAI` 후 `HanAI.GolfSwingMotionAnalyzer`처럼 모듈 한정자를 쓰거나 앱 타입에 `Legacy` 접두어를 임시로 붙인다.

## 3. Android (Kotlin/JVM 라이브러리)

### 의존성 추가

권장 순서:
1. **로컬 검증**: `android/` 프로젝트에서 `gradle :hanai-core:publishToMavenLocal`(publish 플러그인 추가 후) → 앱에서 `mavenLocal()` + `implementation("com.hanai:hanai-core:0.2.0")`.
2. **팀 공유**: 사내 Maven 저장소 또는 GitHub Packages에 배포하고 앱은 좌표만 참조.
3. 개발 중 임시로 composite build(`includeBuild("../HanAI/android")`)를 쓸 수 있으나 상대 경로만 허용하고 커밋하지 않는다.

hanai-core는 Android SDK를 쓰지 않으므로 앱의 어떤 `minSdk`에서도 동작한다(JVM 17 target — 앱의 `compileOptions`/desugaring 설정과 맞춘다).

### Adapter 매핑

| 앱 기존 타입 (`com.hanclip.android.feature.aishot`) | HanAI (`com.hanai.core`) | 비고 |
| --- | --- | --- |
| `AiShotModelVersion` | `GolfModelVersion` | 0.1.0~0.3.0 케이스가 추가됨 |
| `ShotSensitivity.Loud/Normal/Quiet/Auto` | `AudioImpactSensitivity.NOISY/NORMAL/QUIET/AUTOMATIC` | adapter에서 매핑 |
| `RealtimeImpactMetrics` | `AudioImpactMetrics` | |
| `RealtimeImpactClassifier.detectImpact` | `AudioImpactClassifier.detectImpact` | |
| `AiShotImpactEvidence(isTriggered, confidence, peak, impactScore)` | `AudioImpactDecision` + `AudioImpactMetrics` | 정책이 두 값을 따로 받음 |
| `GolfSwingVisualSample(timeSeconds=…)` | `GolfSwingVisualSample(time=…)` | 필드명 `time` |
| `GolfSwingMotionSignal.impactTimeSeconds` | `impactTime` | |
| `GolfSwingPoseSignal.impactWindowStartSeconds/EndSeconds` | `impactWindowStart/End` | |
| `GolfPuttStrokeSignal.strokeTimeSeconds` | `strokeTime` | |
| enum 케이스 `SeekingAddress` 등 | `SEEKING_ADDRESS` 등 | `rawValue`는 camelCase 문자열 |

ML Kit/MediaPipe 관절 → `GolfSwingPoseSample` 변환은 앱 adapter에 둔다.

## 4. 무중단 단계적 이관 절차

각 단계는 독립적으로 배포 가능하며, 어느 단계에서든 이전 단계로 돌아갈 수 있다.

| 단계 | 내용 | 완료 기준 |
| --- | --- | --- |
| 0. 패키지 준비 | HanAI `swift test`, `gradle test` 통과. 태그 `0.2.0`. | 두 플랫폼 fixture 테스트 녹색 |
| 1. 의존성만 추가 | 앱에 HanAI를 추가하되 호출하지 않는다. 빌드·앱 크기·시작 시간 확인. | 앱 동작 변화 없음 |
| 2. 그림자 모드 | 기존 판정 경로는 그대로 두고, 같은 입력을 HanAI 분석기에도 넣어 결과를 비교한다. 불일치는 기기 내 디버그 카운터로만 집계(원본·좌표·시각 로그 금지). | 실사용 샘플에서 불일치율과 원인 파악. 4절 차이표 항목별 확인 |
| 3. 플래그 전환 | 디버그/내부 빌드에서 HanAI 결과를 실제 트리거로 사용하는 플래그를 켠다. 기본값은 기존 경로. | 내부 사용자 검증 |
| 4. 기본값 전환 | 플래그 기본값을 HanAI로 바꾼다. 기존 경로는 롤백용으로 한 릴리스 동안 유지. | 릴리스 후 문제 없음 |
| 5. 기존 코드 제거 | 앱의 중복 구현(`AudioImpactClassifier.swift`의 판정 부분, `AiShotMotionFusion.kt`)을 삭제하고 adapter만 남긴다. | 앱 테스트가 HanAI fixture와 같은 결과 |

### 롤백

- 앱 단위: 3~4단계 플래그를 끄면 즉시 기존 경로로 복귀.
- 모델 단위: `GolfModelVersion.current`를 이전 케이스로 바꾼 HanAI 패치 릴리스, 또는 adapter가 정책 함수에 `modelVersion:`을 명시해 이전 동작 선택.

### 그림자 모드에서 예상되는 차이

`docs/architecture.md` 4절의 통일 결정 때문에, 특히 퍼팅 확정 시점(Apple은 한 샘플 빠름)과 스윙 융합 정렬 창(Apple은 정렬 검사 없음)에서 불일치가 나타날 수 있다. 이는 결함이 아니라 의도된 통일이며, 그림자 모드 결과를 보고 필요하면 fixture와 함께 계약을 조정한다.

## 5. 사후 분석(영상 파일) 연결

HanAI 코어는 실시간 스트림과 파일 분석을 구분하지 않는다. 사후 분석은 adapter가 AVAssetReader/MediaCodec으로 프레임·오디오를 순서대로 디코딩해 같은 `observe`/`detectImpact`에 넣으면 된다. 디코딩된 프레임은 `Work/`(로컬 전용) 밖으로 나가지 않는다.
