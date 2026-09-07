import XCTest
import HanAI

/// `Fixtures/golf/*.json`을 그대로 재생해 Swift 코어가 계약을 지키는지 검증한다.
/// Kotlin `GolfFixtureTest`와 같은 fixture를 읽는다.
final class GolfFixtureTests: XCTestCase {
    func testSwingMotionFixture() throws {
        let file: FixtureFile<SwingMotionCase, VisualSampleDefaults> =
            try Fixtures.load("swing-motion.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)
        let defaults = try XCTUnwrap(file.defaults)

        for testCase in file.cases {
            var analyzer = GolfSwingMotionAnalyzer()
            var signal = analyzer.currentSignal(at: 0)
            for raw in testCase.samples {
                signal = analyzer.observe(raw.resolved(with: defaults))
            }
            let expect = testCase.expect
            if let phase = expect.phase {
                XCTAssertEqual(signal.phase, try motionPhase(phase), testCase.id)
            }
            XCTAssertEqual(signal.isImpactWindow, expect.isImpactWindow, testCase.id)
            if let minConfidence = expect.minConfidence {
                XCTAssertGreaterThanOrEqual(signal.confidence, minConfidence, testCase.id)
            }
            if let impactTime = expect.impactTime {
                XCTAssertEqual(
                    try XCTUnwrap(signal.impactTime, testCase.id),
                    impactTime,
                    accuracy: 1e-9,
                    testCase.id
                )
            }
            if let lastGlobalChangeTime = expect.lastGlobalChangeTime {
                XCTAssertEqual(
                    analyzer.lastGlobalChangeTime,
                    lastGlobalChangeTime,
                    accuracy: 1e-9,
                    testCase.id
                )
            }
        }
    }

    func testSwingPoseFixture() throws {
        let file: FixtureFile<SwingPoseCase, PoseSampleDefaults> =
            try Fixtures.load("swing-pose.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)
        let defaults = try XCTUnwrap(file.defaults)

        for testCase in file.cases {
            var analyzer = GolfSwingPoseAnalyzer()
            var signal = analyzer.currentSignal(at: 0)
            for raw in testCase.samples {
                signal = analyzer.observe(raw.resolved(with: defaults))
            }
            let expect = testCase.expect
            XCTAssertEqual(signal.phase, try posePhase(expect.phase), testCase.id)
            if let minConfidence = expect.minConfidence {
                XCTAssertGreaterThanOrEqual(signal.confidence, minConfidence, testCase.id)
            }
            if let start = expect.impactWindowStart {
                XCTAssertEqual(
                    try XCTUnwrap(signal.impactWindowStart, testCase.id),
                    start,
                    accuracy: 1e-9,
                    testCase.id
                )
            }
            if let end = expect.impactWindowEnd {
                XCTAssertEqual(
                    try XCTUnwrap(signal.impactWindowEnd, testCase.id),
                    end,
                    accuracy: 1e-9,
                    testCase.id
                )
            }
            for time in expect.isImpactWindowAt ?? [] {
                XCTAssertTrue(signal.isImpactWindow(at: time), "\(testCase.id) @\(time)")
            }
            for time in expect.notImpactWindowAt ?? [] {
                XCTAssertFalse(signal.isImpactWindow(at: time), "\(testCase.id) @\(time)")
            }
        }
    }

    func testPuttStrokeFixture() throws {
        let file: FixtureFile<PuttStrokeCase, PoseSampleDefaults> =
            try Fixtures.load("putt-stroke.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)
        let defaults = try XCTUnwrap(file.defaults)

        for testCase in file.cases {
            var analyzer = GolfPuttStrokeAnalyzer()
            var signal = analyzer.currentSignal()
            for raw in testCase.samples {
                signal = analyzer.observe(raw.resolved(with: defaults))
            }
            let expect = testCase.expect
            XCTAssertEqual(signal.phase, try puttPhase(expect.phase), testCase.id)
            XCTAssertEqual(signal.isConfirmedStroke, expect.isConfirmedStroke, testCase.id)
            if let minConfidence = expect.minConfidence {
                XCTAssertGreaterThanOrEqual(signal.confidence, minConfidence, testCase.id)
            }
            if let strokeTime = expect.strokeTime {
                XCTAssertEqual(
                    try XCTUnwrap(signal.strokeTime, testCase.id),
                    strokeTime,
                    accuracy: 1e-9,
                    testCase.id
                )
            }
            for time in expect.latchedAt ?? [] {
                XCTAssertNotNil(analyzer.latchedConfirmedStroke(at: time), "\(testCase.id) latched @\(time)")
            }
            for time in expect.notLatchedAt ?? [] {
                XCTAssertNil(analyzer.latchedConfirmedStroke(at: time), "\(testCase.id) not latched @\(time)")
            }
        }
    }

    func testAudioImpactFixture() throws {
        let file: FixtureFile<AudioImpactCase, NoDefaults> =
            try Fixtures.load("audio-impact.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)

        for testCase in file.cases {
            let decision = AudioImpactClassifier.detectImpact(
                metrics: testCase.metrics.metrics,
                baseline: testCase.baseline,
                previousRecentLevel: testCase.previousRecentLevel,
                sensitivity: try sensitivity(testCase.sensitivity)
            )
            XCTAssertEqual(decision.isTriggered, testCase.expect.isTriggered, testCase.id)
            if testCase.expect.confidenceIsZero == true {
                XCTAssertEqual(decision.confidence, 0, accuracy: 0, testCase.id)
            }
            if let minConfidence = testCase.expect.minConfidence {
                XCTAssertGreaterThanOrEqual(decision.confidence, minConfidence, testCase.id)
            }
        }
    }

    func testSwingFusionFixture() throws {
        let file: FixtureFile<SwingFusionCase, NoDefaults> =
            try Fixtures.load("swing-fusion.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)

        for testCase in file.cases {
            let motion = GolfSwingMotionSignal(
                phase: try motionPhase(testCase.motion.phase),
                confidence: testCase.motion.confidence,
                impactTime: testCase.motion.impactTime
            )
            let pose = try testCase.pose.map { raw in
                GolfSwingPoseSignal(
                    phase: try posePhase(raw.phase),
                    confidence: raw.confidence,
                    impactWindowStart: raw.impactWindowStart,
                    impactWindowEnd: raw.impactWindowEnd
                )
            }
            let result = GolfSwingFusionPolicy.shouldTrigger(
                decision: testCase.decision.decision,
                metrics: testCase.metrics.metrics,
                motion: motion,
                pose: pose,
                referenceTime: testCase.referenceTime,
                requiresPoseConfirmation: testCase.requiresPoseConfirmation,
                hasRecentVisualFrame: testCase.hasRecentVisualFrame,
                isInsideReadyPromptWindow: testCase.isInsideReadyPromptWindow,
                modelVersion: try modelVersion(testCase.modelVersion)
            )
            XCTAssertEqual(result, testCase.expect, testCase.id)
        }
    }

    func testPuttFusionFixture() throws {
        let file: FixtureFile<PuttFusionCase, NoDefaults> =
            try Fixtures.load("putt-fusion.json")
        XCTAssertEqual(file.golfModelVersion, GolfModelVersion.current.rawValue)

        for testCase in file.cases {
            let stroke = try testCase.stroke.map { raw in
                GolfPuttStrokeSignal(
                    phase: try puttPhase(raw.phase),
                    confidence: raw.confidence,
                    strokeTime: raw.strokeTime
                )
            }
            let result = GolfPuttFusionPolicy.shouldTrigger(
                stroke: stroke,
                poseObservationConfidence: testCase.poseObservationConfidence,
                secondsSinceLatestPose: testCase.secondsSinceLatestPose,
                secondsSinceLatestVisualFrame: testCase.secondsSinceLatestVisualFrame,
                secondsSinceLastGlobalChange: testCase.secondsSinceLastGlobalChange,
                isReady: testCase.isReady,
                isInsideReadyPromptWindow: testCase.isInsideReadyPromptWindow,
                isTriggerPending: testCase.isTriggerPending,
                modelVersion: try modelVersion(testCase.modelVersion)
            )
            XCTAssertEqual(result, testCase.expect, testCase.id)
        }
    }

    func testDedicatedPutterDetectorSeparatesShortStrokeFromStaticAndLargeMotion() {
        func sample(_ time: Double, handX: Double, coreX: Double = 0.5) -> GolfSwingPoseSample {
            GolfSwingPoseSample(
                time: time,
                handX: handX,
                handY: 0.5,
                coreX: coreX,
                coreY: 0.5,
                bodyScale: 0.1,
                confidence: 0.9
            )
        }

        let putt = [
            sample(0.0, handX: 0.00), sample(0.2, handX: 0.00), sample(0.4, handX: 0.01),
            sample(0.6, handX: 0.10), sample(0.8, handX: 0.20), sample(1.0, handX: 0.12),
            sample(1.2, handX: 0.02), sample(1.4, handX: 0.08), sample(1.6, handX: 0.10)
        ]
        let staticSamples = Array(0..<9).map { index in sample(Double(index) * 0.2, handX: 0.02) }
        let largeMotion = [
            sample(0.0, handX: 0.00, coreX: 0.2), sample(0.2, handX: 0.00, coreX: 0.2),
            sample(0.4, handX: 0.10, coreX: 0.3), sample(0.6, handX: 0.35, coreX: 0.5),
            sample(0.8, handX: 0.65, coreX: 0.8), sample(1.0, handX: 0.30, coreX: 0.95),
            sample(1.2, handX: 0.00, coreX: 1.0), sample(1.4, handX: 0.20, coreX: 1.0),
            sample(1.6, handX: 0.40, coreX: 1.0)
        ]

        XCTAssertTrue(GolfPutterDetector.evaluate(samples: putt).isCandidate)
        XCTAssertFalse(GolfPutterDetector.evaluate(samples: staticSamples).isCandidate)
        XCTAssertFalse(GolfPutterDetector.evaluate(samples: largeMotion).isCandidate)

        let wideRangeFromStart = [
            sample(0.0, handX: 0.00), sample(0.2, handX: 0.00), sample(0.4, handX: 0.01),
            sample(0.6, handX: 0.30), sample(0.8, handX: 0.20), sample(1.0, handX: -0.05),
            sample(1.2, handX: 0.00), sample(1.4, handX: 0.02), sample(1.6, handX: 0.04)
        ]
        XCTAssertFalse(GolfPutterDetector.evaluate(samples: wideRangeFromStart).isCandidate)

        let padded = Array(0..<31).map { index in
            let time = Double(index) * 0.2
            let handX: Double
            switch time {
            case 2.0..<2.8: handX = [0.00, 0.00, 0.01, 0.10, 0.20, 0.12, 0.02, 0.08, 0.10][min(Int((time - 2.0) / 0.2), 8)]
            default: handX = 0.02
            }
            return sample(time, handX: handX)
        }
        XCTAssertFalse(GolfPutterDetector.evaluate(samples: padded).isCandidate)
        let seriesDetections = GolfPutterDetector.evaluateSeries(samples: padded)
        XCTAssertEqual(seriesDetections.count, 1)
    }
}
