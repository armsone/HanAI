package com.hanai.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** 제품 버전, 현재 모델 계보, 롤백 플래그 계약을 검증한다. Swift `GolfModelVersionTests`와 대응한다. */
class GolfModelVersionTest {
    @Test
    fun productAndModelVersions() {
        assertEquals("0.2.0", HanAIVersion.product)
        assertEquals("0.7.0", HanAIVersion.golfModel)
        assertEquals(GolfModelVersion.V0_7_0, GolfModelVersion.current)
        assertEquals(9, GolfModelVersion.values().size)
        assertEquals(GolfModelVersion.V0_7_0, GolfModelVersion.fromRawValue("0.7.0"))
        assertEquals(GolfModelVersion.V0_6_0, GolfModelVersion.fromRawValue("0.6.0"))
    }

    @Test
    fun currentModelFlags() {
        val current = GolfModelVersion.current
        assertTrue(current.supportsRealtimeVisualAssist)
        assertTrue(current.usesAudibleResponseWeight)
        assertTrue(current.usesGolfSwingMotionFusion)
        assertTrue(current.usesBodyPoseAssist)
        assertTrue(current.usesPoseBackedImpactEvidence)
        assertTrue(current.supportsSoundlessPuttFallback)
        assertTrue(current.requiresVisualShotEvidence)
        assertTrue(current.supportsVisualBackedWeakImpact)
    }

    @Test
    fun rollbackFlagsFollowLineage() {
        // 0.5.1: 자세 근거 보강까지만, 무음 퍼팅은 없음
        assertTrue(GolfModelVersion.V0_5_1.usesPoseBackedImpactEvidence)
        assertFalse(GolfModelVersion.V0_5_1.supportsSoundlessPuttFallback)
        // 0.5.0: 자세 보조는 있으나 자세 단독 시각 근거는 없음
        assertTrue(GolfModelVersion.V0_5_0.usesBodyPoseAssist)
        assertFalse(GolfModelVersion.V0_5_0.usesPoseBackedImpactEvidence)
        // 0.4.0: 화면 움직임 결합만
        assertTrue(GolfModelVersion.V0_4_0.usesGolfSwingMotionFusion)
        assertFalse(GolfModelVersion.V0_4_0.usesBodyPoseAssist)
        // 0.3.0 이하: 골프 결합 없음
        assertFalse(GolfModelVersion.V0_3_0.usesGolfSwingMotionFusion)
        assertTrue(GolfModelVersion.V0_3_0.supportsRealtimeVisualAssist)
        // 0.1.0: 소리 전용
        assertFalse(GolfModelVersion.V0_1_0.supportsRealtimeVisualAssist)
        assertFalse(GolfModelVersion.V0_1_0.usesAudibleResponseWeight)

        for (version in GolfModelVersion.values()) {
            assertTrue(version.rawValue, version.title.isNotEmpty())
            assertTrue(version.rawValue, version.featureSummary.isNotEmpty())
            assertTrue(version.rawValue, version.releaseDate.isNotEmpty())
        }
    }

    @Test
    fun rollbackDisablesSoundlessPuttPolicy() {
        val stroke = GolfPuttStrokeSignal(
            phase = GolfPuttStrokePhase.CONFIRMED_STROKE,
            confidence = 0.9,
            strokeTime = 1.6
        )

        fun trigger(version: GolfModelVersion): Boolean = GolfPuttFusionPolicy.shouldTrigger(
            stroke = stroke,
            poseObservationConfidence = 0.9,
            secondsSinceLatestPose = 0.1,
            secondsSinceLatestVisualFrame = 0.1,
            secondsSinceLastGlobalChange = 5.0,
            isReady = true,
            isInsideReadyPromptWindow = false,
            isTriggerPending = false,
            modelVersion = version
        )

        assertTrue(trigger(GolfModelVersion.V0_7_0))
        assertTrue(trigger(GolfModelVersion.V0_6_0))
        for (version in GolfModelVersion.values()) {
            if (version == GolfModelVersion.V0_6_0 || version == GolfModelVersion.V0_7_0) continue
            assertFalse(version.rawValue, trigger(version))
        }
    }
}
