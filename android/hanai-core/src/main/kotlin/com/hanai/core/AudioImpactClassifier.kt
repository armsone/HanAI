package com.hanai.core

import kotlin.math.max
import kotlin.math.min

/** 주변 소음 등급. [AUTOMATIC]은 baseline으로 등급을 추정한다. */
enum class AudioImpactSensitivity(val rawValue: String) {
    NOISY("noisy"),
    NORMAL("normal"),
    QUIET("quiet"),
    AUTOMATIC("automatic");

    companion object {
        fun fromRawValue(rawValue: String): AudioImpactSensitivity? =
            values().firstOrNull { it.rawValue == rawValue }
    }
}

/**
 * 짧은 오디오 창(수십 ms)에서 계산한 정규화 지표. 값은 0..1 범위를 기대한다.
 * adapter가 PCM 버퍼에서 계산해 넘긴다. HanAI는 원본 오디오를 다루지 않는다.
 */
data class AudioImpactMetrics(
    val rms: Double,
    val peak: Double,
    val crossingRate: Double
) {
    /** 충격음 점수. rms·peak·고주파 텍스처를 합산한 0..1 값. */
    val impactScore: Double
        get() {
            val highFrequencyWeight = min(1.0, crossingRate * 10)
            return min(
                1.0,
                rms * 0.55 + peak * 0.45 + highFrequencyWeight * rms * 0.35
            )
        }
}

/** 실시간 충격음 판정 결과. */
data class AudioImpactDecision(
    val isTriggered: Boolean,
    val confidence: Double
)

/** 실시간 충격음 판정기. 상태가 없고 baseline·recentLevel은 adapter가 추적해 넘긴다. */
object AudioImpactClassifier {
    data class Thresholds(
        val strongScoreFloor: Double,
        val strongBaselineMultiplier: Double,
        val strongPeakFloor: Double,
        val strongPeakBaselineMultiplier: Double,
        val strongRise: Double,
        val strongCrossingRate: Double,
        val strongCrestFactor: Double,
        val distantScoreFloor: Double,
        val distantBaselineMultiplier: Double,
        val distantPeakFloor: Double,
        val distantPeakBaselineMultiplier: Double,
        val distantRise: Double,
        val distantCrossingRate: Double,
        val distantCrestFactor: Double
    )

    /**
     * 한 오디오 창을 판정한다.
     * @param baseline 느리게 따라가는 배경 소음 수준(impactScore 단위).
     * @param previousRecentLevel 직전 창까지의 빠른 최근 수준.
     */
    fun detectImpact(
        metrics: AudioImpactMetrics,
        baseline: Double,
        previousRecentLevel: Double,
        sensitivity: AudioImpactSensitivity
    ): AudioImpactDecision {
        val score = metrics.impactScore
        val referenceLevel = max(0.003, max(baseline, previousRecentLevel * 0.82))
        val suddenRise = score / referenceLevel
        val crestFactor = metrics.peak / max(0.001, metrics.rms)
        val thresholds = thresholds(effectiveSensitivity(sensitivity, baseline))

        val strongScoreRequirement = max(
            thresholds.strongScoreFloor,
            baseline * thresholds.strongBaselineMultiplier
        )
        val strongPeakRequirement = max(
            thresholds.strongPeakFloor,
            baseline * thresholds.strongPeakBaselineMultiplier
        )
        val isStrongImpact = score >= strongScoreRequirement &&
            metrics.peak >= strongPeakRequirement &&
            suddenRise >= thresholds.strongRise &&
            metrics.crossingRate >= thresholds.strongCrossingRate &&
            crestFactor >= thresholds.strongCrestFactor

        val distantScoreRequirement = max(
            thresholds.distantScoreFloor,
            baseline * thresholds.distantBaselineMultiplier
        )
        val distantPeakRequirement = max(
            thresholds.distantPeakFloor,
            baseline * thresholds.distantPeakBaselineMultiplier
        )
        val isDistantSharpImpact = score >= distantScoreRequirement &&
            metrics.peak >= distantPeakRequirement &&
            suddenRise >= thresholds.distantRise &&
            metrics.crossingRate >= thresholds.distantCrossingRate &&
            crestFactor >= thresholds.distantCrestFactor

        // 준비 음성("자, 갑니다" 등)처럼 완만하고 저주파인 소리는 억제한다.
        val isSpeechLikePrompt = metrics.rms >= 0.025 &&
            crestFactor < 3.15 &&
            metrics.crossingRate < 0.16 &&
            suddenRise < 4.8 &&
            score < 0.18

        val confidence = impactConfidence(
            score = score,
            peak = metrics.peak,
            suddenRise = suddenRise,
            crossingRate = metrics.crossingRate,
            crestFactor = crestFactor,
            thresholds = thresholds
        )
        return AudioImpactDecision(
            isTriggered = !isSpeechLikePrompt && (isStrongImpact || isDistantSharpImpact),
            confidence = if (isSpeechLikePrompt) 0.0 else confidence
        )
    }

