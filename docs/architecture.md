# 한양 아키텍처

## 1. 계층

```
┌──────────────────────────────────────────────────────────────┐
│ 앱 (HanClip Apple / HanClip Android / 그 외)                  │
│  - UI, 촬영 시작/저장, ready 상태, 사용자 설정                  │
├──────────────────────────────────────────────────────────────┤
│ Adapter (앱 저장소에 위치, 플랫폼 API 사용)                     │
│  - 카메라 프레임 → GolfSwingVisualSample (격자 차분)            │
│  - Vision/MLKit 관절 → GolfSwingPoseSample (정규화)             │
│  - 마이크 PCM → AudioImpactMetrics + baseline/recentLevel      │
│  - 시계·상태 → referenceTime, secondsSince…, isReady 등          │
├──────────────────────────────────────────────────────────────┤
│ 한양 코어 (기술 모듈 HanAI, 플랫폼 중립, 순수 함수/상태기계)     │
│  - GolfModelVersion / HanAIVersion                             │
│  - AudioImpactClassifier                                       │
│  - GolfSwingMotionAnalyzer / GolfSwingPoseAnalyzer             │
│  - GolfPuttStrokeAnalyzer                                      │
│  - GolfSwingFusionPolicy / GolfPuttFusionPolicy                │
│  - ImageSimilaritySelector (사진 대표본 선택)                    │
└──────────────────────────────────────────────────────────────┘
```

코어는 원본 미디어를 절대 받지 않는다. 시간은 초 단위 `Double`이며 adapter가 하나의 단조 시계로 통일해 넘긴다.

## 2. 데이터 흐름 (AiShot 실시간)

```
오디오 창(≈20~50ms) ─▶ AudioImpactMetrics ─▶ AudioImpactClassifier.detectImpact ─▶ AudioImpactDecision
영상 프레임(≈10~15fps) ─▶ GolfSwingVisualSample ─▶ GolfSwingMotionAnalyzer.observe ─▶ GolfSwingMotionSignal
관절 추정(≈5~10fps)   ─▶ GolfSwingPoseSample ─┬▶ GolfSwingPoseAnalyzer.observe ─▶ GolfSwingPoseSignal
                                              └▶ GolfPuttStrokeAnalyzer.observe ─▶ GolfPuttStrokeSignal

충격음 발생 시:  GolfSwingFusionPolicy.shouldTrigger(decision, metrics, motion, pose, referenceTime, …) → 촬영
자세 샘플마다:  GolfPuttFusionPolicy.shouldTrigger(latchedConfirmedStroke, …)                        → 무음 촬영
```

두 정책은 독립적이다. 스윙 정책은 충격음이 필수이고, 퍼팅 정책은 충격음 없이 자세 시퀀스만 본다.

## 3. Public 계약

모든 타입은 Swift(`HanAI` 모듈)와 Kotlin(`com.hanai.core`)에 같은 이름·의미로 존재한다.
Swift는 `struct`/`enum`, Kotlin은 `data class`/`enum class`/`class`다.

### 3.1 버전

| 타입 | 멤버 | 의미 |
| --- | --- | --- |
| `HanAIVersion` | `product = "0.2.0"`, `golfModel` | 제품 버전, 현재 골프 모델 문자열 |
| `GolfModelVersion` | `v0_1_0 … v0_7_0`, `current = v0_7_0` | 모델 계보와 롤백 지점 |
| | `title`, `releaseDate`, `featureSummary` | UI 표시용 문자열(한국어) |
| | `supportsRealtimeVisualAssist` | 0.2.0+ |
| | `usesAudibleResponseWeight` | 0.2.0+ |
| | `usesGolfSwingMotionFusion` | 0.4.0+ |
| | `usesBodyPoseAssist` | 0.5.0+ |
| | `usesPoseBackedImpactEvidence` | 0.5.1+ |
| | `supportsSoundlessPuttFallback` | 0.6.0 |
| | `requiresVisualShotEvidence` | 0.7.0: 영상 근거 없는 소리 단독 촬영 차단 |
| | `supportsVisualBackedWeakImpact` | 0.7.0: 정렬된 연속 동작이 있는 약한 날카로운 접촉음 보존 |

### 3.2 오디오

