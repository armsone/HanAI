# 다음 시간 모델 2개: 실제 도입 조건과 최소 실험

확인: 2026-10-17 07:30 KST. 실제 Exa 검색 2회·본문 fetch 5회 → 일반 웹 교차검색 → 공식 원문·공개 API·저장소 소스 확인을 수행했다. 가중치·원영상 다운로드, 설치, 모델 실행, 제품 코드 변경은 하지 않았다.

## 판단

**MobileViCLIP-Tiny는 조건부 후보로 남길 가치가 있다.** 실제 시간 연산과 모바일 측정 근거가 있지만 공개된 task 가중치만으로 실행되지 않으며 원형 MobileCLIP의 정확한 가중치 판본과 라이선스 연결을 먼저 확정해야 한다. 지금 확인한 자료만으로 즉시 제품 탑재 가능하다고 할 수 없다.

**InternVideo2 distilled S14는 더 작은 완성 대체품으로 확정하지 않는다.** 작은 영상 encoder는 존재하지만 text/base 가중치까지 포함한 비용·라이선스와 custom loader가 남는다. 공식 HF의 `InternVideo2_CLIP_S`라는 이름도 원 논문의 S14와 동일하다고 가정하지 않는다. 기존 합법적 실행 경로의 XCLIP를 유지하면서 시간창·샘플링의 누락을 먼저 줄이는 비교가 가장 작다.

## 1. MobileViCLIP — 모바일 시간 연산 근거는 있음

