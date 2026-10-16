# 한양 0.4.0 사진·영상 고도화

2026-10-17 한국 시간 기준. 한양 안에서의 구현이며, HanClip이나 홈페이지의 변경·공개 배포는 포함하지 않는다.
기존의 중요 장면 추출·순위 기능을 유지하면서 아래 기능을 추가했다.

## 이번에 연결한 공개 기술

| 기술 | 실제 연결한 역할 | 보장하지 않는 것 |
| --- | --- | --- |
| [DINOv2-small](https://huggingface.co/facebook/dinov2-small) | 로컬 사진·영상 프레임의 384차원 시각 특징과 cosine 거리 | 모든 크롭·연사·다른 시점 사진의 정확한 중복 판정 |
| [SigLIP 2 base](https://huggingface.co/google/siglip2-base-patch16-224) | 로컬 768차원 시각 특징, 한국어 등 텍스트 질의와 프레임 관련도, 영상 재순위의 중요도 입력 | 개인에게 중요한 추억이나 사건 완결성을 이해하는 능력, 관련도 수치의 확률 해석 |
| [ONNX](https://onnxruntime.ai/docs/tutorials/mobile/) | DINOv2 모델의 고정 입력 그래프와 전처리 계약 내보내기 | 모든 기기의 실행·속도·배터리 검증 완료 |

모델은 공식 저장소의 고정 리비전과 가중치 SHA-256을 확인한다. 다운로드와 추론은 별도 명령이다.
추론은 로컬 모델만 읽고 원본·질의·특징 벡터를 외부로 보내지 않는다.
기본 색·타일 특징은 학습 모델과 다른 namespace다. 학습 모델이 실패해도 기본 특징으로 조용히 바꾸지 않는다.

SSCD, DINOv3, TransNetV2, MUSIQ/NIMA 등도 조사했지만 이번에 실행 연결한 모델로 표시하지 않는다.
각각 복사 탐지, 시각 백본, 컷 경계, 품질 평가에 맞는 후보이며 사용 목적·비용·라이선스·자료 평가를 따로 판단한다.
근거와 후보별 적용 한계는 [사진 연구](research/photo-selection-2026-10-17.md)와 [영상 연구](research/video-selection-2026-10-17.md)에 있다.

## 공통 판단 코어

- 사진: 품질 순서로 엄격한 근접 중복 묶음을 만든다. 묶음의 모든 구성원 사이 거리를 확인해 연결만으로 다른 사진이 합쳐지는 것을 막는다.
- 사진: 서로 다른 묶음의 대표를 우선하고, 요청 장수까지 추가 사진을 고른다. 제외 이유·그룹·품질 순위·선택 순서를 반환한다.
- 영상: 품질 하한 이후 중요도와 품질로 순위를 정하고, 가까운 시간의 중복과 지나치게 가까운 프레임을 억제한다. 먼 시간의 같은 장면은 다시 등장할 수 있다.
- 영상: 선택 프레임과 전후 구간을 구분한다. 구간은 영상 범위·선택적 컷 경계·최대 길이·총 길이 제한을 지키며, 구간에 포함하지 못한 중심 프레임도 보고한다.
- 결측 중요도를 만들어 넣지 않는다. 실제 중요도 혼합 여부는 `usedImportanceScores`로 확인한다.

기존 사진 선택·골프 API는 유지한다. 새 API와 기본값은 [아키텍처](architecture.md)의 3.8~3.10절을 따른다.
공통 코어는 Swift Foundation/Kotlin 표준 라이브러리 기반이며 Vision이나 Python·ffmpeg·PyTorch를 필수 의존성으로 갖지 않는다.

## 로컬 사용 준비

아래 명령은 저장소 루트 기준이다. 원본을 새 폴더로 옮기거나 복사할 필요 없이 명시한 로컬 파일을 읽는다.
모델·중간 결과는 Git에서 제외되는 `Work/` 아래에 보관한다.

1. Python 3.11 이상, ffmpeg/ffprobe, JVM 17 이상을 준비한다. Python 추론기는 데스크톱·서버용 adapter다.
2. 가상 환경을 만들고 선택형 모델 의존성을 설치한다. `python3 -m venv Work/media-runtime` 후 해당 환경의 Python으로 `-m pip install -r tools/hanai-media-selector/requirements-models.txt`를 실행한다.
3. `android/`에서 `./gradlew :hanai-media-cli:installDist`를 실행한다. Windows에서는 `gradlew.bat`를 쓴다. 테스트 실행 명령은 포함하지 않는다.

아래의 `PYTHON`은 만든 가상 환경의 Python 실행 파일을 뜻한다. Unix 계열은 `Work/media-runtime/bin/python`, Windows는 `Work\media-runtime\Scripts\python.exe`다.

```text
PYTHON tools/hanai-media-selector/learned_features.py download --backend dinov2 --destination Work/models/dinov2-small
PYTHON tools/hanai-media-selector/learned_features.py download --backend siglip2 --destination Work/models/siglip2-base
```

DINOv2 가중치는 약 88MB, SigLIP 2는 약 1.5GB다. 전체 런타임·토크나이저·중간물 공간은 별도다.
Linux CPU 환경의 PyTorch 설치 방법과 OS별 런타임 조건은 `requirements-models.txt`의 안내를 따른다.
모델 다운로드는 최초 준비 작업이며 원본 미디어 전송이 아니다.

## 사진 선택

```text
PYTHON tools/hanai-media-selector/analyze_media.py images --image LOCAL_PHOTO_1 --image LOCAL_PHOTO_2 --embedding-backend dinov2 --model-dir Work/models/dinov2-small --engine-home android/hanai-media-cli/build/install/hanai-media-cli --count 2 --threshold CALIBRATED_DISTANCE
```

`--image`를 반복해 입력한다. 원본의 순서에 해당하는 index로 결과를 연결한다.
`--threshold`는 선택한 모델·전처리·거리별 보정값이며 보편적인 숫자를 제공하지 않는다.
모델을 바꾸고 예전 임계값을 그대로 재사용하면 안 된다. 결과는 추천이며 원본을 수정·삭제하지 않는다.
SigLIP 질의를 사진에 사용하면 관련도 수치는 출력되지만 사진의 최종 대표 선택 점수를 질의 점수로 대체하지 않는다.

## 설명과 맞는 영상 프레임·구간 찾기

```text
PYTHON tools/hanai-media-selector/analyze_media.py video --video LOCAL_VIDEO --embedding-backend siglip2 --model-dir Work/models/siglip2-base --query "골프채를 휘둘러 공을 치는 사람" --engine-home android/hanai-media-cli/build/install/hanai-media-cli --count 5 --threshold CALIBRATED_DISTANCE --budget 15
```

전체에서 제한된 수의 프레임을 먼저 분석하고, 좋은 후보 주변을 더 촘촘히 읽는다. `--max-frames`, `--interval`, `--refine`으로 범위를 조절한다.
전체 프레임을 읽는 모델이 아니므로 아주 짧은 사건은 놓칠 수 있다. 컷 경계·자세·오디오·기존 중요도 등 다른 근거는 adapter에서 코어에 공급할 수 있다.
질의 없는 경로는 기술 품질 기반이며 `technical-quality-only`, 실제 텍스트 관련도를 얻은 경로는 `text-guided-relevance`로 표시한다.

출력 JSON의 `selectionOrderIndices`는 선택 우선순위, `selectedFrameIndices`는 시간순, `segments`는 구간이다.
`omittedSegmentFrameIndices`, `counts.failed`, `status=partial`도 확인해야 한다. 좋은 중심 프레임 하나를 골랐다고 사건 전체를 온전히 담은 것은 아니다.
원본 경로·파일명·특징 벡터·질의는 결과 JSON에 넣지 않는다.

## 기기·OS에 독립적인 모델 계약

```text
PYTHON tools/hanai-media-selector/learned_features.py export-onnx --backend dinov2 --model-dir Work/models/dinov2-small --output Work/models/dinov2-small.onnx
```

그래프 옆의 `.onnx.json`에 입력 `[1,3,224,224]` float32 RGB/NCHW, 전처리, 출력 `[1,384]` L2 정규화, 그래프 해시를 남긴다.
이 계약으로 다른 기기의 모델 런타임이 같은 종류의 특징을 만들어 코어에 전달할 수 있다.
ONNX Runtime이 Android/iOS를 지원한다는 사실과 각 앱의 모델 포장·디코더 연결·실기기 확인은 구분한다. 이번 작업은 앱별 배포를 포함하지 않는다.

## 정확도를 높이기 위한 다음 판단 기준

원본 복사·연사·같은 피사체의 다른 순간·주제만 비슷한 사진을 분리해 정답을 만든다.
영상은 원하는 순간, 불필요한 순간, 놓친 순간, 구간 시작/끝을 따로 기록한다.
같은 원본의 변형과 같은 촬영 세션을 보정·평가 양쪽에 섞지 않고, 다른 사진을 잘못 합치는 비율과 중요한 순간의 누락을 우선 본다.
개인별 선호와 기기 성능은 실제 자료로 확인해야 한다. 공개 모델의 논문 성적을 한양의 향상률로 옮겨 말하지 않는다.

## 이번 작업에서 실제 확인한 범위

2026-10-17 00:38 KST, Codex Desktop 팀장 확인.

| 항목 | 확인 결과 |
| --- | --- |
| 제품 버전 | Swift/Kotlin/Gradle 0.3.1 → 0.4.0. 골프 모델 계보는 유지 |
| Swift | 최종 `swift build` 성공 |
| Kotlin/JVM | 최종 코어 컴파일 및 CLI `installDist` 성공, JVM 17 대상 |
| 공개 모델 | DINOv2-small·SigLIP 2 base 고정 리비전 다운로드 및 공개 가중치 SHA-256 대조 성공 |
| 사진 | 기존 로컬 프레임 8개에서 DINOv2 특징 추출 8개 성공. 같은 수치 입력으로 최종 코어가 4개 그룹·4장 선택 |
| 영상 | 기존 로컬 4초 클립에서 12개 프레임 추출·SigLIP 한국어 질의 관련도 계산 성공. 최종 선택 프레임 1개, 구간 1개, `usedImportanceScores=true` |
| 모델 호환 형식 | DINOv2 ONNX 내보내기와 전처리 메타데이터 생성 성공. 기존 로컬 프레임의 ONNX Runtime CPU 추론에서 유한한 `[1,384]` 출력 확인 |
| 원본 보호 | 새 로컬 처리 경로의 파일 입력·프로토콜·외부 전송·로그·덮어쓰기 경계를 TM이 1회 소스 확인. URL 입력을 막고 `file,pipe`만 허용하도록 수정 |

최초 실행에서 발견한 Kotlin TSV 음수 split 제한, 영상 끝 시각 샘플링, 컷 경계의 길이 0 구간과 구간 누락 보고를 수정했다.
그 뒤 필요한 소스만 재빌드하고 원래 사진·영상 분석을 재개했다. 작업 전부터 있던 변경·원본은 보존했다.

위 수치는 실행 확인용 처리 건수이며, 정확도·재현율·선호도 향상률이 아니다.
별도의 정답 평가·벤치마크·테스트 러너는 실행하지 않았다. 모든 OS·기기의 실기기 실행을 확인한 것이 아니며,
Android/iOS 앱의 모델 포장·adapter 연결, HanClip 적용, 커밋·푸시·공개 배포는 이번 작업에 포함하지 않았다.

## 후속 실제 자료 비교와 의미 순위 조정

대표님의 기존 자료 비교 요청에 따라 이후 정답 기록을 대조하고 필요한 수치 검사를 실행했다.
보관 영상 조각 716개에서 특징 추출이 모두 성공했다. 조정 세션과 다른 평가 세션을 나누었으며,
Gemini에는 조정 세션의 익명 숫자만 제공했다. 자세한 분모와 한계는
[실제 자료 비교 보고](research/media-evaluation-2026-10-17.md)에 기록했다.

새 텍스트 질의 adapter는 품질 기준을 통과한 후보에서 관련도를 우선할 수 있다.
`video --importance-weight 0.3`은 기존 가중치이며, 질의가 있는 SigLIP2 모드의 기본 가중치는 1이다.
질의가 없으면 기존 품질 중심 정책을 유지한다. Swift와 Kotlin의 코어 기본값 자체는 0.3이다.

관련도 우선으로 새 adapter의 순위는 개선됐지만, 저장된 기존 퍼팅 전용 최종 순위보다 낮았다.
따라서 기존 골프·퍼팅 전용 엔진을 대체하지 않는다. 준비 자세·이동 장면의 정적 의미 일치는
실제 스트로크 확정이 아니며, 이번 결과를 영상 전체 검출률로 해석하지 않는다.

## 전용 분석 결합·온라인 조사 후속

대표님의 삼대장·Exa·유튜브 참고 요청을 반영해 Claude와 Gemini의 실제 작업 경로 및 Exa 검색을 함께 사용했다. 추가 기록은 다음과 같다.

- [전용 점수와 새 모델 결합 실험](research/fusion-comparison-2026-10-17.md): 단순 RRF·정지 화면 결합·8프레임 X-CLIP을 비교했다. 조정 자료의 향상이 다른 세션에서 유지되지 않은 후보는 채택하지 않는다.
- [공개 영상 기술·유튜브 참고 조사](research/temporal-public-research-2026-10-17.md): 공식 논문·구현·가중치 라이선스와 실제 열람한 범위를 구분한다.
- [사진 검색과 중복 검증 조사](research/photo-verification-research-2026-10-17.md): 의미 유사도와 중복 확정을 구분하며, 배경만 같은 사진의 잘못된 병합을 경계한다.
- [사진 네이티브 실행·실제 비교](research/native-parity-2026-10-17.md), [Android 로컬 추론 연결 조사](research/android-ort-pin-2026-10-17.md): 직접 공유와 별도 포트를 구분한다. 모델 파일 형식의 존재가 기기 추론 성공이나 앱 반영을 뜻하지 않는다.

앱 반영과 릴리즈는 요청된 후속 범위에 포함돼 있다. 다만 실제 성능 개선 및 해당 앱 입력·실행 경로의 일치가 먼저이며, 지금까지의 조사·실험을 앱 공개 완료로 처리하지 않는다.
