import Foundation

/// 주변 소음 등급. `automatic`은 baseline으로 등급을 추정한다.
public enum AudioImpactSensitivity: String, CaseIterable, Equatable, Sendable {
    case noisy
    case normal
    case quiet
    case automatic
}

/// 짧은 오디오 창(수십 ms)에서 계산한 정규화 지표. 값은 0...1 범위를 기대한다.
///
/// adapter가 PCM 버퍼에서 계산해 넘긴다. HanAI는 원본 오디오를 다루지 않는다.
public struct AudioImpactMetrics: Equatable, Sendable {
    public let rms: Double
    public let peak: Double
    public let crossingRate: Double

    public init(rms: Double, peak: Double, crossingRate: Double) {
        self.rms = rms
        self.peak = peak
        self.crossingRate = crossingRate
    }

    /// 충격음 점수. rms·peak·고주파 텍스처를 합산한 0...1 값.
    public var impactScore: Double {
        let highFrequencyWeight = min(1, crossingRate * 10)
        return min(
            1,
            rms * 0.55
                + peak * 0.45
                + highFrequencyWeight * rms * 0.35
        )
    }
}

/// 실시간 충격음 판정 결과.
public struct AudioImpactDecision: Equatable, Sendable {
    public let isTriggered: Bool
    public let confidence: Double

    public init(isTriggered: Bool, confidence: Double) {
        self.isTriggered = isTriggered
        self.confidence = confidence
    }
}

/// 실시간 충격음 판정기. 상태가 없고 baseline·recentLevel은 adapter가 추적해 넘긴다.
public enum AudioImpactClassifier {
    public struct Thresholds: Equatable, Sendable {
        public let strongScoreFloor: Double
        public let strongBaselineMultiplier: Double
        public let strongPeakFloor: Double
        public let strongPeakBaselineMultiplier: Double
        public let strongRise: Double
        public let strongCrossingRate: Double
        public let strongCrestFactor: Double
        public let distantScoreFloor: Double
        public let distantBaselineMultiplier: Double
        public let distantPeakFloor: Double
        public let distantPeakBaselineMultiplier: Double
        public let distantRise: Double
        public let distantCrossingRate: Double
        public let distantCrestFactor: Double
    }

    /// 한 오디오 창을 판정한다.
    ///
    /// - Parameters:
    ///   - baseline: 느리게 따라가는 배경 소음 수준(impactScore 단위).
    ///   - previousRecentLevel: 직전 창까지의 빠른 최근 수준.
    public static func detectImpact(
        metrics: AudioImpactMetrics,
        baseline: Double,
        previousRecentLevel: Double,
        sensitivity: AudioImpactSensitivity
    ) -> AudioImpactDecision {
        let score = metrics.impactScore
        let referenceLevel = max(
            0.003,
            max(baseline, previousRecentLevel * 0.82)
        )
        let suddenRise = score / referenceLevel
        let crestFactor = metrics.peak / max(0.001, metrics.rms)
        let thresholds = thresholds(
            for: effectiveSensitivity(sensitivity, baseline: baseline)
        )

        let strongScoreRequirement = max(
            thresholds.strongScoreFloor,
            baseline * thresholds.strongBaselineMultiplier
        )
        let strongPeakRequirement = max(
            thresholds.strongPeakFloor,
            baseline * thresholds.strongPeakBaselineMultiplier
        )
        let isStrongImpact = score >= strongScoreRequirement
            && metrics.peak >= strongPeakRequirement
            && suddenRise >= thresholds.strongRise
            && metrics.crossingRate >= thresholds.strongCrossingRate
            && crestFactor >= thresholds.strongCrestFactor

        let distantScoreRequirement = max(
            thresholds.distantScoreFloor,
            baseline * thresholds.distantBaselineMultiplier
        )
        let distantPeakRequirement = max(
            thresholds.distantPeakFloor,
            baseline * thresholds.distantPeakBaselineMultiplier
        )
        let isDistantSharpImpact = score >= distantScoreRequirement
            && metrics.peak >= distantPeakRequirement
            && suddenRise >= thresholds.distantRise
            && metrics.crossingRate >= thresholds.distantCrossingRate
            && crestFactor >= thresholds.distantCrestFactor

        // 준비 음성("자, 갑니다" 등)처럼 완만하고 저주파인 소리는 억제한다.
        let isSpeechLikePrompt = metrics.rms >= 0.025
            && crestFactor < 3.15
            && metrics.crossingRate < 0.16
            && suddenRise < 4.8
            && score < 0.18

        let confidence = impactConfidence(
            score: score,
            peak: metrics.peak,
            suddenRise: suddenRise,
            crossingRate: metrics.crossingRate,
            crestFactor: crestFactor,
            thresholds: thresholds
        )
        return AudioImpactDecision(
            isTriggered: !isSpeechLikePrompt
                && (isStrongImpact || isDistantSharpImpact),
            confidence: isSpeechLikePrompt ? 0 : confidence
        )
    }

