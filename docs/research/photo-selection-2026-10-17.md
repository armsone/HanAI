# 비슷한 사진 탐색과 대표 사진 선정 고도화 근거

확인일: 2026-10-17, Asia/Seoul. 공개 자료 조사이며 개인 사진이나 비공개 소스를 외부에 전달하지 않았다. 이 문서는 조사·설계 제안이다. **HanAI에서 측정한 정확도 향상은 아직 없다.**

## 먼저 적용할 방향

사진이 비슷한 이유와 좋은 사진인 이유를 분리한다. 같은 원본의 재압축·크롭, 같은 순간의 연사, 같은 피사체의 다른 촬영, 의미만 비슷한 사진을 하나의 임계값으로 판정하면 서로 다른 사진이 한 묶음으로 잘못 합쳐질 수 있다. 운영체제에 종속되지 않는 수치 코어에 선택형 DINOv2·SigLIP 2 로컬 학습 특징을 연결하고, 특징 버전 관리·묶음의 일관성·품질 판단을 함께 개선하는 방향이다. 기본 경로는 가벼운 수치 분석을 유지하고 모델 사용은 명시적으로 선택한다.

| 판단 대상 | 적합한 신호 제안 | 잘못 적용하기 쉬운 방법 |
| --- | --- | --- |
| 같은 원본의 편집·재압축·크롭 | 복사 탐지 특징, 보수적인 원본 일치 근거 | 의미만 비슷하다는 이유로 복사 판정 |
| 연사·동일 장면의 가까운 촬영 | 전체 특징, 시간·장면 문맥, 피사체 영역 | 시간만 가깝다는 이유로 동일 사진 취급 |
| 동일 피사체·장소의 다른 촬영 | 전역·국소 특징과 별도 분류 | 같은 인물이라는 이유로 모든 사진 병합 |
| 의미·종류만 비슷한 사진 | 의미 임베딩을 통한 검색 | 검색 결과를 중복 제거 근거로 사용 |
| 묶음 내 대표 사진 | 얼굴·피사체 품질, 노출, 구도, 미적 점수 | 전역 선명도나 유사도 하나로 대표 결정 |
| 여러 장의 최종 선택 | 품질과 표정·행동·구도의 다양성 | 점수 높은 유사 장면만 반복 선택 |

위 표는 적용 제안이다. 모델의 원문 평가가 한양의 개인 사진 분포에서도 같은 결과를 낸다는 뜻은 아니다.

## 구현 가능한 수치 규칙

### 1. 특징과 임계값을 같은 버전 단위로 관리

특징 캐시와 임계값을 `모델/리비전 + 전처리 + 차원 + 거리 종류`에 묶는다. 서로 다른 단위의 특징은 직접 비교하지 않는다. 유클리드 거리는 낮을수록 유사하고 cosine similarity는 높을수록 유사하므로, 수치 방향도 명시한다. 특징 누락·NaN·무한대를 높은 유사도로 변환하지 않는다.

SSCD 공식 구현의 `cosine > 0.75`와 정밀도 90% 예시는 DISC 데이터에 관한 것이다. 이 값을 한양의 기본 임계값으로 복사하는 것은 근거가 없다. 보정과 평가 사진을 분리하고, 모델·전처리 변경 시 기존 보정값을 자동 재사용하지 않는 방안을 제안한다.

### 2. 유사성의 연결만으로 묶음을 확장하지 않기

`d(A,B)=0.2`, `d(B,C)=0.2`, `d(A,C)=0.4`, 임계값 `0.3`이면 세 항목 전체가 서로 비슷한 것은 아니다. 연결요소 방식은 이 셋을 합칠 수 있다.

엄격한 유사 묶음은 새 항목이 기존 모든 항목과 임계값 이내인지 확인하는 complete-link 조건을 제안한다. 후보가 많으면 먼저 시간·장면 등으로 후보를 좁힌 뒤 묶음 내부에 적용한다. 대표점 비교는 비용이 낮지만 모든 쌍의 유사성을 보장하지 못한다. 이 수학 규칙에 외부 클러스터링 라이브러리 추가가 필수인 것은 아니다.

### 3. 없는 품질 신호를 0점으로 벌하지 않기

Apple 미적 점수는 `a = clamp((overallScore + 1) / 2, 0, 1)`로 정규화할 수 있고 얼굴 품질은 원래 `[0,1]`이다. 합산 시 `Q = Σ(wᵢqᵢ) / Σ(wᵢ)`에서 실제로 유효한 신호만 분모와 분자에 포함하는 방안을 제안한다. 얼굴 없는 풍경을 얼굴 품질 0점으로 처리하지 않는다.

