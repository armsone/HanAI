# DINOv2-small / ORT1.24.2 재배포 고지 준비

확인: 2026-10-17 07:45 KST. 공개 원문 수집·판단 문서다. **실제 릴리즈·앱 bundle 고지 반영·배포 완료가 아니다.** 코드·실행환경·모델 파일은 변경하지 않았다.

## 대상과 라이선스 확인

대상 모델은 [facebook/dinov2-small 고정 revision](https://huggingface.co/facebook/dinov2-small/tree/ed25f3a31f01632728cabb09d1542f84ab7b0056) `ed25f3a31f01632728cabb09d1542f84ab7b0056`이다. 실제 HF API의 license는 `apache-2.0`, tree는 README/config/preprocessor/safetensors/bin 등6개이며 **별도 LICENSE/NOTICE 파일은 없다.** [고정 model card](https://huggingface.co/facebook/dinov2-small/blob/ed25f3a31f01632728cabb09d1542f84ab7b0056/README.md)는 HF 팀 작성이라고 밝히며 원 저자 저장소를 연결한다.

원 저자 repo의 확인 revision은 `7764ea0f912e53c92e82eb78a2a1631e92725fc8`이다. [README의 일반 DINOv2 License 절](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/README.md)은 **코드와 모델 가중치 모두 Apache2.0**라고 명시한다. [원 저자 MODEL_CARD](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/MODEL_CARD.md)의 모델 license도 `Apache License 2.0`이다. 따라서 코드 license만 보고 가중치에 확대 추정한 판단이 아니다. 현재 같은 repo의 XRay/Cell-DINO 별도 NC 조항은 이번 원형 small 모델과 구분한다.

원 코드의 실제 저작권 고지: `Copyright (c) Meta Platforms, Inc. and affiliates.` — [vision_transformer.py](https://github.com/facebookresearch/dinov2/blob/7764ea0f912e53c92e82eb78a2a1631e92725fc8/dinov2/models/vision_transformer.py). 이 코드의 고지를 모델의 개별 저작권 파일이 원래 존재했던 것처럼 표현하지 않는다.

**원 DINO repo의 NOTICE는 존재하지 않았다.** root URL404를 확인한 뒤 전체 Git tree(truncated=false)에서도 NOTICE 경로가 없는 것을 확인했다. Apache4(d)는 원 배포에 NOTICE가 있을 때 그 고지를 유지하는 조건이다. 존재하지 않는 원 NOTICE를 만들거나 누락됐다고 주장하지 않는다. 제품 자체의 THIRD_PARTY_NOTICES에 출처·license·변경 사항을 새로 정리하는 것은 가능하다.

## Apache2.0 변환 모델에 필요한 고지

기준 원문은 [DINO LICENSE 전체](https://raw.githubusercontent.com/facebookresearch/dinov2/7764ea0f912e53c92e82eb78a2a1631e92725fc8/LICENSE)다. 재배포 시 license 사본 제공(4a), 관련 원 고지 유지(4c), 존재하는 NOTICE 유지(4d)를 따른다. 변경 표시는4(b)의 다음 문구를 기준으로 한다: **“You must cause any modified files to carry prominent notices stating that You changed the files”**. 영문 원문은 수정·번역하지 않고 보관하며 아래 한국어 설명은 운영 안내로만 둔다.

ONNX/CoreML 가중치가 binary여서 header를 넣기 어렵다면 모델 metadata와 같은 배포물의 인접 문서/고지 화면에서 변경 표시를 명확히 함께 제공하는 안을 권고한다. metadata만 남기고 최종 앱 사용자에게 license 사본이 도달하지 않는 방식은 피한다. 최종 패키징 적합성 판단은 실제 산출물에서 확인해야 한다.

변환물의 **권고 변경 고지 초안**(원문 인용 아님):

> This distribution contains a modified export of facebook/dinov2-small, Hugging Face revision ed25f3a31f01632728cabb09d1542f84ab7b0056. Modifications: ONNX/Core ML format conversion; positional embeddings precomputed for fixed 224×224 input using the source model's interpolation; CLS-token feature extraction and L2 normalization. The model has not been fine-tuned by this conversion. These modifications are not endorsed by Meta or Hugging Face.

위 문구의 positional bake·정규화·fine-tuning 부재는 **실제 export 산출물과 일치할 때만 사용**한다. 원본·변환물 SHA256, 변환 도구 버전, FP32/FP16, 입력 전처리 계약, 변경자·변경일도 별도 provenance에 기록하는 것이 권고다. Apache 상표·보증 조항을 제품 성능 보증으로 해석하지 않는다.

model card는 일반 feature extraction 용도와 한계를 설명하고 citation을 제공한다. 원 MODEL_CARD는 서구 부유층 쪽 편향과 fine-tuning의 편향 증가 가능성을 기록한다. 카드/논문 출처를 변환 모델 설명에 유지하는 것을 권고하지만, **읽은 Apache 원문과 카드에서 별도의 의무적 모델카드 배포 양식·인증·승인·연구 전용 제한은 찾지 못했다.** 카드의 권고와 license 의무를 혼동하지 않는다.

## ONNX Runtime1.24.2와 제3자 고지

배포 대상 runtime은 공식 Maven AAR/JVM JAR1.24.2로 한정한다. [LICENSE](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.24.2/LICENSE)는 MIT, 저작권은 `Copyright (c) Microsoft Corporation`이다. 보존 조건의 실제 구절은 **“The above copyright notice and this permission notice shall be included”**이다. 실제 배포에는 이 짧은 발췌가 아니라 LICENSE 전체를 포함한다.

[동일 tag ThirdPartyNotices.txt 전체](https://raw.githubusercontent.com/microsoft/onnxruntime/v1.24.2/ThirdPartyNotices.txt)가 실제 존재한다. 첫머리의 **“Do Not Translate or Localize”**를 확인했다. 영문 원문을 그대로 제공하고 설명만 별도로 한국어로 쓴다. MIT 한 줄로 내장 제3자 조건을 대체하지 않는다. 목록의 모든 기술이 모든 ABI에 링크됐다고 주장하지 않고, 구성별 법적 고지의 누락을 피하기 위해 배포된 notices를 보존한다.

공식 AAR/JAR의 central directory만 HTTP Range로 확인한 결과: AAR40,822,946bytes에는 license/notice 이름 경로가 없었고, JVM JAR41,033,658bytes에는 `ThirdPartyNotices.txt`가 있었다. 따라서 **AAR 의존성을 추가하면 앱 사용자용 notices가 자동 포함된다고 가정하지 않는다.** 원 runtime LICENSE와 해당1.24.2 notices를 앱/배포 bundle에 명시적으로 포함하고, 최종 JAR 내부 고지와도 일치 여부를 확인해야 한다. JAR가 포함한 native 라이브러리·ABI가 제품에 실제 들어가는 범위도 최종 산출물 확인 대상이다.

## 원문 수집 식별값과 권고 배치

원문 전체를 HTTP로 읽고 다음 hash를 계산했다. 바이너리·앱·모델 해시가 아니라 **법적 문서 원본 bytes**의 SHA256이다.

| 원문 | bytes | SHA256 |
| --- | --- | --- |
| DINO LICENSE (위 고정 repo revision) | 11,359 | 600cc67cc4cb2f5ea317dcfc687ad1c74dc4bec8782bbe9db0afd83513b935b7 |
| DINO MODEL_CARD (같은 revision) | 9,152 | 70ca59606bee0a5fbb1baec80e7e29a93cd7cfbe26ca1910c52a852c4aab09d0 |
| ORT1.24.2 LICENSE | 1,073 | 2f07c72751aed99790b8a4869cf2311df85a860b22ded05fa22803587a48922c |
| ORT1.24.2 ThirdPartyNotices | 325,054 | 0e07b95f3a8d6230037707c5c4a2b554d12c4cb67369669ac255635528ffcee2 |

향후 repo 권고 경로: `THIRD_PARTY_NOTICES.md`(제품별 요약/변경표시), `licenses/dinov2/LICENSE`, `licenses/dinov2/MODEL_CARD.md`, `licenses/onnxruntime-1.24.2/LICENSE`, `licenses/onnxruntime-1.24.2/ThirdPartyNotices.txt`. ONNX/CoreML 배포물에는 해당 DINO 고지와 변경/provenance를, AAR/JAR를 포함한 앱·archive에는 runtime 고지 전체를 함께 넣는다. 이 경로는 제안이며 이번 조사에서는 repo나 앱 리소스를 만들지 않았다.

조사 근거: 실제 Exa검색1회 → 일반 웹 교차검색1회 → Exa fetch1회(5URLs), 공개 HF API·고정 raw 원문·전체 tree·archive metadata 확인. NOTICE crawl 실패는 실제404/tree 부재로 판정했으며 임의 대체 파일을 원본으로 만들지 않았다. 새 계약·접근신청·유료 서비스 이용은 없었다.
