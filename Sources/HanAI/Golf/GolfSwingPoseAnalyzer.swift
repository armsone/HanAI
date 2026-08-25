import Foundation

/// 자세(관절) 기반 스윙 단계.
public enum GolfSwingPosePhase: String, Equatable, Sendable {
    case seekingAddress
    case addressed
    case backswing
    case impactWindow
}

/// 한 번의 자세 추정 결과를 정규화한 샘플. 관절 원본 좌표는 넘기지 않는다.
///
/// - `handX`, `handY`: 몸 크기(`bodyScale`)로 정규화한 손(손목 평균) 위치
/// - `coreX`, `coreY`: 어깨·골반 중심(정규화 화면 좌표)
/// - `bodyScale`: 어깨 폭 등 몸 크기(정규화 화면 단위)
/// - `confidence`: 자세 추정 신뢰도(0...1)
public struct GolfSwingPoseSample: Equatable, Sendable {
    public let time: Double
    public let handX: Double
    public let handY: Double
    public let coreX: Double
    public let coreY: Double
    public let bodyScale: Double
    public let confidence: Double

    public init(
        time: Double,
        handX: Double,
        handY: Double,
        coreX: Double,
        coreY: Double,
        bodyScale: Double,
        confidence: Double
    ) {
        self.time = time
        self.handX = handX
        self.handY = handY
        self.coreX = coreX
        self.coreY = coreY
        self.bodyScale = bodyScale
        self.confidence = confidence
    }
}

public struct GolfSwingPoseSignal: Equatable, Sendable {
    public let phase: GolfSwingPosePhase
    public let confidence: Double
    public let impactWindowStart: Double?
    public let impactWindowEnd: Double?

    public init(
        phase: GolfSwingPosePhase,
        confidence: Double,
        impactWindowStart: Double?,
        impactWindowEnd: Double?
    ) {
        self.phase = phase
        self.confidence = confidence
        self.impactWindowStart = impactWindowStart
        self.impactWindowEnd = impactWindowEnd
    }

    public func isImpactWindow(at time: Double) -> Bool {
        guard phase == .impactWindow,
              let impactWindowStart,
              let impactWindowEnd
        else { return false }
        return time >= impactWindowStart && time <= impactWindowEnd
    }
}

/// 자세 스윙 상태기계: 정지 주소 → 손 이동(백스윙) → 빠른 복귀 → 임팩트 창.
///
/// 임팩트 창은 복귀 감지 시각 기준 `[-0.45초, +0.30초]`다.
/// 좌타·우타·미러에 공통인 "주소 지점 대비 상대 이동축"만 사용한다.
public struct GolfSwingPoseAnalyzer: Sendable {
    public private(set) var phase = GolfSwingPosePhase.seekingAddress

    private var quietSince: Double?
    private var addressX = 0.0
    private var addressY = 0.0
    private var addressSamples = 0
    private var previousSample: GolfSwingPoseSample?
    private var backswingDirectionX = 0.0
    private var backswingDirectionY = 0.0
    private var backswingStart: Double?
    private var peakProgress = 0.0
    private var previousProgress = 0.0
    private var downswingSamples = 0
    private var impactWindowStart: Double?
    private var impactWindowEnd: Double?
    private var latestConfidence = 0.0

    public init() {}

    public mutating func reset() {
        phase = .seekingAddress
        quietSince = nil
        addressX = 0
        addressY = 0
        addressSamples = 0
        previousSample = nil
        backswingDirectionX = 0
        backswingDirectionY = 0
        backswingStart = nil
        peakProgress = 0
        previousProgress = 0
        downswingSamples = 0
        impactWindowStart = nil
        impactWindowEnd = nil
        latestConfidence = 0
    }

