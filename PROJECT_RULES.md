# HanAI 프로젝트 규칙

이 문서는 HanAI 저장소에서 작업하는 사람과 자동화 도구 모두에게 적용된다.

## 1. 개인정보 경계 (가장 중요)

1. 원본 영상·사진·프레임·오디오·관절 좌표는 **기기 또는 로컬에서만** 처리한다. 저장소, 이슈, 문서, 로그, 원격 서비스 어디에도 올리지 않는다.
2. 저장소에 남길 수 있는 것은 다음뿐이다.
   - 정규화된 수치 특징(움직임 비율, 몸 크기 대비 손 위치, 오디오 rms/peak/crossingRate 등)
   - 라벨(예: `full-swing-positive`, `iron-negative-for-putt`)
   - 검증 fixture(JSON)와 통계 요약(평균·최대·범위·개수)
3. 파일명, 자산 해시, 촬영 시각, 위치, 사람·계정 식별 정보는 어떤 형태로도 남기지 않는다. 기존 학습 JSON을 이관할 때는 이런 필드를 제거한 **소형 provenance 요약**만 옮긴다.
4. `Inbox/`(원본 투입함)와 `Work/`(분석 중간물)는 로컬 전용이며 `.gitignore`가 차단한다. 예외를 만들지 않는다.
5. `~/Downloads`, Photos 보관함 등 사용자 개인 폴더를 임의로 탐색하지 않는다. 다만 대표님이 대화에서 직접 전달하거나 경로를 지정한 학습 파일은 그 파일과 소속 폴더에 대한 로컬 읽기 승인이 함께 주어진 것으로 보고, `Inbox/`로 옮겨 달라고 다시 요청하지 않는다. 이 승인은 원본의 외부 전송·저장소 반입·식별정보 기록까지 허용하지 않는다.
6. 도구·스크립트 출력에 원본 경로가 포함되면 안 된다. 요약 파일에는 `"identity": { "assetHash": "removed", "fileName": "removed" }`처럼 제거 사실만 남긴다.

## 2. 이름과 브랜드

1. 제품·패키지·모듈 이름은 `HanAI`다. Swift 모듈 `HanAI`, Kotlin 패키지 `com.hanai.core`.
2. 대표님이 말씀하시는 `한양`은 항상 이 프로젝트 `HanAI`를 뜻한다. 대화에서는 `한양`, 제품·코드·패키지 표기에서는 `HanAI`를 쓴다.
3. `AiShot`은 HanAI가 제공하는 **골프 기능 이름**으로만 쓴다. 엔진·모델·타입 이름에 `HanClip` 등 특정 앱 브랜드를 넣지 않는다.
4. 골프 관련 타입은 `Golf…` 접두어, 오디오 관련 타입은 `AudioImpact…` 접두어를 쓴다.

## 3. 의존성과 범위

1. Swift 코어는 Foundation 외 의존성이 없다. Kotlin 코어는 Kotlin 표준 라이브러리 외 런타임 의존성이 없다(테스트는 JUnit 4만).
2. Android SDK, Compose, Vision, AVFoundation, CoreML, 서버, ML 학습 프레임워크, UI를 코어에 넣지 않는다. 이런 것은 앱 쪽 adapter의 책임이다(`docs/architecture.md`).
3. 카메라·마이크·관절 추출 결과를 **정규화 수치 샘플**로 바꾸는 코드는 adapter에 둔다. 코어는 `GolfSwingVisualSample`, `GolfSwingPoseSample`, `AudioImpactMetrics`만 받는다.
4. 앱 저장소에 HanAI의 절대 경로 의존성을 만들지 않는다. 배포는 태그된 Git 패키지(Swift) 또는 Maven 아티팩트/composite build(Kotlin)로만 한다.

## 4. 두 플랫폼 계약 동일성

1. Swift와 Kotlin은 같은 public 타입 이름, 같은 상수, 같은 상태 의미를 가져야 한다. 한쪽만 바꾸는 변경은 금지한다.
2. 동작 변경은 반드시 `Fixtures/golf/*.json`에 케이스를 추가하거나 갱신하고, 두 플랫폼 테스트가 모두 통과해야 한다.
3. 상수(임계값, 시간 창, 신뢰도 하한)를 바꾸면 `docs/architecture.md`의 상수 표를 같이 고친다.
4. fixture의 `golfModelVersion`은 `GolfModelVersion.current`와 같아야 한다. 테스트가 이를 강제한다.
5. HanAI는 `project-sync`의 `hanclip` 그룹에서 공유 코어 역할을 맡지만, 학습·진화·성능 향상은 기본적으로 HanAI 안에서만 진행한다. 검증된 변경을 HanClip Apple과 HanClip Android에 즉시 복사하거나 연결하지 않는다.
6. 대표님이 `마무리`라고 하면 HanAI 소스 검토와 관련 검증을 끝내고 이번 변경만 커밋·기본 원격 푸시한다. 대표님이 `릴리즈`라고 하면 그때 두 앱의 adapter·호출 경로·테스트에 함께 반영하고 각각 검증한다. 공개 배포는 별도 지시가 있어야 한다.
7. `HanAI 구현 완료`, `HanAI Git 백업 완료`, `HanClip 적용 완료`, `공개 배포 완료`를 구분해 보고한다. 앱별 검증 전에는 한양의 새 기능이 한클립에 적용됐다고 표현하지 않는다.

## 5. 모델 버전과 롤백

1. 제품 버전(`HanAIVersion.product`)과 골프 모델 계보(`GolfModelVersion`)는 별개로 관리한다.
2. 새 모델 계보를 추가할 때는 케이스·제목·출시일·요약·기능 플래그를 두 플랫폼에 같이 추가한다.
3. 롤백은 `GolfModelVersion.current`를 이전 케이스로 바꾸는 것으로 수행한다. 정책 함수는 `modelVersion` 매개변수로 항상 이전 버전을 흉내 낼 수 있어야 한다.
4. 대표 데이터로 검증되지 않은 기능은 문서에 "검증되지 않은 안전망 설계 상태"라고 정직하게 적는다.

## 6. 학습 자료

1. 학습 메모는 `docs/learning/README.md`에 날짜별로 남기고, 수치 요약은 같은 폴더의 JSON으로 둔다.
2. 라벨 정정(예: "퍼팅이 아니라 아이언")은 사실 그대로 보존한다. 아이언·풀스윙 자료는 퍼팅의 **negative label**이며 퍼팅 양성 자료로 재해석하지 않는다.
3. 대규모 과거 학습 JSON 전체는 복사하지 않는다. 필요한 통계만 요약한다.

## 7. Git과 작업 방식

1. 자동화 도구는 Git 명령(커밋·푸시·브랜치), 설치, 배포, 네트워크 사용을 명시적 지시 없이 수행하지 않는다.
2. 미디어 확장자와 `Inbox/`, `Work/`는 `.gitignore`로 차단되어 있다. 커밋 전 `git status`에서 미디어 파일이 보이면 규칙 위반이다.
3. 외부 앱 저장소(HanClip 등)는 읽기 전용 참고 자료다. HanAI 학습·마무리 작업에서는 앱 저장소를 수정하지 않는다. 대표님의 `릴리즈` 지시가 있을 때만 해당 앱 규칙과 `project-sync` 절차에 따라 수정한다.
