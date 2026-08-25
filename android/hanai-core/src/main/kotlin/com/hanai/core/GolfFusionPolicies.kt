package com.hanai.core

/**
 * 충격음 + 시각 근거 결합 정책 (일반 스윙, 모델 0.4.0 이후).
 *
 * - 일반 경로는 충격음 판정이 필수다. 0.7.0은 정렬된 연속 동작이 있는 약한 날카로운
 *   접촉음만 제한적으로 보존한다.
 * - 0.7.0은 최근 영상 프레임이 없으면 막고, 이전 모델만 소리 단독 롤백 동작을 유지한다.
 * - 시각 근거는 정렬된 화면 움직임 임팩트 창 또는(0.5.1 이후) 정렬된 자세 임팩트 창이다.
 * - ready 억제 구간에서는 강한 물리 충격(peak ≥ 0.16, impactScore ≥ 0.08)만 통과한다.
 */
object GolfSwingFusionPolicy {
    const val MINIMUM_VISUAL_CONFIDENCE = 0.72
    /** `referenceTime - motion.impactTime`이 이 범위 안일 때만 화면 움직임 근거를 인정한다. */
    val MOTION_ALIGNMENT_WINDOW: ClosedFloatingPointRange<Double> = -0.20..0.32
    const val READY_PROMPT_PEAK_FLOOR = 0.16
    const val READY_PROMPT_IMPACT_SCORE_FLOOR = 0.08
    const val WEAK_IMPACT_PEAK_FLOOR = 0.10
    const val WEAK_IMPACT_SCORE_FLOOR = 0.065
    const val WEAK_IMPACT_CROSSING_RATE_FLOOR = 0.08
    const val WEAK_IMPACT_CREST_FACTOR_FLOOR = 3.5

    fun shouldTrigger(
        decision: AudioImpactDecision,
        metrics: AudioImpactMetrics,
        motion: GolfSwingMotionSignal,
        pose: GolfSwingPoseSignal? = null,
        referenceTime: Double,
        requiresPoseConfirmation: Boolean = false,
        hasRecentVisualFrame: Boolean,
        isInsideReadyPromptWindow: Boolean,
        modelVersion: GolfModelVersion = GolfModelVersion.current
    ): Boolean {
        if (!hasRecentVisualFrame) {
            return decision.isTriggered &&
                !modelVersion.requiresVisualShotEvidence &&
                !isInsideReadyPromptWindow
        }

        val motionImpactTime = motion.impactTime
        val hasAlignedMotion = motion.isImpactWindow &&
            motion.confidence >= MINIMUM_VISUAL_CONFIDENCE &&
            motionImpactTime != null &&
            (referenceTime - motionImpactTime) in MOTION_ALIGNMENT_WINDOW
        val hasAlignedPose = modelVersion.usesPoseBackedImpactEvidence &&
            (pose?.confidence ?: 0.0) >= MINIMUM_VISUAL_CONFIDENCE &&
            pose?.isImpactWindow(referenceTime) == true

        if (!hasAlignedMotion && !hasAlignedPose) return false
        if (requiresPoseConfirmation && !hasAlignedPose) return false

        if (!decision.isTriggered) {
            val crestFactor = metrics.peak / maxOf(0.001, metrics.rms)
            return modelVersion.supportsVisualBackedWeakImpact &&
                !isInsideReadyPromptWindow &&
                metrics.peak >= WEAK_IMPACT_PEAK_FLOOR &&
                metrics.impactScore >= WEAK_IMPACT_SCORE_FLOOR &&
                metrics.crossingRate >= WEAK_IMPACT_CROSSING_RATE_FLOOR &&
                crestFactor >= WEAK_IMPACT_CREST_FACTOR_FLOOR
        }

        if (isInsideReadyPromptWindow) {
            return metrics.peak >= READY_PROMPT_PEAK_FLOOR &&
                metrics.impactScore >= READY_PROMPT_IMPACT_SCORE_FLOOR
        }
        return true
    }
}

/**
 * 무음 퍼팅 안전망 정책 (모델 0.6.0 이후).
 *
 * 확정된 스트로크가 있고, 시퀀스·자세 신뢰도가 모두 0.72 이상이며, 모델별 최근 허용 시간 안에
 * 자세와 영상 프레임이 있고, 최근 1.0초 안에 화면 전체 변화가 없고, ready 상태이며
 * ready 억제 구간이 아니고, 이미 대기 중인 트리거가 없을 때만 발동한다.
 */
object GolfPuttFusionPolicy {
    const val MINIMUM_STROKE_CONFIDENCE = 0.72
    const val MINIMUM_POSE_OBSERVATION_CONFIDENCE = 0.72
    const val MAXIMUM_POSE_AGE = 0.55
    const val MAXIMUM_VISUAL_FRAME_AGE = 0.55
    const val MINIMUM_SCENE_STABLE_SECONDS = 1.0

    fun shouldTrigger(
        stroke: GolfPuttStrokeSignal?,
        poseObservationConfidence: Double,
        secondsSinceLatestPose: Double,
        secondsSinceLatestVisualFrame: Double,
        secondsSinceLastGlobalChange: Double,
        isReady: Boolean,
        isInsideReadyPromptWindow: Boolean,
        isTriggerPending: Boolean,
        modelVersion: GolfModelVersion = GolfModelVersion.current
    ): Boolean {
        if (!modelVersion.supportsSoundlessPuttFallback) return false
        if (stroke == null || !stroke.isConfirmedStroke) return false
        if (stroke.confidence < MINIMUM_STROKE_CONFIDENCE) return false
        if (poseObservationConfidence < MINIMUM_POSE_OBSERVATION_CONFIDENCE) return false
        val maximumAge = if (modelVersion == GolfModelVersion.V0_7_0) 0.55 else 0.35
        if (secondsSinceLatestPose > maximumAge) return false
        if (secondsSinceLatestVisualFrame > maximumAge) return false
        if (secondsSinceLastGlobalChange < MINIMUM_SCENE_STABLE_SECONDS) return false
        if (!isReady || isInsideReadyPromptWindow) return false
        if (isTriggerPending) return false
        return true
    }
}
