package com.hanai.core

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/** 자세(관절) 기반 스윙 단계. */
enum class GolfSwingPosePhase(val rawValue: String) {
    SEEKING_ADDRESS("seekingAddress"),
    ADDRESSED("addressed"),
    BACKSWING("backswing"),
    IMPACT_WINDOW("impactWindow");

    companion object {
        fun fromRawValue(rawValue: String): GolfSwingPosePhase? =
            values().firstOrNull { it.rawValue == rawValue }
    }
}

/**
 * 한 번의 자세 추정 결과를 정규화한 샘플. 관절 원본 좌표는 넘기지 않는다.
 *
 * @property handX 몸 크기([bodyScale])로 정규화한 손(손목 평균) X
 * @property handY 몸 크기로 정규화한 손 Y
 * @property coreX 어깨·골반 중심 X(정규화 화면 좌표)
 * @property coreY 어깨·골반 중심 Y(정규화 화면 좌표)
 * @property bodyScale 어깨 폭 등 몸 크기(정규화 화면 단위)
 * @property confidence 자세 추정 신뢰도(0..1)
 */
data class GolfSwingPoseSample(
    val time: Double,
    val handX: Double,
    val handY: Double,
    val coreX: Double,
    val coreY: Double,
    val bodyScale: Double,
    val confidence: Double
)

data class GolfSwingPoseSignal(
    val phase: GolfSwingPosePhase,
    val confidence: Double,
    val impactWindowStart: Double?,
    val impactWindowEnd: Double?
) {
    fun isImpactWindow(time: Double): Boolean {
        val start = impactWindowStart ?: return false
        val end = impactWindowEnd ?: return false
        return phase == GolfSwingPosePhase.IMPACT_WINDOW && time >= start && time <= end
    }
}

/**
 * 자세 스윙 상태기계: 정지 주소 → 손 이동(백스윙) → 빠른 복귀 → 임팩트 창.
 *
 * 임팩트 창은 복귀 감지 시각 기준 `[-0.45초, +0.30초]`다.
 * 좌타·우타·미러에 공통인 "주소 지점 대비 상대 이동축"만 사용한다.
 */
class GolfSwingPoseAnalyzer {
    var phase: GolfSwingPosePhase = GolfSwingPosePhase.SEEKING_ADDRESS
        private set

    private var quietSince: Double? = null
    private var addressX = 0.0
    private var addressY = 0.0
    private var addressSamples = 0
    private var previousSample: GolfSwingPoseSample? = null
    private var backswingDirectionX = 0.0
    private var backswingDirectionY = 0.0
    private var backswingStart: Double? = null
    private var peakProgress = 0.0
    private var previousProgress = 0.0
    private var downswingSamples = 0
    private var impactWindowStart: Double? = null
    private var impactWindowEnd: Double? = null
    private var latestConfidence = 0.0

    fun reset() {
        phase = GolfSwingPosePhase.SEEKING_ADDRESS
        quietSince = null
        addressX = 0.0
        addressY = 0.0
        addressSamples = 0
        previousSample = null
        backswingDirectionX = 0.0
        backswingDirectionY = 0.0
        backswingStart = null
        peakProgress = 0.0
        previousProgress = 0.0
        downswingSamples = 0
        impactWindowStart = null
        impactWindowEnd = null
        latestConfidence = 0.0
    }

    fun observe(sample: GolfSwingPoseSample): GolfSwingPoseSignal {
        if (sample.confidence < 0.45 || sample.bodyScale < 0.04) {
            return currentSignal(sample.time)
        }
        latestConfidence = sample.confidence

        previousSample?.let { previous ->
            val sampleGap = sample.time - previous.time
            val scaleChange = abs(sample.bodyScale - previous.bodyScale) / max(0.001, previous.bodyScale)
            if (sampleGap <= 0.0 || sampleGap > 0.6 || scaleChange > 0.25) reset()
        }
        val previous = previousSample
        val result = step(sample, previous)
        previousSample = sample
        return result
    }