공식 [ICCV2025 논문](https://openaccess.thecvf.com/content/ICCV2025/papers/Yang_MobileViCLIP_An_Efficient_Video-Text_Model_for_Mobile_Devices_ICCV_2025_paper.pdf), [arXiv 본문](https://arxiv.org/html/2508.07312), [저자 저장소](https://github.com/MCG-NJU/MobileViCLIP)를 읽었다. 고정 코드 revision: `3706de2e69d30be05c08f8fb8a4ba0619f76ea43`.

- 입력은 8프레임·256×256. Tiny는 MobileCLIP-S0, Small은 S2에서 시작한다. 프레임별 특징을 마지막에 평균하지만 **그 전에 temporal depthwise convolution·시간 위치 임베딩·spatiotemporal attention을 사용**하므로 독립 정적 특징 평균과 다르다.
- 논문은 batch1, Intel Xeon Gold6248 CPU/V100/iPad Air2020을 구분한다. iPad는 iOS18·CoreMLtools7.1 변환으로 측정했다고 설명한다. 표2의 Tiny는 video/text 각각 CPU85.5/7.2ms, mobile12.4/2.6ms다. 이는 저자의 모델 지연이며 우리 기기의 decode·전처리·발열 포함 전체 지연은 아니다.
- 공식 [평가 script](https://github.com/MCG-NJU/MobileViCLIP/blob/3706de2e69d30be05c08f8fb8a4ba0619f76ea43/scripts/evaluation/clip/zero_shot/mobileviclip_tiny/eval_msrvtt.sh)는 CUDA/torchrun을 사용한다. 손쉬운 standalone CPU demo나 배포용 모바일 앱이 확인된 것은 아니다.
- 저장소 전체 공개 tree와 release 페이지를 확인한 범위에서 `.onnx`/CoreML artifact 또는 이름에 export/coreml/onnx가 있는 파일을 찾지 못했다. **논문의 변환 성공 보고와 재배포 가능한 export 파일 존재는 구분한다.**

### checkpoint의 실제 존재와 비용

README가 직접 연결한 [공개 Google Drive 폴더](https://drive.google.com/drive/folders/1ROJYwQeO8lt4mEfNjEIAIDWj736WIQq7?usp=drive_link)의 HTML 파일목록 메타데이터를 읽었다. 파일을 다운로드하지 않았으므로 아래는 목록상의 bytes이며 자체 해시 검증값이 아니다.

| 파일 | 공개 목록 bytes | 의미 |
| --- | --- | --- |
| mobileviclip_tiny.pt | 24,104,529 | Tiny task checkpoint |
| mobileclip_s0.pt | 215,934,653 | Tiny의 vision/text 초기 base |
| mobileviclip_small.pt | 73,853,707 | Small task checkpoint |
| mobileclip_s2.pt | 398,067,246 | Small의 vision/text 초기 base |

실제 [평가 config](https://github.com/MCG-NJU/MobileViCLIP/blob/3706de2e69d30be05c08f8fb8a4ba0619f76ea43/scripts/evaluation/clip/zero_shot/mobileviclip_tiny/config_msrvtt.py)는 base vision/text 파일을 지정하고 task checkpoint를 별도 적용한다. [모델 loader](https://github.com/MCG-NJU/MobileViCLIP/blob/3706de2e69d30be05c08f8fb8a4ba0619f76ea43/models/mobileviclip_tiny.py)가 두 base 경로를 요구한다. 따라서 24MB 하나를 완성 모델 다운로드 비용으로 홍보하면 틀린다. Drive checkpoint의 immutable revision·공식 SHA256은 확인하지 못했다. 고정된 Git revision은 가중치 판본의 고정을 대신하지 않는다.

### 라이선스 연쇄 — 현재 조건만 옛 가중치에 일괄 적용하지 않음

저자 [코드 LICENSE](https://github.com/MCG-NJU/MobileViCLIP/blob/main/LICENSE)는 MIT다. 하지만 기반 가중치의 조건은 따로 확인해야 한다.

1. **현재** Apple [LICENSE_MODELS](https://github.com/apple/ml-mobileclip/blob/main/LICENSE_MODELS)는 연구 목적 전용이고 제품 개발·상용 제품 사용을 제외하며 파생 모델도 연구 목적으로 제한한다. 이 조건으로 배포된 가중치를 제품에 사용할 수 있다고 보지 않는다. MobileCLIP2와 원형 MobileCLIP를 혼동하지 않는다.
2. **실제 2024-07-22 판본**의 [LICENSE_weights_data](https://github.com/apple/ml-mobileclip/blob/341ef058802f0e4e5ab13c02f0cb32a3a94e367b/LICENSE_weights_data)는 사용·수정·재배포 허용 문구가 있으며 research-only/product-development 제외 문구가 없다. 따라서 MobileViCLIP까지 현재 Apple 조건만으로 일괄 금지라고 단정한 초기 판단은 수정한다.
3. **남은 확인:** 공개 Drive base의 hash·취득 판본·적용 고지와 저자 task checkpoint의 파생 provenance가 이 2024 조건에 연결되는지. 이 연결과 필요한 notices를 문서화하기 전에는 제품 허용도 금지도 확정하지 않는다. 법적 보증을 주장하지 않는다.

## 2. InternVideo2 distilled S14 — 작은 파일과 완성 모델을 구분

공식 [model zoo](https://github.com/OpenGVLab/InternVideo/blob/main/InternVideo2/multi_modality/MODEL_ZOO.md), [S14 설정](https://github.com/OpenGVLab/InternVideo/blob/3965eef16e2dadd0ea6c8d0cc29c8a3039df52e3/InternVideo2/multi_modality/scripts/pretraining/clip/S14/config.py), [모델 소스](https://github.com/OpenGVLab/InternVideo/blob/main/InternVideo2/multi_modality/models/internvideo2_clip_small.py)를 읽었다. S14는 8×224, vision hidden384/depth12/patch14이며 text encoder `mobileclip_b`와 `mobileclip_blt.pt`를 별도로 지정한다. default CUDA·fused/flash 연산·custom 코드여서 단순 CPU 실행이나 mobile export가 완료된 상태로 보지 않는다.

공식 [HF API](https://huggingface.co/api/models/OpenGVLab/InternVideo2_distillation_models?blobs=true), revision `449f7ea1d7d3b70b6b5630e70d238b44d3b7aaac`:

- `stage1/S14/S14_dist_1B_stage2/pytorch_model.bin`: 58,373,943bytes.
- `stage1/S14/S14_ft_k710_f8/pytorch_model.bin`: 47,216,099bytes.
- `clip/S14/pytorch_model.bin`: 2,301,803bytes. 공식 설정은 base vision과 text checkpoint를 요구하므로 이 파일만 완성 검색 모델로 계산하지 않는다.
- 코드 [LICENSE](https://github.com/OpenGVLab/InternVideo/blob/main/LICENSE)는 Apache2.0지만 위 HF repo metadata에는 license가 비어 있었다. base MobileCLIP의 정확한 과거판본 고지와 task weights 조건을 따로 연결해야 한다.

또 다른 공식 HF [InternVideo2_CLIP_S](https://huggingface.co/OpenGVLab/InternVideo2_CLIP_S)는 revision `1f9fca1389fd883defc652634d95a21121c85a8c`, safetensors745,562,274bytes·Apache2.0 metadata가 있다. 그러나 README 본문이 비어 있고 [실제 config](https://huggingface.co/OpenGVLab/InternVideo2_CLIP_S/blob/1f9fca1389fd883defc652634d95a21121c85a8c/config.json)의 vision hidden1024/depth24가 위 S14 설정과 다르다. `auto_map`도 custom 코드다. 이름만 보고 작은 S14의 안전한 완성 bundle이라고 대체하지 않는다. 현재 확인 범위에서 공식 mobile ONNX/CoreML artifact·우리 CPU 측정은 없다. 1B/6B/8B 계열은 이번 후보로 확대하지 않았다.

## 짧은 행동과 sparse sampling: 모델 교체 전 해결할 문제

**원문 사실:** MobileViCLIP는 전체 영상에서8프레임을 이용하는 clip-text 평가와, CG-DETR에 feature를 공급하는 구간 검출 평가를 구분한다. 후자의 경계 검출 성능은 backbone 한 번 호출의 성능이 아니다. [ActionFormer 원문 AppendixA/TableC·AppendixB](https://arxiv.org/html/2202.07925v2)는 feature stride4→16에서 평균 mAP66.8→61.9 감소와 매우 짧은 행동의 높은 FN을 보고한다. 이 숫자를 다른 모델의 예상 향상으로 옮기지 않는다.

[Revisiting KTS 원문](https://arxiv.org/pdf/2309.11569)은 긴 영상을 고정 길이로 균일 분할하면 중복·무정보 구간이 생긴다고 지적하고, 먼저1fps 특징에서 변화점을 찾고 각 segment 안에서 다시 sampling하는 구조를 설명한다. 이 논문을 읽었으며 author 영상을 시청한 것은 아니다. 새로운 학습 모델 채택 권고가 아니라 sampling 근거로만 사용한다.

**추론:** 핵심 동작이 선택한 timestamp 사이에서 끝나면 더 좋은 temporal encoder라도 관측되지 않은 동작을 복원할 수 없다. 같은 배경의 짧은 동작은 scene change도 없을 수 있어 변화점/큰 motion만으로 전 후보를 걸러서는 안 된다. KTS의1fps 앞단도 subsecond 동작을 보장하지 않는다.

**바로 비교할 최소안(제안):** 기존 expert 후보·균일 coverage를 보존하고, 동일8frame 모델에 전체창과 짧은 겹침창을 따로 공급한다. 유망/불확실 구간에서만 더 조밀한 원본 timestamp를 추가 추출하되, 기존 sparse cache를 복제한 것을 새 관측으로 세지 않는다. 실제 frame timestamp·창 길이·최대 sample gap·모델 호출 수를 기록한다. 원본이 없으면 sparse cache 평가의 누락 한계를 명시하고 고밀도 검증 완료로 보고하지 않는다. 짧은 행동 holdout에서 hit/구간 IoU·false positive·전체 지연을 비교한 뒤 변화점/모델 교체의 추가 비용을 판단한다. 구체적 sampling 값은 공개 논문에서 임의 복사하지 않는다.

접근 한계: 잘못된 InternVideo `/distillation` 경로와 Apple 날짜 형식 tag는 Exa CRAWL_NOT_FOUND였고 정확한 실제 tree/config/commit 경로를 찾아 원문으로 복구했다. Google Drive 목록의 파일 존재·bytes만 확인했으며 고정 weight hash와 바이너리 내용은 읽지 않았다. 새 후보의 제품 정확도 향상·CPU 실행·모바일 export·배포를 확인했다고 주장하지 않는다.
