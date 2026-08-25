import Foundation
import XCTest
import HanAI

/// 저장소 루트 `Fixtures/golf/*.json`을 읽는다. Kotlin 테스트와 같은 파일을 공유한다.
enum Fixtures {
    static let rootDirectory: URL = URL(fileURLWithPath: #filePath)
        .deletingLastPathComponent() // HanAITests
        .deletingLastPathComponent() // Tests
        .deletingLastPathComponent() // 저장소 루트
        .appendingPathComponent("Fixtures")

    static func load<T: Decodable>(_ name: String) throws -> T {
        let url = rootDirectory
            .appendingPathComponent("golf")
            .appendingPathComponent(name)
        let data = try Data(contentsOf: url)
        return try JSONDecoder().decode(T.self, from: data)
    }
}

struct FixtureFile<Case: Decodable, Defaults: Decodable>: Decodable {
    let schemaVersion: Int
    let golfModelVersion: String
    let kind: String
    let description: String?
    let defaults: Defaults?
    let cases: [Case]
}

struct NoDefaults: Decodable {}

// MARK: - swingMotion

struct VisualSampleDefaults: Decodable {
    let globalMotion: Double
    let widespreadMotion: Double
    let concentration: Double
    let brightnessChange: Double
    let dominantRegion: Int
}

struct VisualSampleRaw: Decodable {
    let time: Double
    let localMotion: Double
    let globalMotion: Double?
    let widespreadMotion: Double?
    let concentration: Double?
    let brightnessChange: Double?
    let dominantRegion: Int?

    func resolved(with defaults: VisualSampleDefaults) -> GolfSwingVisualSample {
        GolfSwingVisualSample(
            time: time,
            localMotion: localMotion,
            globalMotion: globalMotion ?? defaults.globalMotion,
            widespreadMotion: widespreadMotion ?? defaults.widespreadMotion,
            concentration: concentration ?? defaults.concentration,
            brightnessChange: brightnessChange ?? defaults.brightnessChange,
            dominantRegion: dominantRegion ?? defaults.dominantRegion
        )
    }
}

struct SwingMotionExpect: Decodable {
    let phase: String?
    let isImpactWindow: Bool
    let minConfidence: Double?
    let impactTime: Double?
    let lastGlobalChangeTime: Double?
}

struct SwingMotionCase: Decodable {
    let id: String
    let description: String?
    let samples: [VisualSampleRaw]
    let expect: SwingMotionExpect
}

// MARK: - swingPose / puttStroke

struct PoseSampleDefaults: Decodable {
    let handY: Double
    let coreX: Double
    let coreY: Double
    let bodyScale: Double
    let confidence: Double
}

struct PoseSampleRaw: Decodable {
    let time: Double
    let handX: Double
    let handY: Double?
    let coreX: Double?
    let coreY: Double?
    let bodyScale: Double?
    let confidence: Double?

    func resolved(with defaults: PoseSampleDefaults) -> GolfSwingPoseSample {
        GolfSwingPoseSample(
            time: time,
            handX: handX,
            handY: handY ?? defaults.handY,
            coreX: coreX ?? defaults.coreX,
            coreY: coreY ?? defaults.coreY,
            bodyScale: bodyScale ?? defaults.bodyScale,
            confidence: confidence ?? defaults.confidence
        )
    }
}

struct SwingPoseExpect: Decodable {
    let phase: String
    let minConfidence: Double?
    let impactWindowStart: Double?
    let impactWindowEnd: Double?
    let isImpactWindowAt: [Double]?
    let notImpactWindowAt: [Double]?
}

struct SwingPoseCase: Decodable {
    let id: String
    let description: String?
    let samples: [PoseSampleRaw]
    let expect: SwingPoseExpect
}

struct PuttStrokeExpect: Decodable {
    let phase: String
    let isConfirmedStroke: Bool
    let minConfidence: Double?
    let strokeTime: Double?
    let latchedAt: [Double]?
    let notLatchedAt: [Double]?
}

struct PuttStrokeCase: Decodable {
    let id: String
    let description: String?
    let samples: [PoseSampleRaw]
    let expect: PuttStrokeExpect
}

// MARK: - audioImpact

struct AudioMetricsRaw: Decodable {
    let rms: Double
    let peak: Double
    let crossingRate: Double