    /** [AudioImpactSensitivity.AUTOMATIC]을 baseline 기준으로 실제 등급에 매핑한다. */
    fun effectiveSensitivity(
        sensitivity: AudioImpactSensitivity,
        baseline: Double
    ): AudioImpactSensitivity {
        if (sensitivity != AudioImpactSensitivity.AUTOMATIC) return sensitivity
        return when {
            baseline >= 0.026 -> AudioImpactSensitivity.NOISY
            baseline <= 0.009 -> AudioImpactSensitivity.QUIET
            else -> AudioImpactSensitivity.NORMAL
        }
    }

    fun thresholds(sensitivity: AudioImpactSensitivity): Thresholds = when (sensitivity) {
        AudioImpactSensitivity.NOISY -> Thresholds(
            strongScoreFloor = 0.10,
            strongBaselineMultiplier = 2.7,
            strongPeakFloor = 0.18,
            strongPeakBaselineMultiplier = 4.2,
            strongRise = 2.2,
            strongCrossingRate = 0.07,
            strongCrestFactor = 2.3,
            distantScoreFloor = 0.065,
            distantBaselineMultiplier = 3.4,
            distantPeakFloor = 0.12,
            distantPeakBaselineMultiplier = 5.0,
            distantRise = 3.0,
            distantCrossingRate = 0.10,
            distantCrestFactor = 3.5
        )
        AudioImpactSensitivity.NORMAL, AudioImpactSensitivity.AUTOMATIC -> Thresholds(
            strongScoreFloor = 0.075,
            strongBaselineMultiplier = 2.3,
            strongPeakFloor = 0.13,
            strongPeakBaselineMultiplier = 3.5,
            strongRise = 1.8,
            strongCrossingRate = 0.05,
            strongCrestFactor = 2.0,
            distantScoreFloor = 0.045,
            distantBaselineMultiplier = 2.8,
            distantPeakFloor = 0.09,
            distantPeakBaselineMultiplier = 4.2,
            distantRise = 2.4,
            distantCrossingRate = 0.08,
            distantCrestFactor = 3.0
        )
        AudioImpactSensitivity.QUIET -> Thresholds(
            strongScoreFloor = 0.055,
            strongBaselineMultiplier = 1.9,
            strongPeakFloor = 0.095,
            strongPeakBaselineMultiplier = 3.0,
            strongRise = 1.55,
            strongCrossingRate = 0.04,
            strongCrestFactor = 1.7,
            distantScoreFloor = 0.035,
            distantBaselineMultiplier = 2.3,
            distantPeakFloor = 0.07,
            distantPeakBaselineMultiplier = 3.4,
            distantRise = 2.0,
            distantCrossingRate = 0.065,
            distantCrestFactor = 2.5
        )
    }

    private fun impactConfidence(
        score: Double,
        peak: Double,
        suddenRise: Double,
        crossingRate: Double,
        crestFactor: Double,
        thresholds: Thresholds
    ): Double {
        val scoreRatio = score / max(0.001, thresholds.distantScoreFloor)
        val peakRatio = peak / max(0.001, thresholds.distantPeakFloor)
        val riseRatio = suddenRise / max(0.001, thresholds.distantRise)
        val crossingRatio = crossingRate / max(0.001, thresholds.distantCrossingRate)
        val crestRatio = crestFactor / max(0.001, thresholds.distantCrestFactor)
        return scoreRatio * 0.28 +
            peakRatio * 0.22 +
            riseRatio * 0.24 +
            min(crossingRatio, 1.8) * 0.13 +
            min(crestRatio, 1.8) * 0.13
    }
}