| 타입 | 필드/함수 |
| --- | --- |
| `AudioImpactSensitivity` | `noisy`, `normal`, `quiet`, `automatic` |
| `AudioImpactMetrics` | `rms`, `peak`, `crossingRate`, 계산 속성 `impactScore` |
| `AudioImpactDecision` | `isTriggered`, `confidence` |
| `AudioImpactClassifier` | `detectImpact(metrics, baseline, previousRecentLevel, sensitivity)`, `effectiveSensitivity`, `thresholds` |

`baseline`(느린 추종)과 `previousRecentLevel`(빠른 추종)은 adapter가 유지한다. HanClip 앱은 `baseline = baseline×0.985 + clamp(score)×0.015`, `recent = recent×0.72 + score×0.28`로 갱신한다.

### 3.3 화면 움직임 상태기계 `GolfSwingMotionAnalyzer`

| 단계 | 진입 조건 |
| --- | --- |
| `seekingAddress` | 초기/리셋 |
| `addressed` | `localMotion ≤ 0.075`, `globalMotion ≤ 0.08`, `widespreadMotion ≤ 0.32`가 0.55초 이상 |
| `backswing` | `localMotion ≥ 0.12`, `concentration ≥ 0.38`, `0.04 ≤ widespreadMotion ≤ 0.58`인 후보가 0.35초 안에 같은 영역(±1)에서 2회 |
| `downswing` | 백스윙 0.18초 이후, 유효 샘플 3개 이상, `localMotion ≥ 0.20`이고 (가속 ≥ 0.035 또는 `localMotion ≥ max(0.26, peak×1.15)`) |

- 전역 변화(`globalMotion ≥ 0.16` 또는 `widespreadMotion ≥ 0.68` 또는 `brightnessChange ≥ 0.14`)는 근거로 쓰지 않고, 2회 연속이면 리셋. `lastGlobalChangeTime`에 시각을 기록한다(리셋해도 유지).
- 백스윙 1.8초 초과 또는 다른 영역(±1 밖)으로 큰 움직임 이동 시 리셋. 다운스윙 창은 0.42초.
- 신호 신뢰도: downswing `max(0.72, min(1, 0.62 + peak×0.95))`, backswing `min(0.7, 0.34 + peak)`, addressed 0.28.

### 3.4 자세 스윙 상태기계 `GolfSwingPoseAnalyzer`

입력 `GolfSwingPoseSample`: `handX/handY`(몸 크기로 정규화한 손 위치), `coreX/coreY`(몸통 중심), `bodyScale`, `confidence`.

| 단계 | 진입 조건 |
| --- | --- |
| `addressed` | 손 속도 ≤ 0.22, 몸통 속도(몸 크기 정규화) ≤ 0.20이 0.55초·3샘플 이상 |
| `backswing` | 주소 평균에서 손 이동 ≥ 0.20 (이동 방향을 축으로 고정) |
| `impactWindow` | 0.18초 이후, 최고 진행 ≥ 0.28, 축 방향 복귀 속도 ≥ 0.55인 샘플 2개, 복귀량 ≥ `max(0.18, peak×0.55)` |

- 임팩트 창은 감지 시각 기준 `[-0.45초, +0.30초]`.
- 무시 조건: `confidence < 0.45` 또는 `bodyScale < 0.04`. 리셋 조건: 샘플 간격 ≤ 0 또는 > 0.6초, 몸 크기 변화 > 25%, 백스윙 1.8초 초과.
- 신뢰도: impactWindow `min(latestConfidence, min(1, 0.72 + peak×0.45))`, backswing `min(0.7, 0.35 + peak)`, addressed 0.32.

### 3.5 무음 퍼팅 상태기계 `GolfPuttStrokeAnalyzer` (0.6.0 이후)

| 단계 | 진입 조건 |
| --- | --- |
| `addressed` | 손 속도 ≤ 0.25, 몸통 속도 ≤ 0.20이 **0.60초·3샘플** 이상 |
| `backswing` | 주소 평균에서 손 이동 ≥ **0.06** |
| `forwardStroke` | 0.15초 이후, 최고 진행 ≥ **0.12**, 복귀 속도 ≥ 0.20인 복귀 샘플 2개, 진행 ≤ `max(0.04, peak×0.30)`(주소 통과) |
| `confirmedStroke` | 진행 ≤ `-max(0.025, peak×0.15)`인 팔로스루 샘플 2개, 마지막 확정 후 2.0초 경과 |

