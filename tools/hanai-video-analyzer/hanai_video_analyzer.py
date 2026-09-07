#!/usr/bin/env python3
"""로컬 전용 HanAI 영상 협업 분석기.

원본 영상과 생성 프레임은 --work-dir 아래에만 둔다. 저장소에는 이 도구와
사용자가 확정한 익명 라벨 요약만 남긴다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


LABELS = ["putter", "wedge", "iron", "wood", "driver", "non-shot", "unknown"]


def run(command: list[str], *, capture: bool = True) -> str:
    result = subprocess.run(command, check=True, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    return result.stdout if capture else ""


def write_progress(work: Path, **values: object) -> None:
    progress = work / "progress.json"
    values["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    progress.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def probe(path: Path) -> dict[str, object]:
    raw = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,width,height,avg_frame_rate,sample_rate,channels",
        "-of", "json", str(path)
    ])
    value = json.loads(raw)
    streams = value.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})
    return {
        "durationSeconds": round(float(value.get("format", {}).get("duration", 0)), 3),
        "width": video.get("width"),
        "height": video.get("height"),
        "frameRate": video.get("avg_frame_rate"),
        "audioPresent": bool(audio),
    }


def audio_candidates(path: Path, work: Path, source_index: int, progress: dict[str, object]) -> list[dict[str, object]]:
    raw_path = work / f"audio-{source_index}.txt"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-map", "0:a:0", "-af",
        "asetnsamples=n=2400:p=1,astats=metadata=1:reset=1,"
        f"ametadata=print:key=lavfi.astats.Overall.Peak_level:file={raw_path}",
        "-f", "null", "-"
    ], capture=False)
    raw_candidates: list[dict[str, object]] = []
    timestamp = 0.0
    last = -999.0
    for line in raw_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("frame:"):
            match = re.search(r"pts_time:([0-9.]+)", line)
            if match:
                timestamp = float(match.group(1))
        elif "lavfi.astats.Overall.Peak_level=" in line:
            try:
                peak = float(line.rsplit("=", 1)[1])
            except ValueError:
                continue
            if peak >= -12.0 and timestamp - last >= 1.5:
                raw_candidates.append({
                    "id": f"{source_index}-{len(raw_candidates) + 1:04d}",
                    "sourceIndex": source_index,
                    "timeSeconds": round(timestamp, 3),
                    "peakDb": round(peak, 3),
                    "label": None,
                    "reviewed": False,
                })
                last = timestamp
    # 같은 타격음·잔향으로 반복 검출된 후보를 하나의 검토 이벤트로 묶는다.
    candidates: list[dict[str, object]] = []
    for event in raw_candidates:
        if candidates and event["timeSeconds"] - candidates[-1]["timeSeconds"] < 4.0:
            current = candidates[-1]
            current["mergedCount"] = current.get("mergedCount", 1) + 1
            if event["peakDb"] > current["peakDb"]:
                current["timeSeconds"] = event["timeSeconds"]
                current["peakDb"] = event["peakDb"]
            continue
        event["id"] = f"{source_index}-{len(candidates) + 1:04d}"
        event["mergedCount"] = 1
        event["autoPriority"] = "high" if event["peakDb"] >= -6.0 else "normal"
        event["autoDisposition"] = "review-needed"
        candidates.append(event)
    progress.update({"stage": "audio-candidates", "sourceIndex": source_index,
                     "candidateCount": len(candidates)})
    return candidates


def visual_candidates(path: Path, work: Path, source_index: int, progress: dict[str, object], duration: float) -> list[dict[str, object]]:
    """오디오가 없어도 짧은 스트로크를 후보로 만들기 위한 화면 변화 후보."""
    raw_path = work / f"visual-{source_index}.txt"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path),
        "-vf", "fps=4,scale=160:-1,format=gray,tblend=all_mode=difference,"
        f"signalstats,metadata=print:key=lavfi.signalstats.YAVG:file={raw_path}",
        "-an", "-f", "null", "-", "-progress", "pipe:1", "-nostats"
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, bufsize=1)
    last_progress = 0.0
    assert process.stdout is not None
    for line in process.stdout:
        if not line.startswith("out_time_ms="):
            continue
        try:
            current = int(line.split("=", 1)[1]) / 1_000_000
        except ValueError:
            continue
        if current - last_progress >= 2.0 or current >= duration:
            percent = min(100.0, current / max(0.001, duration) * 100.0)
            write_progress(work, stage="visual-analysis", sourceIndex=source_index,
                           currentSeconds=round(current, 1), durationSeconds=round(duration, 1),
                           percent=round(percent, 1))
            last_progress = current
    process.wait()
    if process.returncode != 0:
        error = process.stderr.read()[-1000:] if process.stderr else "ffmpeg failed"
        write_progress(work, stage="error", sourceIndex=source_index, error=error)
        raise subprocess.CalledProcessError(process.returncode, command, error)
    samples: list[tuple[float, float]] = []
    timestamp = 0.0
    for line in raw_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("frame:"):
            match = re.search(r"pts_time:([0-9.]+)", line)
            if match:
                timestamp = float(match.group(1))
        elif "lavfi.signalstats.YAVG=" in line:
            try:
                samples.append((timestamp, float(line.rsplit("=", 1)[1])))
            except ValueError:
                pass
    if not samples:
        return []
    values = sorted(value for _, value in samples)
    median = values[len(values) // 2]
    threshold = max(10.0, median * 1.25)
    events: list[dict[str, object]] = []
    for index in range(1, len(samples) - 1):
        time_seconds, motion = samples[index]
        if motion < threshold or motion < samples[index - 1][1] or motion < samples[index + 1][1]:
            continue
        if events and time_seconds - events[-1]["timeSeconds"] < 1.5:
            continue
        events.append({
            "id": f"{source_index}-v{len(events) + 1:04d}",
            "sourceIndex": source_index,
            "timeSeconds": round(time_seconds, 3),
            "peakDb": -99.0,
            "visualMotion": round(motion, 3),
            "evidence": "visual",
            "label": None,
            "reviewed": False,
        })
    progress.update({"stage": "visual-candidates", "sourceIndex": source_index,
                     "visualCandidateCount": len(events), "visualThreshold": round(threshold, 3)})
    return events


def merge_candidate_events(events: list[dict[str, object]], source_index: int) -> list[dict[str, object]]:
    merged: list[dict[str, object]] = []
    for event in sorted(events, key=lambda item: item["timeSeconds"]):
        if merged and event["timeSeconds"] - merged[-1]["timeSeconds"] < 4.0:
            current = merged[-1]
            current["mergedCount"] = current.get("mergedCount", 1) + 1
            current["evidence"] = "audio+visual" if current.get("evidence") != event.get("evidence") else current.get("evidence")
            current["visualMotion"] = max(current.get("visualMotion", 0.0), event.get("visualMotion", 0.0))
            current["peakDb"] = max(current.get("peakDb", -99.0), event.get("peakDb", -99.0))
            continue
        copy = dict(event)
        copy["id"] = f"{source_index}-{len(merged) + 1:04d}"
        copy["mergedCount"] = 1
        copy["autoPriority"] = "high" if copy.get("evidence") == "audio+visual" else "visual-review"
        copy["autoDisposition"] = "review-needed"
        merged.append(copy)
    return merged


def triage(work: Path) -> None:
    path = work / "analysis.json"
    analysis = json.loads(path.read_text(encoding="utf-8"))
    auto_count = 0
    for candidate in analysis["candidates"]:
        strong_audio = candidate.get("peakDb", -99.0) >= -3.0
        strong_visual = candidate.get("visualMotion", 0.0) >= 18.0
        if strong_audio or strong_visual:
            candidate["autoConfirmed"] = True
            candidate["autoDecision"] = "likely-full-swing"
            candidate["autoReason"] = "강한 충격음 또는 큰 화면 동작"
            candidate["autoConfidence"] = "high"
            auto_count += 1
        else:
            candidate["autoConfirmed"] = False
            candidate["autoDecision"] = "needs-human-review"
    analysis["automaticTriage"] = {
        "autoConfirmedCount": auto_count,
        "humanReviewCount": len(analysis["candidates"]) - auto_count,
        "autoDecisionIsClubLabel": False,
        "note": "자동 확정은 명확한 큰 동작을 사람 검토에서 제외하는 단계이며 클럽 라벨을 확정하지 않는다."
    }
    path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")


def putter_review(work: Path) -> None:
    """Create a separate, wider-window review set for subtle putting strokes."""
    runtime = json.loads((work / "runtime-sources.json").read_text(encoding="utf-8"))
    prior = json.loads((work / "analysis.json").read_text(encoding="utf-8"))["candidates"]
    prior_review_labels: list[dict[str, object]] = []
    review_paths = list(work.glob("putter-review*.json"))
    final_review = work / "putter-final-30.json"
    if final_review.exists():
        review_paths.append(final_review)
    for review_path in review_paths:
        try:
            reviewed = [candidate for candidate in json.loads(review_path.read_text(encoding="utf-8")).get("candidates", [])
                        if candidate.get("reviewed")]
            prior.extend(reviewed)
            prior_review_labels.extend(reviewed)
        except (OSError, json.JSONDecodeError):
            continue
    anchors: dict[int, list[float]] = {}
    for candidate in prior:
        if candidate.get("autoConfirmed") or candidate.get("label") == "driver":
            anchors.setdefault(candidate["sourceIndex"], []).append(candidate["timeSeconds"])
    for source_index in anchors:
        anchors[source_index].sort()
    candidates: list[dict[str, object]] = []
    for source_index, _ in enumerate(runtime["paths"]):
        raw_path = work / f"visual-{source_index}.txt"
        if not raw_path.exists():
            continue
        samples: list[tuple[float, float]] = []
        timestamp = 0.0
        for line in raw_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("frame:"):
                match = re.search(r"pts_time:([0-9.]+)", line)
                if match:
                    timestamp = float(match.group(1))
            elif "lavfi.signalstats.YAVG=" in line:
                try:
                    samples.append((timestamp, float(line.rsplit("=", 1)[1])))
                except ValueError:
                    pass
        if not samples:
            continue
        p90 = statistics.quantiles([value for _, value in samples], n=10)[8]
        # Do not treat tiny brightness noise as movement. The old floor of 5.0
        # admitted long waiting scenes; use a stronger dynamic floor and a
        # local peak contrast below.
        threshold = max(6.0, p90 * 1.10)
        last_time = -999.0
        for index in range(1, len(samples) - 1):
            time_seconds, motion = samples[index]
            if (motion < threshold or motion < samples[index - 1][1] or
                    motion < samples[index + 1][1] or
                    motion - min(samples[index - 1][1], samples[index + 1][1]) < 0.25):
                continue
            if time_seconds - last_time < 2.0:
                continue
            nearby = [candidate for candidate in prior
                      if candidate["sourceIndex"] == source_index
                      and abs(candidate["timeSeconds"] - time_seconds) < 2.0
                      and candidate.get("reviewed")]
            has_known_putter = any(candidate.get("label") == "putter" for candidate in nearby)
            # The user's “putter before driver” observation is a temporal prior,
            # not a rule that makes every pre-driver frame a putt. Keep candidates
            # without an anchor and use the prior only to rank them.
            next_anchor = next((anchor for anchor in anchors.get(source_index, [])
                                if anchor > time_seconds + 3.0), None)
            # Recall-first pass: subtle putting motion can be below the old
            # 5.5 cutoff. Keep a wider candidate pool; labels still require
            # visual confirmation and are never inferred from the sequence prior.
            if motion > 7.0 and not has_known_putter:
                continue
            if nearby and not has_known_putter:
                continue
            restored = next((candidate for candidate in prior_review_labels
                             if candidate["sourceIndex"] == source_index
                             and abs(candidate["timeSeconds"] - time_seconds) < 0.25), None)
            seconds_before_anchor = (next_anchor - time_seconds) if next_anchor is not None else None
            in_sequence_window = seconds_before_anchor is not None and 3.0 <= seconds_before_anchor <= 45.0
            proximity_score = 0.0
            if in_sequence_window:
                proximity_score = 1.0 - min(abs(seconds_before_anchor - 18.0) / 27.0, 1.0)
            # A putt is a short but real stroke: completely static frames are
            # often waiting/non-shot frames. Prefer the observed middle-motion
            # band, while keeping the sequence prior deliberately weak.
            motion_score = max(0.0, 1.0 - abs(motion - 5.8) / 2.2)
            priority_score = motion_score
            candidates.append({
                "id": f"{source_index}-p{len(candidates) + 1:04d}",
                "sourceIndex": source_index,
                "timeSeconds": round(time_seconds, 3),
                "peakDb": -99.0,
                "visualMotion": round(motion, 3),
                "label": restored.get("label") if restored else None,
                "reviewed": bool(restored),
                "windowSeconds": 6,
                "evidence": "subtle-visual-candidate",
                "nextDriverAnchorSeconds": round(next_anchor, 3) if next_anchor is not None else None,
                "sequencePrior": in_sequence_window,
                "priorityScore": round(priority_score, 4),
            })
            last_time = time_seconds
    candidates.sort(key=lambda candidate: (-float(candidate["priorityScore"]),
                                           int(bool(candidate.get("reviewed"))),
                                           int(candidate["sourceIndex"]),
                                           float(candidate["timeSeconds"])))
    target = work / "putter-review.json"
    if target.exists():
        backup = work / f"putter-review-backup-{int(time.time())}.json"
        shutil.copy2(target, backup)
    target.write_text(json.dumps({"version": 1, "purpose": "퍼터 스트로크 중심 재검토용 넓은 화면 후보", "candidates": candidates,
                                  "minimumCandidateFloor": 30,
                                  "candidateFloorSatisfied": len(candidates) >= 30,
                                  "note": "퍼터 여부만 확인한다. 후보 시각은 스트로크 중심의 추정치이며 최종 정답이 아니다."},
                                 ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selected = []
    review_pool = [candidate for candidate in candidates
                   if not candidate.get("reviewed") and candidate.get("label") is None]
    # The first validation showed that sequencePrior alone promoted waiting
    # scenes. For the next batch, diversify toward candidates outside that
    # prior while keeping the measured middle-motion band.
    review_pool.sort(key=lambda candidate: (
        -max(0.0, 1.0 - abs(float(candidate["visualMotion"]) - 5.8) / 2.2),
        int(candidate["sourceIndex"]), float(candidate["timeSeconds"])))
    selected.extend(candidate for candidate in review_pool if candidate not in selected)
    selected = selected[:30]
    (work / "putter-final-30.json").write_text(
        json.dumps({"version": 1, "purpose": "최종 퍼터 후보 30개 재검증 목록", "candidates": selected,
                    "selectionRule": "기존 라벨 구간을 제외한 미검토 후보 중 priorityScore 순. 퍼터 확정 수가 아니라 재검증용 후보 목록.",
                    "revalidationRequired": True}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"putterReviewCandidates": len(candidates), "path": str(target)}, ensure_ascii=False))


def scan(paths: list[Path], work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    existing = work / "analysis.json"
    if existing.exists():
        backup = work / f"analysis-backup-{int(time.time())}.json"
        backup.write_bytes(existing.read_bytes())
    write_progress(work, stage="starting", completed=0, total=len(paths))
    sources = []
    candidates = []
    for index, path in enumerate(paths):
        progress = {"stage": "metadata", "sourceIndex": index,
                    "completed": index, "total": len(paths)}
        write_progress(work, **progress)
        metadata = probe(path)
        sources.append({"sourceIndex": index, "metadata": metadata})
        audio = audio_candidates(path, work, index, progress)
        visual = visual_candidates(path, work, index, progress, float(metadata["durationSeconds"]))
        candidates.extend(merge_candidate_events(audio + visual, index))
        write_progress(work, stage="source-complete", sourceIndex=index,
                       completed=index + 1, total=len(paths),
                       candidateCount=len(candidates))
    analysis = {
        "schemaVersion": 1,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "privacy": {"rawMediaStoredInRepository": False, "sourceNamesStored": False},
        "sources": sources,
        "candidates": candidates,
        "labels": LABELS,
    }
    (work / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    # 원본 경로는 로컬 서버가 미리보기를 만들 때만 사용하며, 분석 요약에는 기록하지 않는다.
    (work / "runtime-sources.json").write_text(
        json.dumps({"paths": [str(path) for path in paths]}, ensure_ascii=False),
        encoding="utf-8"
    )
    write_progress(work, stage="complete", completed=len(paths), total=len(paths),
                   candidateCount=len(candidates))


HTML = """<!doctype html><meta charset=utf-8><title>한양 영상 분석</title>
<style>body{font:15px system-ui;margin:24px;background:#f5f6f8;color:#18202a}main{max-width:760px;margin:auto}header{position:sticky;top:0;background:#f5f6f8;padding-bottom:12px;z-index:2}.bar{height:10px;background:#d8dde5;border-radius:9px}.fill{height:100%;background:#276ef1;border-radius:9px;width:0}.card{background:white;border:1px solid #dfe3e8;border-radius:12px;padding:20px;margin:16px 0}.meta{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}.time{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}.video{width:100%;max-height:65vh;background:#111;border-radius:8px}.video-track{height:8px;background:#d8dde5;border-radius:8px;margin-top:8px;overflow:hidden}.video-progress{height:100%;width:0;background:#276ef1;border-radius:8px;transition:width .08s linear}.video-time{display:block;text-align:right;color:#667085;font-size:12px;margin-top:4px}.buttons{display:flex;flex-wrap:wrap;gap:8px;margin-top:16px}.buttons button,.nav button{border:1px solid #c9ced6;background:#fff;border-radius:8px;padding:10px 14px;cursor:pointer;font-size:15px}.buttons button.active{background:#276ef1;color:#fff;border-color:#276ef1}.nav{display:flex;justify-content:space-between;gap:8px;margin-top:12px}.nav button{flex:1}.muted{color:#667085}.hidden{display:none}</style>
<main><header><h1>한양 영상 협업 분석</h1><div id=status>상태 읽는 중…</div><div class=bar><div id=fill class=fill></div></div><p class=muted>퍼팅과 연결된 준비·스트로크·직후 장면은 퍼터로 분류합니다. 영상 아래 진행바는 항상 표시되며, 라벨 선택 또는 이전·다음 조작 때만 후보가 이동합니다.</p></header><section id=viewer></section></main>
<script>
const mode=new URLSearchParams(location.search).get('mode');const engineMode=mode==='putter-engine'||mode==='putter-engine-uncertain';const putterMode=engineMode||mode==='putter'||mode==='putter-final';const finalMode=mode==='putter-final';const labels=putterMode?{putter:'퍼터',nonPutter:'퍼터 아님','non-shot':'비샷'}:{putter:'퍼터',wedge:'웻지',iron:'아이언',wood:'우드',driver:'드라이버','non-shot':'비샷',unknown:'모르겠음'};let analysis=null;let index=0;let renderedId=null;let currentId=null;let advanceRequested=false;function queue(){return ((analysis&&analysis.candidates)||[]).filter(x=>finalMode||!x.reviewed)}
async function get(path, options){return fetch(path,options).then(r=>r.json())}
function completedCount(){return (analysis.candidates||[]).filter(x=>x.reviewed||x.autoConfirmed).length}
function finalReviewedCount(){return finalMode?(analysis.candidates||[]).filter(x=>x.reviewed).length:0}
function render(){const all=queue();if(!all.length){document.querySelector('#viewer').innerHTML=`<article class=card>${putterMode?'퍼터 재검토가 완료되었습니다.':'대표님이 확인할 후보가 없습니다. 전체 분석과 라벨링이 완료되었습니다.'}</article>`;return}if(index<0)index=0;if(index>=all.length)index=all.length-1;const c=all[index];currentId=c.id;document.querySelector('#status').textContent=window.progressText||`미확인 ${index+1}/${all.length}`;document.querySelector('#fill').style.width=(completedCount()/Math.max(1,analysis.candidates.length)*100)+'%';if(renderedId===c.id){return}renderedId=c.id;document.querySelector('#viewer').innerHTML=`<article class=card><div class=meta><span class=time>${c.timeSeconds.toFixed(1)}초</span><span class=muted>${c.id} · ${finalMode?'최종 30개 재검증 · 6초 구간':engineMode?'통합 엔진 후보 · 6초 구간':putterMode?'퍼터 후보 · 6초 구간':`peak ${c.peakDb.toFixed(1)} dB · ${c.mergedCount||1}개 묶음`}</span></div><video class=video controls autoplay preload=metadata src="/api/preview?source=${c.sourceIndex}&time=${c.timeSeconds}${putterMode?'&mode=putter':''}"></video><div class=video-track><div id=video-progress class=video-progress></div></div><span id=video-time class=video-time>0:00 / 0:00</span><div class=buttons>${Object.entries(labels).map(([key,name])=>`<button data-label="${key}" class="${c.label===key?'active':''}">${name}</button>`).join('')}</div><div class=nav><button id=prev>← 이전</button><button id=next>다음 →</button></div></article>`;const video=document.querySelector('.video');const progress=document.querySelector('#video-progress');const videoTime=document.querySelector('#video-time');const updateVideoProgress=()=>{const duration=Number.isFinite(video.duration)?video.duration:6;const current=video.currentTime||0;progress.style.width=Math.min(100,current/duration*100)+'%';videoTime.textContent=`${Math.floor(current/60)}:${String(Math.floor(current%60)).padStart(2,'0')} / ${Math.floor(duration/60)}:${String(Math.floor(duration%60)).padStart(2,'0')}`};video.addEventListener('loadedmetadata',updateVideoProgress);video.addEventListener('timeupdate',updateVideoProgress);document.querySelectorAll('[data-label]').forEach(b=>b.onclick=async()=>{advanceRequested=true;const labelRoute=engineMode?'/api/putter-engine-label':putterMode?'/api/putter-label':'/api/label';await get(labelRoute,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:c.id,label:b.dataset.label})});c.label=b.dataset.label;c.reviewed=true;index=Math.min(index+1,all.length-1);render();advanceRequested=false});document.querySelector('#prev').onclick=()=>{index=Math.max(0,index-1);render()};document.querySelector('#next').onclick=()=>{index=Math.min(all.length-1,index+1);render()}}
async function refresh(){const [p,a]=await Promise.all([get('/api/progress'),get(finalMode?'/api/putter-final':engineMode?'/api/putter-engine':putterMode?'/api/putter-analysis':'/api/analysis')]);const oldId=currentId;analysis=a;const progress=p.percent!==undefined?` · ${p.sourceIndex+1}/${p.total}번 영상 ${p.currentSeconds||0}/${p.durationSeconds||0}초 (${p.percent}%)`:'';window.progressText=finalMode?`최종 30개 재검증${progress} · 확인 ${finalReviewedCount()}/${a.candidates.length} · 남음 ${a.candidates.length-finalReviewedCount()}`:`${engineMode?'통합 엔진 후보 검토':putterMode?'퍼터 재검토':'complete'}${progress} · ${queue().length}개`;const all=queue();const sameIndex=oldId?all.findIndex(x=>x.id===oldId):-1;if(oldId&&sameIndex<0&&!advanceRequested){document.querySelector('#status').textContent=window.progressText;return}if(sameIndex>=0)index=sameIndex;if(renderedId!==queue()[index]?.id)render();else{document.querySelector('#status').textContent=window.progressText;document.querySelector('#fill').style.width=(finalMode?finalReviewedCount():completedCount())/Math.max(1,a.candidates.length)*100+'%'}}
refresh();setInterval(refresh,5000);document.addEventListener('keydown',e=>{if(e.key==='ArrowLeft'){index=Math.max(0,index-1);render()}if(e.key==='ArrowRight'){index=Math.min(queue().length-1,index+1);render()}const shortcuts=putterMode?{',':'putter','.':'nonPutter','/':'non-shot'}:{',':'putter','.':'unknown','/':'non-shot'};const label=shortcuts[e.key];if(label){e.preventDefault();document.querySelector(`[data-label="${label}"]`)?.click()}});
</script>"""


class Handler(BaseHTTPRequestHandler):
    work: Path

    def send_json(self, value: object, status: int = 200) -> None:
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/":
            body = HTML.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if parsed.path == "/api/progress":
            self.send_json(json.loads((self.work / "progress.json").read_text(encoding="utf-8"))); return
        if parsed.path == "/api/analysis":
            self.send_json(json.loads((self.work / "analysis.json").read_text(encoding="utf-8"))); return
        if parsed.path == "/api/putter-analysis":
            self.send_json(json.loads((self.work / "putter-review.json").read_text(encoding="utf-8"))); return
        if parsed.path == "/api/putter-final":
            self.send_json(json.loads((self.work / "putter-final-30.json").read_text(encoding="utf-8"))); return
        if parsed.path in {"/api/putter-engine", "/api/putter-engine-uncertain"}:
            unified = self.work / "putter-engine-review-all.json"
            fallback = self.work / "putter-engine-review-30.json"
            self.send_json(json.loads((unified if unified.exists() else fallback).read_text(encoding="utf-8"))); return
        if parsed.path == "/api/preview":
            query = parse_qs(parsed.query)
            source = int(query.get("source", ["0"])[0]); timestamp = float(query.get("time", ["0"])[0])
            sources = json.loads((self.work / "runtime-sources.json").read_text(encoding="utf-8"))["paths"]
            if source < 0 or source >= len(sources): self.send_error(400); return
            preview_dir = self.work / "previews"; preview_dir.mkdir(exist_ok=True)
            wide = query.get("mode", [""])[0] == "putter"
            preview = preview_dir / f"{source}-{int(timestamp * 10)}-{'wide8' if wide else 'normal'}.mp4"
            if not preview.exists():
                start = max(0.0, timestamp - (4.0 if wide else 2.0))
                run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}",
                     "-i", sources[source], "-t", "8" if wide else "4", "-vf", "scale=640:-2", "-c:v", "libx264",
                     "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(preview)], capture=False)
            body = preview.read_bytes(); self.send_response(200); self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        route = urlparse(self.path).path
        if route not in {"/api/label", "/api/putter-label", "/api/putter-engine-label", "/api/putter-engine-uncertain-label"}: self.send_error(404); return
        length = int(self.headers.get("Content-Length", "0")); payload = json.loads(self.rfile.read(length))
        engine_route = route in {"/api/putter-engine-label", "/api/putter-engine-uncertain-label"}
        analysis_path = self.work / ("putter-engine-review-all.json" if engine_route else "putter-review.json" if route == "/api/putter-label" else "analysis.json")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        for candidate in analysis["candidates"]:
            if candidate["id"] == payload.get("id"):
                candidate["label"] = payload.get("label"); candidate["reviewed"] = True; break
        analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
        if route == "/api/putter-label":
            final_path = self.work / "putter-final-30.json"
            if final_path.exists():
                final = json.loads(final_path.read_text(encoding="utf-8"))
                for candidate in final.get("candidates", []):
                    if candidate["id"] == payload.get("id"):
                        candidate["label"] = payload.get("label"); candidate["reviewed"] = True; break
                final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
        history_path = self.work / ("putter-engine-label-history.jsonl" if engine_route else "putter-label-history.jsonl" if route == "/api/putter-label" else "label-history.jsonl")
        with history_path.open("a", encoding="utf-8") as history:
            history.write(json.dumps({"id": payload.get("id"), "label": payload.get("label"),
                                      "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False) + "\n")
        self.send_json({"ok": True})


def serve(work: Path, port: int) -> None:
    Handler.work = work
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"한양 분석 화면: http://127.0.0.1:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan"); scan_parser.add_argument("--work-dir", type=Path, default=Path("Work/hanai-video-analysis")); scan_parser.add_argument("videos", nargs="+", type=Path)
    serve_parser = sub.add_parser("serve"); serve_parser.add_argument("--work-dir", type=Path, default=Path("Work/hanai-video-analysis")); serve_parser.add_argument("--port", type=int, default=8765)
    triage_parser = sub.add_parser("triage"); triage_parser.add_argument("--work-dir", type=Path, default=Path("Work/hanai-video-analysis"))
    putter_parser = sub.add_parser("putter-review"); putter_parser.add_argument("--work-dir", type=Path, default=Path("Work/hanai-video-analysis"))
    args = parser.parse_args()
    if args.command == "scan": scan(args.videos, args.work_dir)
    elif args.command == "triage": triage(args.work_dir)
    elif args.command == "putter-review": putter_review(args.work_dir)
    else: serve(args.work_dir, args.port)


if __name__ == "__main__": main()
