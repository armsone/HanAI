import Foundation

/// 충격음 + 시각 근거 결합 정책 (일반 스윙, 모델 0.4.0 이후).
///
/// - 일반 경로는 충격음 판정이 필수다. 0.7.0은 정렬된 연속 동작이 있는 약한 날카로운
///   접촉음만 제한적으로 보존한다.
/// - 0.7.0은 최근 영상 프레임이 없으면 비샷 오탐을 막기 위해 촬영하지 않는다.
///   이전 모델은 소리 단독 안전망을 롤백 동작으로 유지한다.
/// - 시각 근거는 정렬된 화면 움직임 임팩트 창 또는(0.5.1 이후) 정렬된 자세 임팩트 창이다.
/// - ready 억제 구간에서는 강한 물리 충격(peak ≥ 0.16, impactScore ≥ 0.08)만 통과한다.
public enum GolfSwingFusionPolicy {
    public static let minimumVisualConfidence = 0.72
    /// `referenceTime - motion.impactTime`이 이 범위 안일 때만 화면 움직임 근거를 인정한다.
    public static let motionAlignmentWindow: ClosedRange<Double> = -0.20...0.32
    public static let readyPromptPeakFloor = 0.16
    public static let readyPromptImpactScoreFloor = 0.08
    public static let weakImpactPeakFloor = 0.10
    public static let weakImpactScoreFloor = 0.065
    public static let weakImpactCrossingRateFloor = 0.08
    public static let weakImpactCrestFactorFloor = 3.5

    public static func shouldTrigger(
        decision: AudioImpactDecision,
        metrics: AudioImpactMetrics,
        motion: GolfSwingMotionSignal,
        pose: GolfSwingPoseSignal? = nil,
        referenceTime: Double,
        requiresPoseConfirmation: Bool = false,
        hasRecentVisualFrame: Bool,
        isInsideReadyPromptWindow: Bool,
        modelVersion: GolfModelVersion = .current
    ) -> Bool {
        guard hasRecentVisualFrame else {
            return decision.isTriggered
                && !modelVersion.requiresVisualShotEvidence
                && !isInsideReadyPromptWindow
        }

        let hasAlignedMotion: Bool = {
            guard motion.isImpactWindow,
                  motion.confidence >= minimumVisualConfidence,
                  let impactTime = motion.impactTime
            else { return false }
            return motionAlignmentWindow.contains(referenceTime - impactTime)
        }()
        let hasAlignedPose = modelVersion.usesPoseBackedImpactEvidence
            && (pose?.confidence ?? 0) >= minimumVisualConfidence
            && pose?.isImpactWindow(at: referenceTime) == true

        guard hasAlignedMotion || hasAlignedPose else { return false }
        guard !requiresPoseConfirmation || hasAlignedPose else { return false }

        if !decision.isTriggered {
            let crestFactor = metrics.peak / max(0.001, metrics.rms)
            guard modelVersion.supportsVisualBackedWeakImpact,
                  !isInsideReadyPromptWindow,
                  metrics.peak >= weakImpactPeakFloor,
                  metrics.impactScore >= weakImpactScoreFloor,
                  metrics.crossingRate >= weakImpactCrossingRateFloor,
                  crestFactor >= weakImpactCrestFactorFloor
            else { return false }
            return true
        }

        if isInsideReadyPromptWindow {
            return metrics.peak >= readyPromptPeakFloor
                && metrics.impactScore >= readyPromptImpactScoreFloor
        }
        return true
    }
}

/// 무음 퍼팅 안전망 정책 (모델 0.6.0 이후).
///
/// 확정된 스트로크가 있고, 시퀀스·자세 신뢰도가 모두 0.72 이상이며, 최근 허용 시간 안에
/// 자세와 영상 프레임이 있고, 최근 1.0초 안에 화면 전체 변화가 없고, ready 상태이며
/// ready 억제 구간이 아니고, 이미 대기 중인 트리거가 없을 때만 발동한다.
public enum GolfPuttFusionPolicy {
    public static let minimumStrokeConfidence = 0.72
    public static let minimumPoseObservationConfidence = 0.72
    public static let maximumPoseAge = 0.55
    public static let maximumVisualFrameAge = 0.55
    public static let minimumSceneStableSeconds = 1.0

    public static func shouldTrigger(
        stroke: GolfPuttStrokeSignal?,
        poseObservationConfidence: Double,
        secondsSinceLatestPose: Double,
        secondsSinceLatestVisualFrame: Double,
        secondsSinceLastGlobalChange: Double,
        isReady: Bool,
        isInsideReadyPromptWindow: Bool,
        isTriggerPending: Bool,
        modelVersion: GolfModelVersion = .current
    ) -> Bool {
        guard modelVersion.supportsSoundlessPuttFallback else { return false }
        guard let stroke, stroke.isConfirmedStroke else { return false }
        guard stroke.confidence >= minimumStrokeConfidence else { return false }
        guard poseObservationConfidence >= minimumPoseObservationConfidence else { return false }
        let maximumAge = modelVersion == .v0_7_0 ? 0.55 : 0.35
        guard secondsSinceLatestPose <= maximumAge else { return false }
        guard secondsSinceLatestVisualFrame <= maximumAge else { return false }
        guard secondsSinceLastGlobalChange >= minimumSceneStableSeconds else { return false }
        guard isReady, !isInsideReadyPromptWindow else { return false }
        guard !isTriggerPending else { return false }
        return true
    }
}
