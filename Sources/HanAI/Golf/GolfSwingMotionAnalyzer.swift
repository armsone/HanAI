import Foundation

/// 화면 격자 움직임 기반 스윙 단계.
public enum GolfSwingMotionPhase: String, Equatable, Sendable {
    case seekingAddress
    case addressed
    case backswing
    case downswing
}

/// 한 프레임의 화면 움직임 요약. adapter가 저해상도 격자 차분에서 계산한다.
///
/// - `localMotion`: 가장 활발한 국소 영역의 움직임(0...1)
/// - `globalMotion`: 화면 전체 평균 움직임(0...1)
/// - `widespreadMotion`: 움직인 격자 비율(0...1)
/// - `concentration`: 움직임이 한 영역에 집중된 정도(0...1)
/// - `brightnessChange`: 평균 밝기 변화량(0...1)
/// - `dominantRegion`: 가장 활발한 영역 인덱스(격자 열 등 adapter 정의)
public struct GolfSwingVisualSample: Equatable, Sendable {
    public let time: Double
    public let localMotion: Double
    public let globalMotion: Double
    public let widespreadMotion: Double
    public let concentration: Double
    public let brightnessChange: Double
    public let dominantRegion: Int

    public init(
        time: Double,
        localMotion: Double,
        globalMotion: Double,
        widespreadMotion: Double,
        concentration: Double,
        brightnessChange: Double,
        dominantRegion: Int
    ) {
        self.time = time
        self.localMotion = localMotion
        self.globalMotion = globalMotion
        self.widespreadMotion = widespreadMotion
        self.concentration = concentration
        self.brightnessChange = brightnessChange
        self.dominantRegion = dominantRegion
    }
}

public struct GolfSwingMotionSignal: Equatable, Sendable {
    public let phase: GolfSwingMotionPhase
    public let confidence: Double
    public let impactTime: Double?

    public init(phase: GolfSwingMotionPhase, confidence: Double, impactTime: Double?) {
        self.phase = phase
        self.confidence = confidence
        self.impactTime = impactTime
    }

    public var isImpactWindow: Bool {
        phase == .downswing && impactTime != nil
    }
}

/// 화면 움직임 상태기계: 정지(주소) → 국소 백스윙 → 가속 다운스윙.
///
/// 화면 전체가 함께 움직이는 팬·흔들림·밝기 급변은 스윙 근거에서 제외하고
/// 두 번 연속이면 상태를 초기화한다. `lastGlobalChangeTime`은 무음 퍼팅
/// 장면 안정성 판정용이며 `reset()`으로 지워지지 않는다.
public struct GolfSwingMotionAnalyzer: Sendable {
    public private(set) var phase = GolfSwingMotionPhase.seekingAddress
    public private(set) var lastGlobalChangeTime = -Double.infinity

    private var quietSince: Double?
    private var motionCandidateSince: Double?
    private var motionCandidateCount = 0
    private var backswingStart: Double?
    private var backswingPeak = 0.0
    private var backswingSamples = 0
    private var previousMotion = 0.0
    private var lockedRegion: Int?
    private var downswingTime: Double?
    private var globalMotionCount = 0

    public init() {}

    public mutating func reset() {
        phase = .seekingAddress
        quietSince = nil
        motionCandidateSince = nil
        motionCandidateCount = 0
        backswingStart = nil
        backswingPeak = 0
        backswingSamples = 0
        previousMotion = 0
        lockedRegion = nil
        downswingTime = nil
        globalMotionCount = 0
    }

