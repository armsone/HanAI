# 한양 영상 협업 분석기

원본 영상은 로컬에서만 처리하고, 후보 구간을 사람이 빠르게 라벨링하기 위한 도구다.

## 실행

```bash
python3 tools/hanai-video-analyzer/hanai_video_analyzer.py scan \
  --work-dir Work/hanai-video-analysis \
  /path/to/first.MOV /path/to/second.MOV

python3 tools/hanai-video-analyzer/hanai_video_analyzer.py serve \
  --work-dir Work/hanai-video-analysis
```

브라우저에서 `http://127.0.0.1:8765`를 열면 진행률과 후보별 라벨 버튼이 나온다. 라벨은 즉시 로컬 `analysis.json`에 저장되며, 원본 파일명은 결과에 기록하지 않는다.

`ffprobe`와 `ffmpeg`가 필요하다. `Work/`는 저장소에서 차단되어 있다.