    var metrics: AudioImpactMetrics {
        AudioImpactMetrics(rms: rms, peak: peak, crossingRate: crossingRate)
    }
}

struct AudioImpactExpect: Decodable {
    let isTriggered: Bool
    let confidenceIsZero: Bool?
    let minConfidence: Double?
}

struct AudioImpactCase: Decodable {
    let id: String
    let description: String?
    let metrics: AudioMetricsRaw
    let baseline: Double
    let previousRecentLevel: Double
    let sensitivity: String
    let expect: AudioImpactExpect
}

// MARK: - swingFusion

struct DecisionRaw: Decodable {
    let isTriggered: Bool
    let confidence: Double

    var decision: AudioImpactDecision {
        AudioImpactDecision(isTriggered: isTriggered, confidence: confidence)
    }
}

struct MotionSignalRaw: Decodable {
    let phase: String
    let confidence: Double
    let impactTime: Double?
}

struct PoseSignalRaw: Decodable {
    let phase: String
    let confidence: Double
    let impactWindowStart: Double?
    let impactWindowEnd: Double?
}

struct SwingFusionCase: Decodable {
    let id: String
    let description: String?
    let decision: DecisionRaw
    let metrics: AudioMetricsRaw
    let motion: MotionSignalRaw
    let pose: PoseSignalRaw?
    let referenceTime: Double
    let requiresPoseConfirmation: Bool
    let hasRecentVisualFrame: Bool
    let isInsideReadyPromptWindow: Bool
    let modelVersion: String
    let expect: Bool
}

// MARK: - puttFusion

struct StrokeSignalRaw: Decodable {
    let phase: String
    let confidence: Double
    let strokeTime: Double?
}

struct PuttFusionCase: Decodable {
    let id: String
    let description: String?
    let stroke: StrokeSignalRaw?
    let poseObservationConfidence: Double
    let secondsSinceLatestPose: Double
    let secondsSinceLatestVisualFrame: Double
    let secondsSinceLastGlobalChange: Double
    let isReady: Bool
    let isInsideReadyPromptWindow: Bool
    let isTriggerPending: Bool
    let modelVersion: String
    let expect: Bool
}

// MARK: - 문자열 → 타입 변환

func motionPhase(_ raw: String, file: StaticString = #filePath, line: UInt = #line) throws -> GolfSwingMotionPhase {
    try XCTUnwrap(GolfSwingMotionPhase(rawValue: raw), "unknown motion phase \(raw)", file: file, line: line)
}

func posePhase(_ raw: String, file: StaticString = #filePath, line: UInt = #line) throws -> GolfSwingPosePhase {
    try XCTUnwrap(GolfSwingPosePhase(rawValue: raw), "unknown pose phase \(raw)", file: file, line: line)
}

func puttPhase(_ raw: String, file: StaticString = #filePath, line: UInt = #line) throws -> GolfPuttStrokePhase {
    try XCTUnwrap(GolfPuttStrokePhase(rawValue: raw), "unknown putt phase \(raw)", file: file, line: line)
}

func modelVersion(_ raw: String, file: StaticString = #filePath, line: UInt = #line) throws -> GolfModelVersion {
    try XCTUnwrap(GolfModelVersion(rawValue: raw), "unknown model version \(raw)", file: file, line: line)
}

func sensitivity(_ raw: String, file: StaticString = #filePath, line: UInt = #line) throws -> AudioImpactSensitivity {
    try XCTUnwrap(AudioImpactSensitivity(rawValue: raw), "unknown sensitivity \(raw)", file: file, line: line)
}
