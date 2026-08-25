# HanAI

HanAI는 영상·사진에서 "기억할 순간"을 찾는 재사용 가능한 판정 엔진이다. 특정 앱에 종속되지 않으며,
플랫폼 카메라·오디오·관절 추출은 앱 쪽 adapter가 맡고 HanAI는 **순수 수치 입력 → 판정 출력**만 담당한다.

대표님이 부르는 이름은 **한양**, 제품·코드·패키지의 공식 표기는 **HanAI**다.

- 제품 버전: `0.1.0`
- 골프 판정 모델 계보(AiShot): `0.7.0` (`GolfModelVersion.current`)
- 첫 기능: **AiShot** — 골프 스윙 자동 촬영(충격음 + 화면 움직임 + 자세 결합)과 무음 퍼팅 안전망

## 저장소 구조

```
HanAI/
├── Package.swift                 # Swift Package (Foundation만 사용)
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
| 오디오 | `AudioImpactMetrics`, `AudioImpactClassifier.detectImpact` | 실시간 충격음 판정(준비 음성 억제 포함) |
| 화면 움직임 | `GolfSwingMotionAnalyzer` | 정지 → 백스윙 → 다운스윙 상태기계, 전역 변화 기록 |
| 자세 스윙 | `GolfSwingPoseAnalyzer` | 정지 → 손 이동 → 빠른 복귀 → 임팩트 창 |
| 무음 퍼팅 | `GolfPuttStrokeAnalyzer` | 정지 → 작은 백스윙 → 전진 스트로크 → 팔로스루 확정, 아이언 오탐 방지 |
| 융합 | `GolfSwingFusionPolicy`, `GolfPuttFusionPolicy` | 최종 촬영 여부 결정 |

상세 계약과 상수는 [docs/architecture.md](docs/architecture.md), 앱 연결은 [docs/integration.md](docs/integration.md)를 본다.

## 현재 상태

- 0.6.0 골프 로직을 HanClip Apple/Android 구현에서 이관했다. 두 앱은 현재 같은 0.6.0 동작을 자체 코드로 사용하며, 패키지 직접 소비 전환은 무중단 단계적 이관 대상으로 남아 있다.
- HanAI·HanClip Apple·HanClip Android는 하나의 동기화 그룹으로 등록되어 있지만 한양은 먼저 독립적으로 학습하고 성장한다. `마무리`는 HanAI 소스 검토·검증·커밋·푸시까지이며, 대표님의 `릴리즈` 지시가 있을 때만 검증된 기능을 두 앱의 adapter·호출 경로·테스트에 적용한다.
- 무음 퍼팅 안전망은 대표 퍼팅 클립으로 검증되지 않은 **보수적 설계 상태**다. 학습 근거는 [docs/learning/README.md](docs/learning/README.md)에 있다.
