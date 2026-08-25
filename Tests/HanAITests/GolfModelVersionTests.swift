import XCTest
import HanAI

/// 제품 버전, 현재 모델 계보, 롤백 플래그 계약을 검증한다.
final class GolfModelVersionTests: XCTestCase {
    func testProductAndModelVersions() {
        XCTAssertEqual(HanAIVersion.product, "0.1.0")
        XCTAssertEqual(HanAIVersion.golfModel, "0.7.0")
        XCTAssertEqual(GolfModelVersion.current, .v0_7_0)
        XCTAssertEqual(GolfModelVersion.allCases.count, 9)
        XCTAssertEqual(GolfModelVersion(rawValue: "0.7.0"), .v0_7_0)
        XCTAssertEqual(GolfModelVersion(rawValue: "0.6.0"), .v0_6_0)
    }

    func testCurrentModelFlags() {
        let current = GolfModelVersion.current
        XCTAssertTrue(current.supportsRealtimeVisualAssist)
        XCTAssertTrue(current.usesAudibleResponseWeight)
        XCTAssertTrue(current.usesGolfSwingMotionFusion)
        XCTAssertTrue(current.usesBodyPoseAssist)
        XCTAssertTrue(current.usesPoseBackedImpactEvidence)
        XCTAssertTrue(current.supportsSoundlessPuttFallback)
        XCTAssertTrue(current.requiresVisualShotEvidence)
        XCTAssertTrue(current.supportsVisualBackedWeakImpact)
    }

    func testRollbackFlagsFollowLineage() {
        // 0.5.1: 자세 근거 보강까지만, 무음 퍼팅은 없음
        XCTAssertTrue(GolfModelVersion.v0_5_1.usesPoseBackedImpactEvidence)
        XCTAssertFalse(GolfModelVersion.v0_5_1.supportsSoundlessPuttFallback)
        // 0.5.0: 자세 보조는 있으나 자세 단독 시각 근거는 없음
        XCTAssertTrue(GolfModelVersion.v0_5_0.usesBodyPoseAssist)
        XCTAssertFalse(GolfModelVersion.v0_5_0.usesPoseBackedImpactEvidence)
        // 0.4.0: 화면 움직임 결합만
        XCTAssertTrue(GolfModelVersion.v0_4_0.usesGolfSwingMotionFusion)
        XCTAssertFalse(GolfModelVersion.v0_4_0.usesBodyPoseAssist)
        // 0.3.0 이하: 골프 결합 없음
        XCTAssertFalse(GolfModelVersion.v0_3_0.usesGolfSwingMotionFusion)
        XCTAssertTrue(GolfModelVersion.v0_3_0.supportsRealtimeVisualAssist)
        // 0.1.0: 소리 전용
        XCTAssertFalse(GolfModelVersion.v0_1_0.supportsRealtimeVisualAssist)
        XCTAssertFalse(GolfModelVersion.v0_1_0.usesAudibleResponseWeight)

        for version in GolfModelVersion.allCases {
            XCTAssertFalse(version.title.isEmpty, version.rawValue)
            XCTAssertFalse(version.featureSummary.isEmpty, version.rawValue)
            XCTAssertFalse(version.releaseDate.isEmpty, version.rawValue)
        }
    }

    func testRollbackDisablesSoundlessPuttPolicy() {
        let stroke = GolfPuttStrokeSignal(
            phase: .confirmedStroke,
            confidence: 0.9,
            strokeTime: 1.6
        )
        func trigger(_ version: GolfModelVersion) -> Bool {
            GolfPuttFusionPolicy.shouldTrigger(
                stroke: stroke,
                poseObservationConfidence: 0.9,
                secondsSinceLatestPose: 0.1,
                secondsSinceLatestVisualFrame: 0.1,
                secondsSinceLastGlobalChange: 5,
                isReady: true,
                isInsideReadyPromptWindow: false,
                isTriggerPending: false,
                modelVersion: version
            )
        }
        XCTAssertTrue(trigger(.v0_7_0))
        XCTAssertTrue(trigger(.v0_6_0))
        for version in GolfModelVersion.allCases where version != .v0_6_0 && version != .v0_7_0 {
            XCTAssertFalse(trigger(version), version.rawValue)
        }
    }
}
