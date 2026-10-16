# 영상 구간 검색: 공개 시간 모델 조사와 실행 판단

확인: 2026-10-17 02:41 KST. 공개 검색·원문 읽기만 수행했다. 이 조사 담당자는 모델 가중치·원영상을 다운로드하거나 모델을 실행하지 않았다. 다른 담당자의 실제 실행 결과는 별도 실행 기록을 기준으로 한다.

## 실행 판단

먼저 Microsoft X-CLIP B/32의 8프레임을 같은 시간창·같은 정답으로 비교하는 것이 합리적이다. 단일 사진의 주제 점수를 결합하는 대신 프레임 간 정보를 처리한다는 차이가 있다. 그러나 행동 분류 점수가 곧 좋은 추억이나 정확한 시작·종료 시각이라는 근거는 없다. 제품 정확도 향상은 아직 이 조사에서 측정하지 않았다.

32프레임 zero-shot 체크포인트는 별도 실험으로 둔다. 더 최신인 PE-Core는 작고 유용한 비교 후보지만 프레임 평균 방식이므로 순서 이해 검증을 대신하지 못한다. MobileCLIP2는 공식 가중치 조건 때문에 제품 후보에서 제외한다. 실제 구간 경계가 필요하면 Moment-DETR/UniVTG의 문제 정의를 참고하되, 무거운 전처리·GPU 코드·데이터 조건까지 포함한 별도 도입 판단이 필요하다.

## X-CLIP: 같은 이름과 체크포인트 구분

