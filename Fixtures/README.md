# Fixtures

Swift(`Tests/HanAITests`)와 Kotlin(`android/hanai-core/src/test`)이 **같은 파일**을 읽는 공유 검증 fixture다.
모든 값은 합성 수치이거나 익명화된 특징값이며, 원본 영상·사진·프레임·관절 좌표는 포함하지 않는다.

## 공통 헤더

```json
{
  "schemaVersion": 1,
  "golfModelVersion": "0.7.0",
  "kind": "swingMotion | swingPose | puttStroke | audioImpact | swingFusion | puttFusion",
  "description": "...",
  "defaults": { ... },   // kind에 따라 선택
  "cases": [ ... ]
}
```

`golfModelVersion`은 fixture가 검증하는 모델 계보다. 테스트는 이 값이 `GolfModelVersion.current`와 같은지 먼저 확인한다.
모델을 롤백하거나 올릴 때는 fixture도 함께 갱신한다.

## kind별 스키마

| kind | 입력 | expect |
| --- | --- | --- |
| `swingMotion` | `samples[]` = `GolfSwingVisualSample` (`defaults`로 생략 필드 보충) | `phase`, `isImpactWindow`, `minConfidence?`, `impactTime?`, `lastGlobalChangeTime?` |
| `swingPose` | `samples[]` = `GolfSwingPoseSample` (`defaults` 보충) | `phase`, `minConfidence?`, `impactWindowStart?`, `impactWindowEnd?`, `isImpactWindowAt[]?`, `notImpactWindowAt[]?` |
| `puttStroke` | `samples[]` = `GolfSwingPoseSample` (`defaults` 보충) | `phase`, `isConfirmedStroke`, `minConfidence?`, `strokeTime?`, `latchedAt[]?`, `notLatchedAt[]?` |
| `audioImpact` | `metrics{rms,peak,crossingRate}`, `baseline`, `previousRecentLevel`, `sensitivity` | `isTriggered`, `confidenceIsZero?`, `minConfidence?` |
| `swingFusion` | `decision`, `metrics`, `motion`, `pose|null`, `referenceTime`, `requiresPoseConfirmation`, `hasRecentVisualFrame`, `isInsideReadyPromptWindow`, `modelVersion` | `true|false` |
| `puttFusion` | `stroke|null`, `poseObservationConfidence`, `secondsSinceLatestPose`, `secondsSinceLatestVisualFrame`, `secondsSinceLastGlobalChange`, `isReady`, `isInsideReadyPromptWindow`, `isTriggerPending`, `modelVersion` | `true|false` |

`latchedAt`은 순서대로 먼저 호출하고 `notLatchedAt`은 그 뒤에 호출한다(latch는 만료 시 비워지므로 순서가 중요하다).

## 새 fixture 추가 규칙

- 실제 영상에서 뽑은 값을 넣을 때도 파일명·해시·촬영 시각·사람 식별 정보는 넣지 않는다.
- 아이언·풀스윙에서 뽑은 시퀀스는 퍼팅 fixture에서 항상 `expect.isConfirmedStroke: false`(negative label)여야 한다.
- 두 플랫폼 테스트가 모두 통과해야 fixture를 확정한다.
