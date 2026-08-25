package com.hanai.core

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/** 무음 퍼팅 상태기계 단계. */
enum class GolfPuttStrokePhase(val rawValue: String) {
    SEEKING_ADDRESS("seekingAddress"),
    ADDRESSED("addressed"),
    BACKSWING("backswing"),
    FORWARD_STROKE("forwardStroke"),
    CONFIRMED_STROKE("confirmedStroke");

    companion object {
        fun fromRawValue(rawValue: String): GolfPuttStrokePhase? =
            values().firstOrNull { it.rawValue == rawValue }
    }
}

data class GolfPuttStrokeSignal(
    val phase: GolfPuttStrokePhase,
    val confidence: Double,
    val strokeTime: Double?
) {
    val isConfirmedStroke: Boolean
        get() = phase == GolfPuttStrokePhase.CONFIRMED_STROKE && strokeTime != null
}

/**
 * 무음 퍼팅 상태기계 (모델 0.6.0).
 *
 * 정지 주소(0.60초 이상, 3샘플 이상) → 작은 백스윙(몸 크기 대비 0.06 이상 시작,
 * 최고 0.12 이상 0.60 이하) → 주소 지점을 지나는 전진 스트로크(복귀 2샘플 이상)
 * → 짧은 팔로스루(2샘플 이상)가 순서대로 이어질 때만 확정한다.
 *
 * 아이언 오탐 방지:
 * - 백스윙 폭이 0.60을 넘으면 스윙으로 보고 초기화
 * - 주소 지점을 1.5/s보다 빠르게 통과하면(return speed) 스윙으로 보고 초기화
 * - 팔로스루가 `max(0.30, peak×1.2)`를 넘으면(큰 follow-through) 초기화
 * - 몸통(core) 이동 속도가 0.9/s를 넘으면(걷기·이동·큰 회전) 어느 단계든 초기화
 *
 * 확정 뒤 2.0초 동안은 재확정하지 않으며, 0.7.0 확정 신호는 0.60초 동안 latch로 유지된다.
 */