얼굴 품질은 가능한 한 동일 얼굴의 여러 촬영 비교에 사용한다. 얼굴 검출만으로 동일 인물을 안다고 가정하지 않는다. 대표는 품질로 선택하고 추가 사진은 이미 선택한 사진과의 구도·행동 차이를 반영할 수 있다. 가중치와 다양성 강도는 원문이 보장한 값이 아니며 별도 보정 대상이다.

## 모델 선택과 비용 경계

확인된 사실: DINOv3는 검색과 국소 특징을 지원하는 범용 시각 백본이며, SigLIP 2는 의미 이해·이미지 텍스트 검색 등을 지원한다. SSCD는 같은 2D 원본에서 나온 복사를 겨냥한다. 세 후보가 해결하는 문제가 같지 않다.

제안: 실제 연결의 첫 후보는 작은 DINOv2 시각 특징과 선택형 SigLIP 2 텍스트-이미지 특징이다. 이들은 같은 전처리·리비전·거리 계약을 거쳐 공통 수치 코어에 전달한다. DINOv3·SSCD는 추후 분리된 평가로 비교할 후보로 남긴다. Apple Vision은 해당 플랫폼에서 추가로 활용할 수 있는 품질 신호이며 공통 코어의 필수 조건으로 삼지 않는다. 모델 하나가 검색·중복 판정·대표 선정 모두를 개선한다고 가정하지 않는다.

미확인: 실제 대상 기기에서의 지연·메모리·배터리, Core ML 등 변환 성공, 다운로드 외 최종 배포 크기, 한양 사진에서의 성능. 따라서 신규 모델의 자동 다운로드나 모든 앱의 기본 런타임 편입을 이 문서로 정당화하지 않는다. DINOv3 공식 라이선스는 사용자 정의 라이선스이므로 비공식 사이트의 라이선스 요약을 그대로 따르지 않는다. DINOv2·SigLIP 2의 고정 가중치 크기는 아래에서 별도로 확인했다.

## 평가를 설계할 때의 기준

이 절은 향후 평가 설계이며 테스트·벤치마크를 실행했다는 보고가 아니다.

사진 쌍은 최소한 다음 범주를 나눠 라벨링한다.

1. 같은 원본: 그대로 복사, 해상도 변경, JPEG 재압축, 색·밝기 변경, 크롭, 회전.
2. 같은 순간·장면의 연사: 표정·초점·손떨림·노출만 달라 대표 선정이 필요한 경우.
3. 같은 피사체지만 다른 의미: 다른 행동·시간·배경·관점으로 보존 가치가 다른 경우.
4. 의미만 유사: 다른 인물·장소·사물이 비슷한 구도나 종류로 등장하는 경우.
5. 혼동하기 쉬운 음성 쌍: 같은 배경의 다른 사람, 비슷한 옷, 단체사진의 인원 차이, 반복 무늬.
6. 별도 보존 유형: 문서·스크린샷·영수증. 추억 영상에서는 유틸리티 신호를 쓸 수 있지만 삭제 근거로 사용하지 않는다.

원본과 그 변형, 같은 연사·행사·영상에서 나온 사진은 동일 분할에 둔다. 무작위 사진 쌍 분할은 사실상 같은 원본을 보정·평가 양쪽에 넣을 수 있어 피한다. 보정 분할에서 임계값을 선택하고 독립 평가 분할에는 고정한다. 모델 변경 전후 동일한 분할과 입력을 사용한다.

우선 지표는 **다른 사진을 같은 묶음으로 잘못 합치는 비율**과 유사 판정의 정밀도다. 함께 재현율과 변형별 누락, 군집 내 서로 다른 의미의 사진 혼입, 묶음이 과하게 쪼개지는 경우를 보고한다. 쌍 분류가 좋아도 연결 확장 때문에 큰 군집이 오염될 수 있으므로 군집 결과도 따로 평가한다. 좋은 사진 선택은 사용자 쌍 선호와 대표 일치율, 여러 장을 고를 때는 다양성과 중요한 장면 누락을 별도로 본다.

기술 품질과 추억의 중요도는 다르다. 흐리더라도 유일한 의미를 가진 사진을 자동으로 제외하거나 원본을 삭제하는 설계는 이 제안 범위에 포함하지 않는다. 성능 검증을 하려면 사용자 승인 범위 내에서 별도 평가 실행이 필요하다.