    private fun step(sample: GolfSwingPoseSample, previous: GolfSwingPoseSample?): GolfSwingPoseSignal {
        when (phase) {
            GolfSwingPosePhase.SEEKING_ADDRESS -> {
                if (previous == null) {
                    beginAddressAverage(sample)
                    return currentSignal(sample.time)
                }
                val deltaTime = max(0.05, sample.time - previous.time)
                val handSpeed = hypot(sample.handX - previous.handX, sample.handY - previous.handY) / deltaTime
                val coreSpeed = hypot(sample.coreX - previous.coreX, sample.coreY - previous.coreY) /
                    deltaTime / max(0.04, sample.bodyScale)
                if (handSpeed <= 0.22 && coreSpeed <= 0.20) {
                    quietSince = quietSince ?: previous.time
                    addToAddressAverage(sample)
                    if (sample.time - (quietSince ?: sample.time) >= 0.55 && addressSamples >= 3) {
                        phase = GolfSwingPosePhase.ADDRESSED
                    }
                } else {
                    quietSince = null
                    beginAddressAverage(sample)
                }
            }

            GolfSwingPosePhase.ADDRESSED -> {
                val dx = sample.handX - addressX
                val dy = sample.handY - addressY
                val displacement = hypot(dx, dy)
                if (displacement >= 0.20) {
                    backswingDirectionX = dx / displacement
                    backswingDirectionY = dy / displacement
                    backswingStart = sample.time
                    peakProgress = displacement
                    previousProgress = displacement
                    downswingSamples = 0
                    phase = GolfSwingPosePhase.BACKSWING
                }
            }

            GolfSwingPosePhase.BACKSWING -> {
                val start = backswingStart
                if (start == null || sample.time - start > 1.8) {
                    reset()
                    return currentSignal(sample.time)
                }
                val elapsed = sample.time - start
                val progress = (sample.handX - addressX) * backswingDirectionX +
                    (sample.handY - addressY) * backswingDirectionY
                peakProgress = max(peakProgress, progress)
                val deltaTime = max(0.05, sample.time - (previous?.time ?: (sample.time - 0.2)))
                val returnSpeed = (previousProgress - progress) / deltaTime
                if (elapsed >= 0.18 &&
                    peakProgress >= 0.28 &&
                    progress < previousProgress &&
                    returnSpeed >= 0.55
                ) {
                    downswingSamples += 1
                } else if (progress >= previousProgress) {
                    downswingSamples = 0
                }
                previousProgress = progress

                val returnedEnough = peakProgress - progress >= max(0.18, peakProgress * 0.55)
                if (downswingSamples >= 2 && returnedEnough) {
                    phase = GolfSwingPosePhase.IMPACT_WINDOW
                    impactWindowStart = sample.time - 0.45
                    impactWindowEnd = sample.time + 0.30
                }
            }

            GolfSwingPosePhase.IMPACT_WINDOW -> {
                if (sample.time > (impactWindowEnd ?: sample.time)) reset()
            }
        }
        return currentSignal(sample.time)
    }

    fun currentSignal(time: Double): GolfSwingPoseSignal = when (phase) {
        GolfSwingPosePhase.IMPACT_WINDOW -> GolfSwingPoseSignal(
            GolfSwingPosePhase.IMPACT_WINDOW,
            min(latestConfidence, min(1.0, 0.72 + peakProgress * 0.45)),
            impactWindowStart,
            impactWindowEnd
        )
        GolfSwingPosePhase.BACKSWING -> GolfSwingPoseSignal(
            GolfSwingPosePhase.BACKSWING,
            min(0.7, 0.35 + peakProgress),
            null,
            null
        )
        GolfSwingPosePhase.ADDRESSED -> GolfSwingPoseSignal(GolfSwingPosePhase.ADDRESSED, 0.32, null, null)
        GolfSwingPosePhase.SEEKING_ADDRESS -> GolfSwingPoseSignal(GolfSwingPosePhase.SEEKING_ADDRESS, 0.0, null, null)
    }

    private fun beginAddressAverage(sample: GolfSwingPoseSample) {
        addressX = sample.handX
        addressY = sample.handY
        addressSamples = 1
    }

    private fun addToAddressAverage(sample: GolfSwingPoseSample) {
        addressSamples += 1
        val weight = 1.0 / addressSamples.toDouble()
        addressX += (sample.handX - addressX) * weight
        addressY += (sample.handY - addressY) * weight
    }
}