아이언·풀스윙 오탐 방지(모두 리셋):
- 최고 진행 > **0.60** (백스윙이 퍼팅 범위를 넘음)
- 주소 통과 시 복귀 속도 > **1.5/s** (return speed)
- 팔로스루 진행 ≤ `-max(0.30, peak×1.2)` (큰 follow-through)
- 몸통 속도 > **0.9/s** — 어느 단계든 (큰 core motion, 걷기)
- 백스윙 2.0초 초과, 전진 스트로크 0.9초 초과, 샘플 간격/몸 크기 급변

확정 신호는 `latchedConfirmedStroke(at:)`로 0.6.0에서는 **0.35초**, 0.7.0에서는 **0.60초** 동안 읽을 수 있고, `consumeConfirmedStroke()`로 비운다.
신뢰도는 `min(시퀀스 최소 자세 신뢰도, min(1, 0.62 + peak×0.9))`.

### 3.6 융합 정책

`GolfSwingFusionPolicy.shouldTrigger(decision, metrics, motion, pose?, referenceTime, requiresPoseConfirmation, hasRecentVisualFrame, isInsideReadyPromptWindow, modelVersion)`

1. 최근 영상이 없으면 0.7.0은 false. 이전 모델은 `decision.isTriggered && !isInsideReadyPromptWindow`인 소리 단독 롤백 동작을 유지한다.
2. 정렬된 화면 근거: `motion.isImpactWindow && confidence ≥ 0.72 && (referenceTime − impactTime) ∈ [−0.20, 0.32]`.
3. 정렬된 자세 근거(0.5.1+): `pose.confidence ≥ 0.72 && pose.isImpactWindow(at: referenceTime)`.
4. 둘 다 없으면 false. `requiresPoseConfirmation`이면 자세 근거 필수.
5. 일반 경로는 `decision.isTriggered`가 필요하다. 0.7.0은 예외적으로 정렬된 시각 근거가 있고 `peak ≥ 0.10`, `impactScore ≥ 0.065`, `crossingRate ≥ 0.08`, `crestFactor ≥ 3.5`인 약한 날카로운 접촉음을 허용한다. ready 억제 구간에서는 이 예외를 막는다.
6. 일반 경로가 ready 억제 구간이면 `peak ≥ 0.16 && impactScore ≥ 0.08`만 통과한다.

`GolfPuttFusionPolicy.shouldTrigger(stroke?, poseObservationConfidence, secondsSinceLatestPose, secondsSinceLatestVisualFrame, secondsSinceLastGlobalChange, isReady, isInsideReadyPromptWindow, isTriggerPending, modelVersion)`

모든 조건이 동시에 참이어야 true: `supportsSoundlessPuttFallback`, `stroke.isConfirmedStroke`, `stroke.confidence ≥ 0.72`, `poseObservationConfidence ≥ 0.72`, 자세·프레임 나이 ≤ 0.35초(0.6.0) 또는 ≤ 0.55초(0.7.0), 마지막 전역 변화 후 ≥ 1.0초, `isReady`, ready 억제 구간 아님, 대기 중 트리거 없음.

### 3.7 이미지 유사도 선택

`ImageSimilaritySelector.representativeIndices(candidates, distances, threshold)`는 플랫폼 adapter가 만든 익명 수치만 받아 가까운 중복 사진의 대표 한 장을 고른다.

- 기본 가까운 중복 임계값은 Swift/Kotlin 공통 `0.12`다.
- 유사한 묶음에서는 `sharpness + log(pixelCount) / 100` 점수가 높은 사진을 남긴다.
- 두 사진 사이 거리 자료가 없거나 임계값보다 크면 서로 다른 사진으로 보존한다.
- Vision, ML Kit, 원본 사진 디코딩과 전송 상한 처리는 앱 adapter 책임이다.

## 4. 이관 시 통일한 플랫폼 차이 (Codex 검증 포인트)