## 공식 원문과 적용 범위

| 원문 | 확인한 내용 | 적용 한계 |
| --- | --- | --- |
| [SSCD 논문](https://arxiv.org/html/2202.10261v2) | 복사는 동일 2D 원본, instance matching과 구분. 크롭·압축·회전·오버레이 등을 학습에 사용 | 모든 변형이나 연사에 완전한 불변성 보장 아님 |
| [SSCD 공식 구현](https://github.com/facebookresearch/sscd-copy-detection) | 기본 512차원 L2 정규화 특징, large 1024차원. cosine/유클리드 거리, 전처리와 DISC 예시 제공 | 저장소 archived. 코드 MIT와 실제 사용할 가중치 조건을 구분할 필요 |
| [DINOv3 논문](https://arxiv.org/abs/2508.10104) | 범용 전역·고해상도 dense feature 개선 | 한양의 유사 사진·대표 선정 향상률 직접 근거 아님 |
| [DINOv3 모델 카드](https://github.com/facebookresearch/dinov3/blob/main/MODEL_CARD.md) | 이미지 검색·대응점·추적 용도, 여러 모델 크기 | 모바일 처리량·배포 적합성 미확인 |
| [DINOv3 공식 라이선스](https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md) | custom DINOv3 License | 비공식 사이트의 CC-BY-NC 설명으로 대체하지 않음 |
| [DINOv2 공식 구현](https://github.com/facebookresearch/dinov2) | 기본 DINOv2 코드·가중치 Apache 2.0, 여러 백본 | 추가 XRay/Cell 계열은 별도 조건. 저장소 전체로 일반화 금지 |
| [SigLIP 2 논문](https://arxiv.org/abs/2502.14786) | 의미 이해·이미지 텍스트 검색·국소화 개선. 일부 종횡비 보존 변형 | 중복 탐지 전용 성능 증거 아님 |
| [Google SigLIP 2 모델 카드](https://huggingface.co/google/siglip2-base-patch16-224) | 이미지 특징 추출과 intended uses | 전체 모델 크기와 vision tower 크기를 혼동하지 않기 |
| [Apple 얼굴 품질](https://developer.apple.com/documentation/vision/vnfaceobservation/facecapturequality-2o4xv) | 빛·흐림·위치를 반영하는 0~1 점수, 같은 얼굴 촬영 사이 비교 용도 | 얼굴의 중요도·감정적 의미를 평가하는 점수 아님 |
| [Apple 특징 거리](https://developer.apple.com/documentation/vision/vnfeatureprintobservation/computedistance(_:to:)) | 낮은 거리가 높은 유사성 | 보편적인 중복 임계값 제시 안 함 |
| [Apple 리비전 비교 설명](https://developer.apple.com/forums/thread/732876?answerId=757495022) | 다른 request revision 특징을 비교하면 불일치 오류 가능 | 개발자 포럼 설명. SDK/API 원문과 함께 적용 |
| [Apple WWDC24 Vision](https://developer.apple.com/videos/play/wwdc2024/10163/) | 미적 점수 -1~1, isUtility 소개 | 구현 시 설치 SDK와 지원 OS 확인 필요 |
| [Google Research MUSIQ](https://research.google/blog/musiq-assessing-image-aesthetic-and-technical-quality-with-multi-scale-transformers/) | 강제 크롭·리사이즈가 품질 판단을 바꿀 수 있어 native resolution·종횡비 보존 다중 스케일 활용 | 모델 자체를 채택하지 않아도 전처리 설계에 적용 가능 |
| [NIMA 논문](https://arxiv.org/abs/1709.05424) | 인간 점수 분포를 예측하는 무참조 품질·미적 평가 | 개인별 추억 선호를 학습한 결과는 아님 |
| [complete linkage 정의](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.AgglomerativeClustering.html) | 두 군집 사이 최대 거리를 사용하는 병합 기준 | 라이브러리 도입을 요구하는 근거가 아니라 수학 정의 참고 |

## 조사 실행과 증거 한계

실제 Exa `web_search_exa` 4회, `web_fetch_exa` 2회로 검색·공식 본문 읽기를 수행했고 일반 웹 검색과 공식 원문 열기로 교차 확인했다. 4번째 검색은 아래 로컬 학습 모델 연결에 필요한 ONNX Runtime 공식 경로 탐색이다. 일부 원문은 페이지당 6,500~10,000자 추출 또는 초록·모델 카드 범위로 읽었다. 모든 논문의 전체 부록이나 모든 가중치 파일을 검토한 것은 아니다.

Apple 얼굴 품질의 짧은 문서 URL에서 `CRAWL_NOT_FOUND`가 발생했다. 검색에서 공식 문서 suffix URL을 찾아 같은 Exa 원문 읽기로 복구했다. 비공식 DINOv3 사이트의 라이선스 설명은 공식 GitHub 라이선스와 달라 채택하지 않았다.

실제 HanAI 사진의 정밀도·재현율·대표 선호도나 기기 성능을 이 조사에서 측정하지 않았다. 모델 후보에 관한 외부 평가를 제품의 측정 결과로 표시하면 안 된다.

## 후속 범위: 공개 사전학습 모델의 실제 로컬 연결

수치 휴리스틱 정리에 그치지 않고 공개 사전학습 모델을 선택적으로 연결한다. 아래는 공식 모델 API와 파일 메타데이터를 직접 읽어 확정한 연결 계약이다. 선택형 로컬 어댑터로 실제 비교 가능한 경로를 만들되 기본 동작을 대형 모델로 자동 대체하지 않는다. 구현·다운로드·실기기 동작·정확도는 각각 별도 상태이며, 이 문서는 해당 성공을 대신 증명하지 않는다. 실제 실행·빌드 결과는 별도의 실행 보고에서 확인해야 한다.

### 고정 모델과 비용

| 항목 | DINOv2-small | SigLIP 2 base 224 |
| --- | --- | --- |
| 공식 저장소 | `facebook/dinov2-small` | `google/siglip2-base-patch16-224` |
| 고정 리비전 공식 카드의 라이선스 표기 | Apache 2.0 | Apache 2.0 |
| 확인한 revision | `ed25f3a31f01632728cabb09d1542f84ab7b0056` | `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` |
| `model.safetensors` 크기 | 88,249,960 bytes | 1,500,800,904 bytes |
| 가중치 SHA256 | `ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1` | `612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b` |
| 시각 입력 전처리 | 짧은 변 256 bicubic → 중앙 224 crop | 224×224 bilinear resize |
| 정규화 | /255, mean .485/.456/.406, std .229/.224/.225 | /255, mean/std 모두 .5 |
| 최초 사용 목적 | 384차원 CLS의 L2 정규화 시각 특징 | 시각 특징과 선택적 텍스트-이미지 연관도 |

공식 API: [DINOv2 모델 메타데이터](https://huggingface.co/api/models/facebook/dinov2-small?blobs=true), [SigLIP 2 모델 메타데이터](https://huggingface.co/api/models/google/siglip2-base-patch16-224?blobs=true). 이 조회에서 가중치는 다운로드하지 않고 메타데이터와 설정 JSON만 읽었다. 샌드박스 DNS 조회가 실패해 정식 권한 경로로 동일한 공개 메타데이터 요청을 재개했고 성공했다.

라이선스 표기는 각 고정 리비전의 [DINOv2 카드](https://huggingface.co/facebook/dinov2-small/blob/ed25f3a31f01632728cabb09d1542f84ab7b0056/README.md), [SigLIP 2 카드](https://huggingface.co/google/siglip2-base-patch16-224/blob/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2/README.md) front matter를 직접 읽어 확인했다. 공개 모델을 재배포할 때의 고지 등 실제 배포 절차까지 이 조사에서 수행한 것은 아니다.

DINOv2 config의 `image_size=518`과 저장된 image processor의 기본 출력 224는 구분해야 한다. 공식 processor 설정을 따라야 하며 모델 설정의 숫자로 전처리를 임의 대체하지 않는다. SigLIP 2는 vision tower만의 크기와 전체 가중치 크기가 다르다. 다국어 텍스트 임베딩을 포함한 실제 다운로드가 약 1.5GB이므로 자동 기본 다운로드보다 명시 선택이 적절하다.

DINOv2 기본 중앙 크롭은 가장자리의 사람·피사체를 일부 놓칠 수 있다. 이 모델을 연결했다는 것만으로 심한 크롭이나 회전에 대한 강건성을 확보했다고 말할 수 없다. 원본 전체 뷰와 피사체 뷰를 추가하는 후속 설계는 연산 비용과 후보 재현율을 함께 비교해야 하며, 전처리와 임계값도 새 버전으로 관리해야 한다. 품질 점수는 검색용 중앙 크롭 특징과 별도로 계산하는 것이 제안 방향이다.

정확한 전처리 원문: [DINOv2 processor](https://huggingface.co/facebook/dinov2-small/blob/ed25f3a31f01632728cabb09d1542f84ab7b0056/preprocessor_config.json), [SigLIP 2 processor](https://huggingface.co/google/siglip2-base-patch16-224/blob/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2/preprocessor_config.json).

### 로컬 추론 계약

어댑터는 명시한 로컬 디렉터리만 읽고, `local_files_only=True`, `trust_remote_code=False`, `use_safetensors=True`로 로드하는 방안을 구현 대상으로 정했다. 모델 다운로드는 별도 명령에서만 허용하며 정해진 공식 repo·revision·파일 이름만 사용한다. pickle과 원격 Python 코드는 허용하지 않는다. manifest에 파일별 해시·크기와 모델 리비전을 기록하고 고정된 가중치 해시도 별도로 확인한다. 원본 픽셀·벡터·사용자 질의는 외부 추론 서비스로 전송하지 않는다.

기본은 CPU, eval/inference mode다. DINOv2는 `last_hidden_state[:,0,:]`를 정규화한다. SigLIP는 이미지와 텍스트 특징을 각각 정규화하고 `sigmoid(cosine × exp(logit_scale) + logit_bias)`로 연관도를 계산한다. 이 값은 사진이 사용자 질의와 얼마나 연결되는지 보는 신호이며, 보정된 추억의 중요도 확률이나 미적 품질 점수로 부르면 안 된다. 질의가 없으면 연관도는 미제공 상태를 유지한다.

버전별 API 차이도 처리해야 한다. [Transformers v5.20.1 공식 SigLIP 구현](https://github.com/huggingface/transformers/blob/v5.20.1/src/transformers/models/siglip/modeling_siglip.py)의 `get_image_features`는 `pooler_output`을 가진 ModelOutput을 반환한다. 이전 Tensor 반환 예제만 따르면 실패할 수 있다. 텍스트에는 모델 학습 방식에 맞게 `padding="max_length"`를 적용한다. [공식 SigLIP 문서](https://huggingface.co/docs/transformers/model_doc/siglip)

SigLIP 2 토크나이저 설정의 최대 길이는 거대한 sentinel 값이므로 이를 입력 길이로 사용하지 않고 모델 text config의 실제 위치 임베딩 길이를 적용한다. 고정한 Transformers 5.20.1에서 설치된 GemmaTokenizer 클래스와 초기화 소스를 읽어 TokenizersBackend 기반임을 확인했다. 공식 `tokenizer.json`을 사용하는 해당 버전 경로에는 별도 SentencePiece 설치를 추측으로 추가하지 않는다. 이 클래스 조회는 실제 텍스트 토큰화 성공을 대신하지 않는다.

### ONNX와 운영체제 범위

제안한 최초 내보내기는 DINOv2의 고정 float32 입력 `[1,3,224,224]`에서 정규화 CLS `[1,384]`를 만드는 ONNX graph, opset 17이다. 입력에는 앞서 설명한 공식 전처리가 이미 적용되어 있어야 한다. graph를 생성했다고 PyTorch와의 수치 일치나 실제 모바일 실행이 확인된 것은 아니다.

[ONNX Runtime 모바일 공식 안내](https://onnxruntime.ai/docs/tutorials/mobile/)는 모든 대상의 CPU 지원과 Android Java/C/C++ 및 iOS C/C++/Objective-C 패키지를 안내한다. [공식 호환성](https://onnxruntime.ai/docs/reference/compatibility.html)은 Windows·Linux·macOS·Android·iOS를 다룬다. **런타임이 운영체제를 지원하는 것과 HanAI의 각 운영체제 앱에 모델 어댑터를 넣어 빌드·설치한 것은 다르다.** Python 데스크톱 도구나 ONNX 파일만으로 Android/iOS 제품 통합까지 완료했다고 표현하지 않는다. 하드웨어 가속도 별도 기기별 확인 대상이다.

조사 시점의 공개 패키지 버전은 torch 2.14.1, transformers 5.20.1, huggingface_hub 2.2.0, Pillow 12.3.0, safetensors .8.0, onnx 1.23.2, onnxruntime 1.31.0이다. ONNX Runtime 해당 버전은 Python 3.11 이상이며, torch의 확인한 macOS arm64 wheel은 macOS 14 이상을 대상으로 한다. Linux 기본 torch 배포는 CUDA 의존성이 포함될 수 있으므로 CPU 배포 경로를 별도로 선택해야 한다. 공개 메타데이터 확인은 실제 환경에서 이 조합을 설치·구동했다는 뜻이 아니다.