대상은 Microsoft의 ECCV 2022 논문 [Expanding Language-Image Pretrained Models for General Video Recognition](https://arxiv.org/html/2208.02816v1)과 [공식 VideoX/X-CLIP](https://github.com/microsoft/VideoX/tree/master/X-CLIP)이다. Cross-frame communication, multi-frame integration transformer, video-conditioned text prompting을 사용한다. [xuguohai/X-CLIP](https://github.com/xuguohai/X-CLIP)은 다른 저자·ACM MM 2022 비디오 검색 논문이며 성능 수치·체크포인트를 섞으면 안 된다.

| 항목 | microsoft/xclip-base-patch32 | microsoft/xclip-base-patch16-zero-shot |
| --- | --- | --- |
| 입력 계약 | 8 × 224 × 224, patch32 | 32 × 224 × 224, patch16 |
| 공식 모델 동기 | Kinetics400 학습 B/32 | Kinetics400 학습 후 unseen 분류 평가용 B/16 |
| 원 논문 FLOPs | 39G/view | 571G/view |
| safetensors 크기 | 786,414,772 bytes | 779,838,804 bytes |
| HF 고정 revision | a2e27a78a2b5d802e894b8a1ef14f3a8ce490963 | 6746d6a5202bf24300cd9bf36f457f174483d41b |
| SHA256 | abf286e8cdd0612761c3e42d3a55eca998382dfa67a04a0f3fdcdfa4f150cdbb | 0057077dd2bb6a5852fc9150f6e08f866448f4928f0acd7b267007f22a6adaf7 |

크기·revision·해시는 공식 [B32 API 메타데이터](https://huggingface.co/api/models/microsoft/xclip-base-patch32?blobs=true), [B16 zero-shot API 메타데이터](https://huggingface.co/api/models/microsoft/xclip-base-patch16-zero-shot?blobs=true)를 직접 읽었다. 모델 카드는 [B32](https://huggingface.co/microsoft/xclip-base-patch32), [B16 zero-shot](https://huggingface.co/microsoft/xclip-base-patch16-zero-shot)이며 HF 팀이 작성했다고 밝힌다. 원 저장소 코드 LICENSE와 카드의 MIT 표기를 확인했으며 데이터·영상의 권리까지 이 표기로 확대하지 않는다.

571/39 ≈ 14.64는 계산량 비율이며 CPU 시간 비율이 아니다. 원 논문의 B32 K400 수치는 4 temporal clips × 3 spatial crops 평가를 사용하고 zero-shot 절은 single-view32frame 설정이다. 체크포인트·평가 뷰·학습 과제가 다르므로 공개 top1 수치를 직접 비교해 제품 향상을 예측하지 않는다. 가중치 파일 크기가 비슷해도 추론 비용과 peak RAM은 다를 수 있다.

공식 저장소에는 CPU 선택 및 float 변환 경로가 있지만 학습 환경은 Python3.7/Apex/CUDA 중심이다. 구형 학습 설치 절차를 그대로 가져올 필요는 없다. 현재 [Transformers XCLIP 문서](https://huggingface.co/docs/transformers/model_doc/xclip)와 [v5.20.1 model 소스](https://github.com/huggingface/transformers/blob/v5.20.1/src/transformers/models/x_clip/modeling_x_clip.py), [processor 소스](https://github.com/huggingface/transformers/blob/v5.20.1/src/transformers/models/x_clip/processing_x_clip.py)를 읽었다.

- `XCLIPModel` 입력 `pixel_values`: batch, frames, channels, height, width. 전체 forward의 `logits_per_video`가 비디오-텍스트 점수다.
- 최신 `get_video_features`는 `BaseModelOutputWithPooling`/tuple 반환 경로여서 이전 예제처럼 무조건 Tensor로 가정하지 않는다.
- 공식 processor는 resize224·center crop·RGB normalization(mean .485/.456/.406, std .229/.224/.225)이다. 모델별 실제 로컬 processor와 frame count를 고정해야 한다.
- 텍스트 점수 softmax는 후보 문장 집합 안의 상대 비교다. 문장 하나면 항상1이므로 중요도 확률로 사용하지 않는다. 비디오 조건부 text prompt 경로를 생략하는 단순 고정 text embedding 내적도 원 모델 forward와 같다고 가정하지 않는다.
- 현재 지원 소스 확인과 실제 checkpoint 로딩·ONNX 변환·Android/iOS 실행 성공은 서로 다른 증거다. 이번 조사에서는 후자를 확인하지 않았다.

## YouTube와 공식 데모에서 실제 확인한 범위

1. [저자 Jie Lei의 QVHighlights NeurIPS 발표](https://www.youtube.com/watch?v=XwYD3iovHtc): YouTube 공개 HTML의 제목, 저자, 13분6초 길이, 논문으로 연결되는 설명을 읽었다. **실제 시청하지 않았다.** 영어 자동자막 track 메타데이터는 있었지만 공식 caption URL을 json3/vtt로 요청한 두 응답이 HTTP200·0bytes여서 자막 본문은 확보하지 못했다. 발표 내용에 대한 근거는 아래 공식 논문·저장소 본문이다.
2. [NeurIPS 공식 포스터](https://neurips.cc/virtual/2021/poster/27962)와 연결된 [SlidesLive 발표](https://slideslive.com/38968159/qvhighlights-detecting-moments-and-highlights-in-videos-via-natural-language-queries)의 소개·초록을 읽었다. 동영상·발표 자막은 읽지 않았다.
3. [NielsRogge의 X-CLIP YouTube 분류 노트북](https://github.com/NielsRogge/Transformers-Tutorials/blob/master/X-CLIP/Zero_shot_classify_a_YouTube_video_with_X_CLIP.ipynb)은 실제 코드 본문을 읽었다. 저자 발표 영상이 아니라 YouTube 영상 한 개를 분류하는 예제다. yt_dlp 셀은 실행하지 않았다. B16-zero-shot으로 source128frames 범위에서32frames를 추출해 세 문장과 비교하며, 긴 영상 전체를 스캔하거나 시작·끝을 검출하지 않는다. 저장된 실행 출력도 없어 성능 측정 근거로 삼지 않는다.
4. Microsoft README가 연결한 저자 [X-CLIP slides.pptx](https://github.com/nbl97/X-CLIP_Model_Zoo/releases/download/v1.0/xclip-slides.pptx)는 링크 존재만 확인했다. 파일·본문은 읽지 않았다.

## 구간 경계를 직접 다루는 모델

| 후보 | 공식 원문에서 확인한 기능 | 실제 적용의 남은 비용 |
| --- | --- | --- |
| [Moment-DETR/QVHighlights](https://github.com/jayleicn/moment_detr), [논문](https://arxiv.org/abs/2107.09609) | 질의에 맞는 시간 구간 좌표와 saliency 예측. 공개 raw-video demo는 CLIP-only feature 방식도 제공한다. | benchmark SlowFast+CLIP recipe와 demo를 구분해야 한다. default CUDA·구형 환경이며 CPU 비용 미측정. 코드 MIT와 annotation CC BY-NC-SA4.0가 다르므로 데이터/가중치별 조건 확인 필요. |
| [UniVTG](https://github.com/showlab/UniVTG), [ICCV2023 원문](https://openaccess.thecvf.com/content/ICCV2023/html/Lin_UniVTG_Towards_Unified_Video-Language_Temporal_Grounding_ICCV_2023_paper.html) | moment retrieval, highlight detection, summarization을 구간·곡선·점 형태로 통합한다. | 공식 Gradio 소스는 `.cuda()` 사용. 영상 feature 추출을 별도 선행한다. README의 10분영상 grounding <1초 주장은 전체 CPU decode+feature+grounding 시간으로 사용할 수 없다. 코드 MIT, 정확한 배포 가중치 크기·조건은 미확인. |

[UniVTG 실제 demo 소스](https://github.com/showlab/UniVTG/blob/main/main_gradio.py)는 feature 추출→저장→로드→grounding 단계가 분리된다. 외부 Gradio에 개인 영상을 업로드하는 도입을 제안하지 않으며, 향후 실험도 로컬 처리로 제한한다. 두 모델의 공개 benchmark는 개인 기억의 중요도를 측정한 결과가 아니다.

## 더 최신인 비교 후보 2개

| 후보 | 확인된 성격·비용·조건 | 판단 |
| --- | --- | --- |
| Meta [PE-Core-T16-384](https://huggingface.co/facebook/PE-Core-T16-384) | [공식 API](https://huggingface.co/api/models/facebook/PE-Core-T16-384?blobs=true): revision `8b4722741a516c18e7d311f158f13c600fea6c78`, `.pt` 278,284,008bytes. 공식 PE 전용 Apache2.0. | 영상 학습을 한 작은 의미 특징 후보. 순서 모델 대체가 아니라 이후 비교군이다. |
| Apple [MobileCLIP2-S0](https://huggingface.co/apple/MobileCLIP2-S0) | [공식 API](https://huggingface.co/api/models/apple/MobileCLIP2-S0?blobs=true): revision `3136ea51c8ed56b9f9abfab04cb816735aaad6cb`, `.pt` 299,864,852bytes. 이미지-텍스트 모델. | 제품 개발을 제외하는 가중치 이용 조건 때문에 제품 후보에서 제외한다. |

PE의 [공식 논문 §2](https://proceedings.neurips.cc/paper_files/paper/2025/file/57bc0a850255e2041341bf74c7e2b9fa-Paper-Conference.pdf)는 영상8프레임 각각을 인코딩하고 평균해 text에 정렬한다고 설명한다. **추론:** 이 평균 집계만으로는 프레임 순서 변경을 구분할 수 없으므로 앞뒤 동작 순서 구분이 목표인 실험을 대체하지 않는다. [공식 README](https://github.com/facebookresearch/perception_models/blob/main/apps/pe/README.md)는 작은 T/S와 timm 통합 경로를 제공하지만 이 조사에서는 CPU 실행·현재 Transformers 직접지원·export를 확인하지 않았다. 원 HF B16은 1,790,786,632bytes로 오히려 B32보다 크다. PE와 같은 저장소의 PLM 조건을 섞지 않고 [LICENSE.PE](https://github.com/facebookresearch/perception_models/blob/main/LICENSE.PE)를 확인했다.

MobileCLIP2의 [Apple 공식 소개](https://machinelearning.apple.com/research/mobileclip2)는 이미지-텍스트 경량화를 설명한다. 공식 [LICENSE_MODELS](https://github.com/apple-aiml-research/ml-mobileclip/blob/main/LICENSE_MODELS)는 비상업 연구용이며 제품 개발·상용 제품 사용을 Research Purposes에서 제외한다. 코드 MIT 표기로 모델 조건을 대체할 수 없다. 공개 iPhone latency를 다른 CPU나 영상 처리 시간으로 옮기지 않는다.

## 다음 비교의 최소 계약 — 제안, 아직 실행 전

1. 동일한 고정 시간창과 정답 구간으로 기존 expert, 정적 semantic, XCLIP B32를 비교한다. 각 창의 8개 실제 timestamp·순서·반복 여부를 기록한다. 짧은 창 보간/반복 정책도 고정한다. 랜덤 한 구간만 뽑는 데모를 긴 영상 전체 평가로 사용하지 않는다.
2. 같은 사람·배경·장비의 준비/동작/마무리/대기를 hard negative로 포함한다. 질의와 대조 문장 집합은 calibration에서 고정하고 holdout 결과를 보고 바꾸지 않는다. 영상·사건 단위로 분리해 인접창 누수를 막는다.
3. 행동 검출 top1 hit·구간 IoU/recall, 좋은 프레임의 미적 품질, 개인 중요도는 따로 판정한다. 단순 점수 혼합보다 실제 오선택 감소가 있는지 본다. decode·전처리·모델 추론·전체 지연과 peak RAM도 구분한다. 개선이 없으면 새 모델/결합 복잡도를 채택하지 않는다.

## 조사 실행·접근 한계

이번 temporal 조사에서 실제 Exa 검색 **6회**, Exa fetch **7회**를 수행한 뒤 일반 웹 검색으로 교차확인하고 공식 논문·repo·모델 API·소스 원문을 읽었다. Exa 글자 제한으로 잘린 페이지는 필요한 부분을 공식 raw HTML/소스/API로 보완했다. 웹 도구의 일부 GitHub/NeurIPS/YouTube URL 제한은 Exa 및 공개 authless HTML 읽기로 복구했다. PE 저장소의 `/LICENSE` crawl 실패는 실제 원본 README의 code badge와 PE 전용 `/LICENSE.PE` 및 checkpoint metadata를 읽어 대상별로 복구했다. YouTube 자막 본문 확보 실패는 해결된 것으로 처리하지 않았다.

새 가중치·원영상 다운로드, 패키지 설치, 제품 core 수정, 개인 미디어 전송은 이 조사 범위에 포함되지 않았다.