    /// `automatic`을 baseline 기준으로 실제 등급에 매핑한다.
    public static func effectiveSensitivity(
        _ sensitivity: AudioImpactSensitivity,
        baseline: Double
    ) -> AudioImpactSensitivity {
        guard sensitivity == .automatic else { return sensitivity }
        if baseline >= 0.026 {
            return .noisy
        } else if baseline <= 0.009 {
            return .quiet
        }
        return .normal
    }

    public static func thresholds(
        for sensitivity: AudioImpactSensitivity
    ) -> Thresholds {
        switch sensitivity {
        case .noisy:
            return Thresholds(
                strongScoreFloor: 0.10,
                strongBaselineMultiplier: 2.7,
                strongPeakFloor: 0.18,
                strongPeakBaselineMultiplier: 4.2,
                strongRise: 2.2,
                strongCrossingRate: 0.07,
                strongCrestFactor: 2.3,
                distantScoreFloor: 0.065,
                distantBaselineMultiplier: 3.4,
                distantPeakFloor: 0.12,
                distantPeakBaselineMultiplier: 5.0,
                distantRise: 3.0,
                distantCrossingRate: 0.10,
                distantCrestFactor: 3.5
            )
        case .normal, .automatic:
            return Thresholds(
                strongScoreFloor: 0.075,
                strongBaselineMultiplier: 2.3,
                strongPeakFloor: 0.13,
                strongPeakBaselineMultiplier: 3.5,
                strongRise: 1.8,
                strongCrossingRate: 0.05,
                strongCrestFactor: 2.0,
                distantScoreFloor: 0.045,
                distantBaselineMultiplier: 2.8,
                distantPeakFloor: 0.09,
                distantPeakBaselineMultiplier: 4.2,
                distantRise: 2.4,
                distantCrossingRate: 0.08,
                distantCrestFactor: 3.0
            )
        case .quiet:
            return Thresholds(
                strongScoreFloor: 0.055,
                strongBaselineMultiplier: 1.9,
                strongPeakFloor: 0.095,
                strongPeakBaselineMultiplier: 3.0,
                strongRise: 1.55,
                strongCrossingRate: 0.04,
                strongCrestFactor: 1.7,
                distantScoreFloor: 0.035,
                distantBaselineMultiplier: 2.3,
                distantPeakFloor: 0.07,
                distantPeakBaselineMultiplier: 3.4,
                distantRise: 2.0,
                distantCrossingRate: 0.065,
                distantCrestFactor: 2.5
            )
        }
    }

    private static func impactConfidence(
        score: Double,
        peak: Double,
        suddenRise: Double,
        crossingRate: Double,
        crestFactor: Double,
        thresholds: Thresholds
    ) -> Double {
        let scoreRatio = score / max(0.001, thresholds.distantScoreFloor)
        let peakRatio = peak / max(0.001, thresholds.distantPeakFloor)
        let riseRatio = suddenRise / max(0.001, thresholds.distantRise)
        let crossingRatio = crossingRate
            / max(0.001, thresholds.distantCrossingRate)
        let crestRatio = crestFactor
            / max(0.001, thresholds.distantCrestFactor)
        return scoreRatio * 0.28
            + peakRatio * 0.22
            + riseRatio * 0.24
            + min(crossingRatio, 1.8) * 0.13
            + min(crestRatio, 1.8) * 0.13
    }
}