    @discardableResult
    public mutating func observe(
        _ sample: GolfSwingPoseSample
    ) -> GolfSwingPoseSignal {
        guard sample.confidence >= 0.45, sample.bodyScale >= 0.04 else {
            return currentSignal(at: sample.time)
        }
        latestConfidence = sample.confidence

        if let previousSample {
            let sampleGap = sample.time - previousSample.time
            let scaleChange = abs(sample.bodyScale - previousSample.bodyScale)
                / max(0.001, previousSample.bodyScale)
            if sampleGap <= 0 || sampleGap > 0.6 || scaleChange > 0.25 {
                reset()
            }
        }

        let previous = previousSample
        defer { previousSample = sample }

        switch phase {
        case .seekingAddress:
            guard let previous else {
                beginAddressAverage(with: sample)
                return currentSignal(at: sample.time)
            }
            let deltaTime = max(0.05, sample.time - previous.time)
            let handSpeed = hypot(
                sample.handX - previous.handX,
                sample.handY - previous.handY
            ) / deltaTime
            let coreSpeed = hypot(
                sample.coreX - previous.coreX,
                sample.coreY - previous.coreY
            ) / deltaTime / max(0.04, sample.bodyScale)
            if handSpeed <= 0.22 && coreSpeed <= 0.20 {
                quietSince = quietSince ?? previous.time
                addToAddressAverage(sample)
                if sample.time - (quietSince ?? sample.time) >= 0.55,
                   addressSamples >= 3 {
                    phase = .addressed
                }
            } else {
                quietSince = nil
                beginAddressAverage(with: sample)
            }

        case .addressed:
            let dx = sample.handX - addressX
            let dy = sample.handY - addressY
            let displacement = hypot(dx, dy)
            if displacement >= 0.20 {
                backswingDirectionX = dx / displacement
                backswingDirectionY = dy / displacement
                backswingStart = sample.time
                peakProgress = displacement
                previousProgress = displacement
                downswingSamples = 0
                phase = .backswing
            }

        case .backswing:
            guard let backswingStart, sample.time - backswingStart <= 1.8 else {
                reset()
                return currentSignal(at: sample.time)
            }
            let elapsed = sample.time - backswingStart
            let progress = (sample.handX - addressX) * backswingDirectionX
                + (sample.handY - addressY) * backswingDirectionY
            peakProgress = max(peakProgress, progress)
            let deltaTime = max(
                0.05,
                sample.time - (previous?.time ?? sample.time - 0.2)
            )
            let returnSpeed = (previousProgress - progress) / deltaTime
            if elapsed >= 0.18,
               peakProgress >= 0.28,
               progress < previousProgress,
               returnSpeed >= 0.55 {
                downswingSamples += 1
            } else if progress >= previousProgress {
                downswingSamples = 0
            }
            previousProgress = progress

            let returnedEnough = peakProgress - progress
                >= max(0.18, peakProgress * 0.55)
            if downswingSamples >= 2 && returnedEnough {
                phase = .impactWindow
                impactWindowStart = sample.time - 0.45
                impactWindowEnd = sample.time + 0.30
            }

        case .impactWindow:
            if sample.time > (impactWindowEnd ?? sample.time) {
                reset()
            }
        }

        return currentSignal(at: sample.time)
    }

    public func currentSignal(at time: Double) -> GolfSwingPoseSignal {
        switch phase {
        case .impactWindow:
            return GolfSwingPoseSignal(
                phase: .impactWindow,
                confidence: min(
                    latestConfidence,
                    min(1, 0.72 + peakProgress * 0.45)
                ),
                impactWindowStart: impactWindowStart,
                impactWindowEnd: impactWindowEnd
            )
        case .backswing:
            return GolfSwingPoseSignal(
                phase: .backswing,
                confidence: min(0.7, 0.35 + peakProgress),
                impactWindowStart: nil,
                impactWindowEnd: nil
            )
        case .addressed:
            return GolfSwingPoseSignal(
                phase: .addressed,
                confidence: 0.32,
                impactWindowStart: nil,
                impactWindowEnd: nil
            )
        case .seekingAddress:
            return GolfSwingPoseSignal(
                phase: .seekingAddress,
                confidence: 0,
                impactWindowStart: nil,
                impactWindowEnd: nil
            )
        }
    }

    private mutating func beginAddressAverage(with sample: GolfSwingPoseSample) {
        addressX = sample.handX
        addressY = sample.handY
        addressSamples = 1
    }

    private mutating func addToAddressAverage(_ sample: GolfSwingPoseSample) {
        addressSamples += 1
        let weight = 1.0 / Double(addressSamples)
        addressX += (sample.handX - addressX) * weight
        addressY += (sample.handY - addressY) * weight
    }
}
