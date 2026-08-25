package com.hanai.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** 화면 격자 움직임 기반 스윙 단계. */
enum class GolfSwingMotionPhase(val rawValue: String) {
    SEEKING_ADDRESS("seekingAddress"),
    ADDRESSED("addressed"),
    BACKSWING("backswing"),
    DOWNSWING("downswing");

    companion object {
        fun fromRawValue(rawValue: String): GolfSwingMotionPhase? =
            values().firstOrNull { it.rawValue == rawValue }
    }
}

/**
 * 한 프레임의 화면 움직임 요약. adapter가 저해상도 격자 차분에서 계산한다.
 *
 * @property localMotion 가장 활발한 국소 영역의 움직임(0..1)
 * @property globalMotion 화면 전체 평균 움직임(0..1)
 * @property widespreadMotion 움직인 격자 비율(0..1)
 * @property concentration 움직임이 한 영역에 집중된 정도(0..1)
 * @property brightnessChange 평균 밝기 변화량(0..1)
 * @property dominantRegion 가장 활발한 영역 인덱스(격자 열 등 adapter 정의)
 */
data class GolfSwingVisualSample(
    val time: Double,
    val localMotion: Double,
    val globalMotion: Double,
    val widespreadMotion: Double,
    val concentration: Double,
    val brightnessChange: Double,
    val dominantRegion: Int
)

data class GolfSwingMotionSignal(
    val phase: GolfSwingMotionPhase,
    val confidence: Double,
    val impactTime: Double?
) {
    val isImpactWindow: Boolean
        get() = phase == GolfSwingMotionPhase.DOWNSWING && impactTime != null
}

/**
 * 화면 움직임 상태기계: 정지(주소) → 국소 백스윙 → 가속 다운스윙.
 *
 * 화면 전체가 함께 움직이는 팬·흔들림·밝기 급변은 스윙 근거에서 제외하고
 * 두 번 연속이면 상태를 초기화한다. [lastGlobalChangeTime]은 무음 퍼팅
 * 장면 안정성 판정용이며 [reset]으로 지워지지 않는다.
 */
class GolfSwingMotionAnalyzer {
    var phase: GolfSwingMotionPhase = GolfSwingMotionPhase.SEEKING_ADDRESS
        private set
    var lastGlobalChangeTime: Double = Double.NEGATIVE_INFINITY
        private set

    private var quietSince: Double? = null
    private var motionCandidateSince: Double? = null
    private var motionCandidateCount = 0
    private var backswingStart: Double? = null
    private var backswingPeak = 0.0
    private var backswingSamples = 0
    private var previousMotion = 0.0
    private var lockedRegion: Int? = null
    private var downswingTime: Double? = null
    private var globalMotionCount = 0

    fun reset() {
        phase = GolfSwingMotionPhase.SEEKING_ADDRESS
        quietSince = null
        motionCandidateSince = null
        motionCandidateCount = 0
        backswingStart = null
        backswingPeak = 0.0
        backswingSamples = 0
        previousMotion = 0.0
        lockedRegion = null
        downswingTime = null
        globalMotionCount = 0
    }