class GolfPuttStrokeAnalyzer(
    modelVersion: GolfModelVersion = GolfModelVersion.current
) {
    private val confirmationLatchDuration = if (modelVersion == GolfModelVersion.V0_7_0) 0.60 else 0.35
    var phase: GolfPuttStrokePhase = GolfPuttStrokePhase.SEEKING_ADDRESS
        private set
    var lastConfirmedTime: Double = Double.NEGATIVE_INFINITY
        private set

    private var quietSince: Double? = null
    private var addressX = 0.0
    private var addressY = 0.0
    private var addressSamples = 0
    private var previousSample: GolfSwingPoseSample? = null
    private var directionX = 0.0
    private var directionY = 0.0
    private var backswingStart: Double? = null
    private var peakProgress = 0.0
    private var previousProgress = 0.0
    private var returningSamples = 0
    private var followThroughSamples = 0
    private var strokeTime: Double? = null
    private var sequenceMinimumConfidence = 1.0
    private var confirmedSignal: GolfPuttStrokeSignal? = null

    /** 상태를 초기화한다. [lastConfirmedTime]은 재발동 금지용이라 유지한다. */
    fun reset() {
        phase = GolfPuttStrokePhase.SEEKING_ADDRESS
        quietSince = null
        addressX = 0.0
        addressY = 0.0
        addressSamples = 0
        previousSample = null
        directionX = 0.0
        directionY = 0.0
        backswingStart = null
        peakProgress = 0.0
        previousProgress = 0.0
        returningSamples = 0
        followThroughSamples = 0
        strokeTime = null
        sequenceMinimumConfidence = 1.0
        confirmedSignal = null
    }

    /** 모델별 latch 시간 안에서만 확정 신호를 돌려준다. 지나면 latch를 비운다. */
    fun latchedConfirmedStroke(now: Double): GolfPuttStrokeSignal? {
        val signal = confirmedSignal ?: return null
        if (now - lastConfirmedTime > confirmationLatchDuration) {
            confirmedSignal = null
            return null
        }
        return signal
    }

    /** adapter가 촬영을 시작한 뒤 latch를 비운다. */
    fun consumeConfirmedStroke() {
        confirmedSignal = null
    }

    fun observe(sample: GolfSwingPoseSample): GolfPuttStrokeSignal {
        if (sample.confidence < 0.45 || sample.bodyScale < 0.04) {
            return currentSignal()
        }

        previousSample?.let { previous ->
            val sampleGap = sample.time - previous.time
            val scaleChange = abs(sample.bodyScale - previous.bodyScale) / max(0.001, previous.bodyScale)
            if (sampleGap <= 0.0 || sampleGap > 0.6 || scaleChange > 0.25) reset()
        }

        val previous = previousSample
        previousSample = sample
        val deltaTime = previous?.let { max(0.05, sample.time - it.time) } ?: 0.2
        val coreSpeed = previous?.let {
            hypot(sample.coreX - it.coreX, sample.coreY - it.coreY) / deltaTime / max(0.04, sample.bodyScale)
        } ?: 0.0
        // 걷기·이동·큰 몸통 회전은 어느 단계에서든 초기화한다.
        if (coreSpeed > 0.9) {
            reset()
            previousSample = sample
            return currentSignal()
        }
        sequenceMinimumConfidence = min(sequenceMinimumConfidence, sample.confidence)

        when (phase) {
            GolfPuttStrokePhase.SEEKING_ADDRESS -> {
                val handSpeed = previous?.let {
                    hypot(sample.handX - it.handX, sample.handY - it.handY) / deltaTime
                } ?: 0.0
                if (previous == null || (handSpeed <= 0.25 && coreSpeed <= 0.20)) {
                    quietSince = quietSince ?: previous?.time ?: sample.time
                    addressSamples += 1
                    val weight = 1.0 / addressSamples.toDouble()
                    addressX += (sample.handX - addressX) * weight
                    addressY += (sample.handY - addressY) * weight
                    if (sample.time - (quietSince ?: sample.time) >= 0.60 && addressSamples >= 3) {
                        phase = GolfPuttStrokePhase.ADDRESSED
                        sequenceMinimumConfidence = sample.confidence
                    }
                } else {
                    quietSince = null
                    addressSamples = 1
                    addressX = sample.handX
                    addressY = sample.handY
                }
            }

            GolfPuttStrokePhase.ADDRESSED -> {
                val dx = sample.handX - addressX
                val dy = sample.handY - addressY
                val displacement = hypot(dx, dy)
                if (displacement >= 0.06) {
                    directionX = dx / displacement
                    directionY = dy / displacement
                    backswingStart = sample.time
                    peakProgress = displacement
                    previousProgress = displacement
                    returningSamples = 0
                    phase = GolfPuttStrokePhase.BACKSWING
                }
            }

            GolfPuttStrokePhase.BACKSWING -> {
                val start = backswingStart
                if (start == null || sample.time - start > 2.0) {
                    reset()
                    return currentSignal()
                }
                val progress = (sample.handX - addressX) * directionX +
                    (sample.handY - addressY) * directionY
                peakProgress = max(peakProgress, progress)
                // 아이언 반례 상한: 백스윙 폭이 퍼팅 범위를 넘으면 스윙으로 본다.
                if (peakProgress > 0.60) {
                    reset()
                    return currentSignal()
                }
                val returnSpeed = (previousProgress - progress) / deltaTime
                val addressPassThreshold = max(0.04, peakProgress * 0.30)
                // 주소 지점을 너무 빠르게 통과하면 아이언/풀스윙 복귀로 본다.
                if (progress <= addressPassThreshold && returnSpeed > 1.5) {
                    reset()
                    return currentSignal()
                }
                if (sample.time - start >= 0.15 &&
                    peakProgress >= 0.12 &&
                    progress < previousProgress &&
                    returnSpeed >= 0.20
                ) {
                    returningSamples += 1
                } else if (progress >= previousProgress) {
                    returningSamples = 0
                }
                previousProgress = progress
                if (returningSamples >= 2 && progress <= addressPassThreshold) {
                    phase = GolfPuttStrokePhase.FORWARD_STROKE
                    strokeTime = sample.time
                    followThroughSamples = 0
                }
            }

            GolfPuttStrokePhase.FORWARD_STROKE -> {
                val stroke = strokeTime
                if (stroke == null || sample.time - stroke > 0.9) {
                    reset()
                    return currentSignal()
                }
                val progress = (sample.handX - addressX) * directionX +
                    (sample.handY - addressY) * directionY
                // 큰 팔로스루는 퍼팅이 아니라 스윙으로 본다.
                if (progress <= -max(0.30, peakProgress * 1.2)) {
                    reset()
                    return currentSignal()
                }
                if (progress <= -max(0.025, peakProgress * 0.15)) {
                    followThroughSamples += 1
                }
                previousProgress = progress
                if (followThroughSamples >= 2 && sample.time - lastConfirmedTime >= 2.0) {
                    phase = GolfPuttStrokePhase.CONFIRMED_STROKE
                    lastConfirmedTime = sample.time
                    confirmedSignal = GolfPuttStrokeSignal(
                        phase = GolfPuttStrokePhase.CONFIRMED_STROKE,
                        confidence = min(
                            sequenceMinimumConfidence,
                            min(1.0, 0.62 + peakProgress * 0.9)
                        ),
                        strokeTime = stroke
                    )
                }
            }

            GolfPuttStrokePhase.CONFIRMED_STROKE -> {
                // 한 스트로크 중복 방지: 확정 뒤에는 다시 정지 탐색부터 시작한다.
                val latched = confirmedSignal
                reset()
                confirmedSignal = latched
                previousSample = sample
            }
        }
        return currentSignal()
    }

    fun currentSignal(): GolfPuttStrokeSignal = when (phase) {
        GolfPuttStrokePhase.CONFIRMED_STROKE ->
            confirmedSignal ?: GolfPuttStrokeSignal(GolfPuttStrokePhase.CONFIRMED_STROKE, 0.0, null)
        GolfPuttStrokePhase.FORWARD_STROKE -> GolfPuttStrokeSignal(
            GolfPuttStrokePhase.FORWARD_STROKE,
            min(0.7, 0.4 + peakProgress),
            null
        )
        GolfPuttStrokePhase.BACKSWING -> GolfPuttStrokeSignal(
            GolfPuttStrokePhase.BACKSWING,
            min(0.6, 0.3 + peakProgress),
            null
        )
        GolfPuttStrokePhase.ADDRESSED -> GolfPuttStrokeSignal(GolfPuttStrokePhase.ADDRESSED, 0.3, null)
        GolfPuttStrokePhase.SEEKING_ADDRESS -> GolfPuttStrokeSignal(GolfPuttStrokePhase.SEEKING_ADDRESS, 0.0, null)
    }
}