    @discardableResult
    public mutating func observe(
        _ sample: GolfSwingVisualSample
    ) -> GolfSwingMotionSignal {
        let isGlobalChange = sample.globalMotion >= 0.16
            || sample.widespreadMotion >= 0.68
            || sample.brightnessChange >= 0.14
        if isGlobalChange {
            lastGlobalChangeTime = sample.time
            globalMotionCount += 1
            if globalMotionCount >= 2 {
                reset()
            }
            return currentSignal(at: sample.time)
        }
        globalMotionCount = 0

        switch phase {
        case .seekingAddress:
            let isQuiet = sample.localMotion <= 0.075
                && sample.globalMotion <= 0.08
                && sample.widespreadMotion <= 0.32
            if isQuiet {
                quietSince = quietSince ?? sample.time
                if sample.time - (quietSince ?? sample.time) >= 0.55 {
                    phase = .addressed
                    motionCandidateSince = nil
                    motionCandidateCount = 0
                }
            } else {
                quietSince = nil
            }

        case .addressed:
            let isBackswingCandidate = sample.localMotion >= 0.12
                && sample.concentration >= 0.38
                && sample.widespreadMotion >= 0.04
                && sample.widespreadMotion <= 0.58
            if isBackswingCandidate {
                if let since = motionCandidateSince,
                   sample.time - since <= 0.35,
                   abs((lockedRegion ?? sample.dominantRegion)
                       - sample.dominantRegion) <= 1 {
                    motionCandidateCount += 1
                } else {
                    motionCandidateSince = sample.time
                    motionCandidateCount = 1
                    lockedRegion = sample.dominantRegion
                }

                if motionCandidateCount >= 2 {
                    phase = .backswing
                    backswingStart = motionCandidateSince
                    backswingPeak = sample.localMotion
                    backswingSamples = motionCandidateCount
                    previousMotion = sample.localMotion
                }
            } else if sample.localMotion <= 0.085 {
                motionCandidateSince = nil
                motionCandidateCount = 0
                lockedRegion = nil
            }

        case .backswing:
            guard let backswingStart else {
                reset()
                return currentSignal(at: sample.time)
            }
            let elapsed = sample.time - backswingStart
            let movedToAnotherRegion = abs(
                (lockedRegion ?? sample.dominantRegion) - sample.dominantRegion
            ) > 1
                && sample.localMotion >= 0.18
            if elapsed > 1.8 || movedToAnotherRegion {
                reset()
                return currentSignal(at: sample.time)
            }

            if sample.localMotion >= 0.10 {
                backswingSamples += 1
            }
            let acceleration = sample.localMotion - previousMotion
            let previousPeak = backswingPeak
            let isDownswing = elapsed >= 0.18
                && backswingSamples >= 3
                && sample.localMotion >= 0.20
                && (acceleration >= 0.035
                    || sample.localMotion >= max(0.26, previousPeak * 1.15))
            backswingPeak = max(backswingPeak, sample.localMotion)
            previousMotion = sample.localMotion

            if isDownswing {
                phase = .downswing
                downswingTime = sample.time
            }

        case .downswing:
            if let downswingTime, sample.time - downswingTime > 0.42 {
                reset()
            }
        }

        return currentSignal(at: sample.time)
    }

    public func currentSignal(at time: Double) -> GolfSwingMotionSignal {
        switch phase {
        case .downswing:
            guard let downswingTime, time - downswingTime <= 0.42 else {
                return GolfSwingMotionSignal(
                    phase: .seekingAddress,
                    confidence: 0,
                    impactTime: nil
                )
            }
            return GolfSwingMotionSignal(
                phase: .downswing,
                confidence: max(0.72, min(1, 0.62 + backswingPeak * 0.95)),
                impactTime: downswingTime
            )
        case .backswing:
            return GolfSwingMotionSignal(
                phase: .backswing,
                confidence: min(0.7, 0.34 + backswingPeak),
                impactTime: nil
            )
        case .addressed:
            return GolfSwingMotionSignal(
                phase: .addressed,
                confidence: 0.28,
                impactTime: nil
            )
        case .seekingAddress:
            return GolfSwingMotionSignal(
                phase: .seekingAddress,
                confidence: 0,
                impactTime: nil
            )
        }
    }
}