HanClip Apple과 Android 0.6.0 구현은 세부 동작이 달랐다. HanAI는 하나의 계약으로 통일했으며 두 앱은 이관 시 아래 차이를 흡수해야 한다.

| 항목 | HanClip Apple | HanClip Android | HanAI 계약 |
| --- | --- | --- | --- |
| 퍼팅 전진 스트로크 진입 | 주소 통과 샘플 1개 | 복귀 샘플 2개 + 주소 통과 | **Android** (보수적) |
| 퍼팅 팔로스루 확정 | 스트로크 샘플 포함 2개 | 팔로스루 샘플 2개 | **Android** (실제 팔로스루 2개) |
| 팔로스루 중 미달 샘플 | 즉시 리셋 | 0.9초 타임아웃까지 대기 | **Android** |
| 확정 후 재발동 금지 | 2초간 입력 무시 | 확정 시점에 2초 검사 | **Android** + latch API |
| 몸통 속도 리셋 | 주소 탐색 단계 제외 | 모든 단계 | **모든 단계** |
| 퍼팅 샘플 간격/스케일 리셋 | 있음 | 없음 | **있음** (스윙 자세 분석기와 동일) |
| 퍼팅 중간 단계 신뢰도 | 단계별 값 | 0 | **단계별 값** (Apple) |
| 스윙 융합 정렬 창 | 없음(다운스윙 창 0.42초에 의존) | `[−0.20, 0.32]` | **`[−0.20, 0.32]`** |
| 퍼팅 융합 입력 | 불리언(`hasRecentPose`, `isSceneStable`) | 초 단위 + `isReady`, `isTriggerPending` | **초 단위** (상수는 코어에) |
| 모션 분석기 `lastGlobalChangeTime` | 없음 | 있음 | **있음** |
| 모델 계보 케이스 | 0.1.0~0.6.0 | 0.4.0~0.6.0 | **0.1.0~0.7.0** (앱 적용은 다음 릴리즈) |
| 오디오 민감도 이름 | noisy/normal/quiet/automatic | Loud/Normal/Quiet/Auto | **noisy/normal/quiet/automatic** (adapter가 매핑) |

Apple `rankedImpactTimes`(오프라인 하이라이트 순위)는 골프 실시간 판정이 아니므로 이번 이관 범위에서 제외했다. 후속 작업에서 `Audio/`에 추가할 수 있다.

## 5. Adapter 책임 (코어에 넣지 않는 것)

| 책임 | Apple | Android |
| --- | --- | --- |
| 카메라 프레임 캡처, 저해상도 격자 차분 → `GolfSwingVisualSample` | AVFoundation, vImage/CoreImage | CameraX, RenderScript 대체(ByteBuffer 직접 계산) |
| 관절 추출 → `GolfSwingPoseSample` (손목 평균, 어깨·골반 중심, 어깨 폭) | Vision `VNDetectHumanBodyPoseRequest` | ML Kit Pose 또는 MediaPipe |
| PCM → `AudioImpactMetrics`, baseline/recent 추적 | AVAudioEngine | AudioRecord |
| 영상 디코딩(사후 분석) | AVAssetReader | MediaCodec/MediaExtractor |
| 시계 통일, `secondsSince…` 계산, ready/억제 구간 상태 | 앱 상태 | 앱 상태 |
| 저전력·발열 시 자세 분석 빈도 조절 | ProcessInfo thermal state | PowerManager |
| 촬영 시작·저장·UI | 앱 | 앱 |
| 이미지 특징 거리·선명도 추출 → `ImageSimilarityCandidate`/`ImagePairDistance` | Vision + 기기 내 픽셀 계산 | 앱 선택 이미지 특징 추출기 |

원본 프레임·관절 좌표·오디오는 adapter 안에서만 존재하며 로그·업로드·저장하지 않는다.

## 6. 테스트 전략

- `Fixtures/golf/*.json`과 `Fixtures/image/*.json`을 Swift XCTest와 Kotlin JUnit이 함께 읽는다(`Fixtures/README.md`).
- fixture는 상태기계 시퀀스, 오디오 판정, 두 융합 정책, 롤백 플래그를 덮는다.
- 새 동작은 fixture 케이스 추가 → 두 플랫폼 구현 → 두 테스트 통과 순서로 넣는다.