    fun observe(sample: GolfSwingVisualSample): GolfSwingMotionSignal {
        val isGlobalChange = sample.globalMotion >= 0.16 ||
            sample.widespreadMotion >= 0.68 ||
            sample.brightnessChange >= 0.14
        if (isGlobalChange) {
            lastGlobalChangeTime = sample.time
            globalMotionCount += 1
            if (globalMotionCount >= 2) reset()
            return currentSignal(sample.time)
        }
        globalMotionCount = 0

        when (phase) {
            GolfSwingMotionPhase.SEEKING_ADDRESS -> {
                val isQuiet = sample.localMotion <= 0.075 &&
                    sample.globalMotion <= 0.08 &&
                    sample.widespreadMotion <= 0.32
                if (isQuiet) {
                    quietSince = quietSince ?: sample.time
                    if (sample.time - (quietSince ?: sample.time) >= 0.55) {
                        phase = GolfSwingMotionPhase.ADDRESSED
                        motionCandidateSince = null
                        motionCandidateCount = 0
                    }
                } else {
                    quietSince = null
                }
            }

            GolfSwingMotionPhase.ADDRESSED -> {
                val isBackswingCandidate = sample.localMotion >= 0.12 &&
                    sample.concentration >= 0.38 &&
                    sample.widespreadMotion >= 0.04 &&
                    sample.widespreadMotion <= 0.58
                if (isBackswingCandidate) {
                    val since = motionCandidateSince
                    if (since != null &&
                        sample.time - since <= 0.35 &&
                        abs((lockedRegion ?: sample.dominantRegion) - sample.dominantRegion) <= 1
                    ) {
                        motionCandidateCount += 1
                    } else {
                        motionCandidateSince = sample.time
                        motionCandidateCount = 1
                        lockedRegion = sample.dominantRegion
                    }
                    if (motionCandidateCount >= 2) {
                        phase = GolfSwingMotionPhase.BACKSWING
                        backswingStart = motionCandidateSince
                        backswingPeak = sample.localMotion
                        backswingSamples = motionCandidateCount
                        previousMotion = sample.localMotion
                    }
                } else if (sample.localMotion <= 0.085) {
                    motionCandidateSince = null
                    motionCandidateCount = 0
                    lockedRegion = null
                }
            }

            GolfSwingMotionPhase.BACKSWING -> {
                val start = backswingStart
                if (start == null) {
                    reset()
                    return currentSignal(sample.time)
                }
                val elapsed = sample.time - start
                val movedToAnotherRegion = abs(
                    (lockedRegion ?: sample.dominantRegion) - sample.dominantRegion
                ) > 1 && sample.localMotion >= 0.18
                if (elapsed > 1.8 || movedToAnotherRegion) {
                    reset()
                    return currentSignal(sample.time)
                }
                if (sample.localMotion >= 0.10) backswingSamples += 1
                val acceleration = sample.localMotion - previousMotion
                val previousPeak = backswingPeak
                val isDownswing = elapsed >= 0.18 &&
                    backswingSamples >= 3 &&
                    sample.localMotion >= 0.20 &&
                    (acceleration >= 0.035 || sample.localMotion >= max(0.26, previousPeak * 1.15))
                backswingPeak = max(backswingPeak, sample.localMotion)
                previousMotion = sample.localMotion
                if (isDownswing) {
                    phase = GolfSwingMotionPhase.DOWNSWING
                    downswingTime = sample.time
                }
            }

            GolfSwingMotionPhase.DOWNSWING -> {
                val time = downswingTime
                if (time != null && sample.time - time > 0.42) reset()
            }
        }
        return currentSignal(sample.time)
    }

    fun currentSignal(time: Double): GolfSwingMotionSignal = when (phase) {
        GolfSwingMotionPhase.DOWNSWING -> {
            val impactTime = downswingTime
            if (impactTime == null || time - impactTime > 0.42) {
                GolfSwingMotionSignal(GolfSwingMotionPhase.SEEKING_ADDRESS, 0.0, null)
            } else {
                GolfSwingMotionSignal(
                    GolfSwingMotionPhase.DOWNSWING,
                    max(0.72, min(1.0, 0.62 + backswingPeak * 0.95)),
                    impactTime
                )
            }
        }
        GolfSwingMotionPhase.BACKSWING -> GolfSwingMotionSignal(
            GolfSwingMotionPhase.BACKSWING,
            min(0.7, 0.34 + backswingPeak),
            null
        )
        GolfSwingMotionPhase.ADDRESSED -> GolfSwingMotionSignal(
            GolfSwingMotionPhase.ADDRESSED,
            0.28,
            null
        )
        GolfSwingMotionPhase.SEEKING_ADDRESS -> GolfSwingMotionSignal(
            GolfSwingMotionPhase.SEEKING_ADDRESS,
            0.0,
            null
        )
    }
}
