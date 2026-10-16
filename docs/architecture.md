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
| `HanAIVersion` | `product = "0.4.0"`, `golfModel` | 제품 버전, 현재 골프 모델 문자열 |
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

### 3.8 다양성을 반영한 사진 선택 (0.4.0)

기존 `ImageSimilaritySelector`와 `ImageTopNSelector` 계약은 그대로 유지한다. 추가 API는
`ImageDiversitySelector.selectDiverse(candidates:distances:threshold:n:)`이며 Swift/Kotlin에서 의미가 같다.

- `ImageQualityCandidate`의 선명도·노출·선택적 미학 점수는 유한한 `0...1`, 픽셀 수는 양수다.
  Apple 원시 미학 점수 `-1...1`은 adapter에서 `(score + 1) / 2`로 변환한다.
- 중복 ID의 모든 입력과 비정상 수치는 제외하고 이유를 반환한다. 거리가 없으면 같은 묶음으로 합치지 않는다.
- 높은 품질·낮은 index 순으로 묶음을 만들되, 새 항목과 묶음의 **모든** 구성원의 거리가 임계값 이내여야 한다.
  같은 쌍의 유효 거리 입력이 충돌하면 큰 거리를 사용한다.
- 묶음별 대표를 먼저 선택하고 추가 구성원을 순서대로 채운다. 결과 장수는 `min(n, 유효 입력 수)`이며 최종 index는 입력 순서로 정렬한다.
- 일부 후보에 미학 점수가 없으면 전체 후보에서 미학 혼합을 사용하지 않는다. 제공된 잘못된 점수는 결측으로 숨기지 않는다.
- 기본 품질은 기존 정책과 같은 `sharpness × 0.4 + exposure × 0.2 + log(pixelCount) / 100`이다.
  전부 미학 점수가 있으면 `base × 0.4 + aesthetics × 0.6`을 쓴다. 가중치는 한양의 실제 선호도에 맞춰 검증된 값이라는 의미가 아니다.
- 임계값에 보편적인 기본값은 없다. 모델·리비전·전처리·거리 종류가 일치하는 adapter가 보정값을 전달한다.
  유효하지 않은 임계값이면 안전하게 단독 묶음으로 보존한다.

결과는 선택 index 외에 품질 순위, 선택 순서, 그룹 구성원, 대표/추가 구성원 역할과 제외 이유를 포함한다.
이 API는 같은 인물이나 주제라는 이유만으로 사진을 근접 중복으로 취급하지 않으며 원본을 삭제하지 않는다.

### 3.9 영상 프레임·구간 재선택 (0.4.0)

`VideoHighlightSelector.select(candidates:distances:durationSeconds:configuration:shotBoundarySeconds:)`는
기존 중요 장면 추출·랭킹 결과 또는 adapter의 프레임 후보를 받아 선택을 보완한다. 원본 영상이나 OS API는 받지 않는다.
`VideoFrameCandidate`는 index, 초 단위 시각, `0...1` 품질, 선택적 `0...1` 중요도를 갖는다.

| 설정 | 기본값 | 의미 |
| --- | --- | --- |
| `frameCount` | 필수 | 최대 프레임 수. 품질·간격 제약 때문에 적게 선택될 수 있음 |
| `similarityThreshold` | 필수 | adapter와 일치하는 보정 거리 |
| `minimumQuality` | 0.25 | 다양성 선택보다 먼저 적용하는 품질 하한 |
| `minimumSpacingSeconds` | 1 | 선택 프레임 사이 최소 시간 간격 |
| `localDiversityWindowSeconds` | 30 | 이 범위의 가까운 중복만 억제. 먼 시간의 재등장은 보존 가능 |
| `preRollSeconds` / `postRollSeconds` | 1 / 2 | 중심 프레임 앞뒤 여유 |
| `maxSegmentDurationSeconds` | 6 | 한 구간의 최대 길이 |
| `totalDurationBudgetSeconds` | 30 | 반환 구간 전체의 최대 길이 |

잘못된 설정과 비정상 후보를 구분해 반환한다. 모든 유효 후보가 중요도를 제공할 때만 중요도를 함께 사용하며,
실제 적용 여부는 `usedImportanceScores`다. 일부 결측 때문에 품질만 쓴 결과를 중요도 랭킹으로 표시하면 안 된다.
선택 프레임과 반환 구간은 별도다. 예산 때문에 구간이 빠진 중심 프레임은 `omittedSegmentFrameIndices`에 남는다.
구간은 영상 범위·선택적 컷 경계 안에서 겹침을 줄여 반환하지만, 의미 있는 사건의 시작·끝을 이해했다고 보장하지 않는다.

### 3.10 OS·기기와 특징 추출기 경계

한양의 기본 판정은 플랫폼 중립 수치 계약을 기준으로 한다. Swift와 Kotlin 구현은 같은 상태·상수·출력 의미를 유지한다.
특정 기기의 Vision 기능, Python, ffmpeg 또는 공개 모델 런타임을 공통 코어의 필수 의존성으로 만들지 않는다.

로컬 미디어 도구는 Python+ffmpeg/ffprobe로 특징을 만들고 별도 JVM 실행기로 동일 Kotlin 코어를 호출한다.
선택형 DINOv2/SigLIP2 adapter는 고정된 공개 모델을 로컬에서 사용한다. 원시 특징 벡터는 출력하지 않고,
거리·품질·관련도·선택 결과만 익명 index로 반환한다. 모델 관련도는 개인 기억의 중요도나 확률이 아니다.
기기별 디코더·모델 런타임 연결과 실제 기기 확인은 별도이며, 런타임의 OS 지원을 전체 기기 검증 완료로 표시하지 않는다.

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
