import Foundation

/// 무음 퍼팅 상태기계 단계.
public enum GolfPuttStrokePhase: String, Equatable, Sendable {
    case seekingAddress
    case addressed
    case backswing
    case forwardStroke
    case confirmedStroke
}

public struct GolfPuttStrokeSignal: Equatable, Sendable {
    public let phase: GolfPuttStrokePhase
    public let confidence: Double
    public let strokeTime: Double?

    public init(phase: GolfPuttStrokePhase, confidence: Double, strokeTime: Double?) {
        self.phase = phase
        self.confidence = confidence
        self.strokeTime = strokeTime
    }

    public var isConfirmedStroke: Bool {
        phase == .confirmedStroke && strokeTime != nil
    }
}

/// 무음 퍼팅 상태기계 (모델 0.6.0).
///
/// 정지 주소(0.60초 이상, 3샘플 이상) → 작은 백스윙(몸 크기 대비 0.06 이상 시작,
/// 최고 0.12 이상 0.60 이하) → 주소 지점을 지나는 전진 스트로크(복귀 2샘플 이상)
/// → 짧은 팔로스루(2샘플 이상)가 순서대로 이어질 때만 확정한다.
///
/// 아이언 오탐 방지:
/// - 백스윙 폭이 0.60을 넘으면 스윙으로 보고 초기화
/// - 주소 지점을 1.5/s보다 빠르게 통과하면(return speed) 스윙으로 보고 초기화
/// - 팔로스루가 `max(0.30, peak×1.2)`를 넘으면(큰 follow-through) 초기화
/// - 몸통(core) 이동 속도가 0.9/s를 넘으면(걷기·이동·큰 회전) 어느 단계든 초기화
///
/// 확정 뒤 2.0초 동안은 재확정하지 않으며, 0.7.0 확정 신호는 0.60초 동안 latch로 유지된다.
public struct GolfPuttStrokeAnalyzer: Sendable {
    public private(set) var phase = GolfPuttStrokePhase.seekingAddress
    public private(set) var lastConfirmedTime = -Double.infinity

    private var quietSince: Double?
    private var addressX = 0.0
    private var addressY = 0.0
    private var addressSamples = 0
    private var previousSample: GolfSwingPoseSample?
    private var directionX = 0.0
    private var directionY = 0.0
    private var backswingStart: Double?
    private var peakProgress = 0.0
    private var previousProgress = 0.0
    private var returningSamples = 0
    private var followThroughSamples = 0
    private var strokeTime: Double?
    private var sequenceMinimumConfidence = 1.0
    private var confirmedSignal: GolfPuttStrokeSignal?
    private let confirmationLatchDuration: Double

    public init(modelVersion: GolfModelVersion = .current) {
        confirmationLatchDuration = modelVersion == .v0_7_0 ? 0.60 : 0.35
    }

    /// 상태를 초기화한다. `lastConfirmedTime`은 재발동 금지용이라 유지한다.
    public mutating func reset() {
        phase = .seekingAddress
        quietSince = nil
        addressX = 0
        addressY = 0
        addressSamples = 0
        previousSample = nil
        directionX = 0
        directionY = 0
        backswingStart = nil
        peakProgress = 0
        previousProgress = 0
        returningSamples = 0
        followThroughSamples = 0
        strokeTime = nil
        sequenceMinimumConfidence = 1
        confirmedSignal = nil
    }

    /// 모델별 latch 시간 안에서만 확정 신호를 돌려준다. 지나면 latch를 비운다.
    public mutating func latchedConfirmedStroke(at now: Double) -> GolfPuttStrokeSignal? {
        guard let signal = confirmedSignal else { return nil }
        if now - lastConfirmedTime > confirmationLatchDuration {
            confirmedSignal = nil
            return nil
        }
        return signal
    }

    /// adapter가 촬영을 시작한 뒤 latch를 비운다.
    public mutating func consumeConfirmedStroke() {
        confirmedSignal = nil
    }

    @discardableResult
    public mutating func observe(
        _ sample: GolfSwingPoseSample
    ) -> GolfPuttStrokeSignal {
        guard sample.confidence >= 0.45, sample.bodyScale >= 0.04 else {
            return currentSignal()
        }

        if let previousSample {
            let sampleGap = sample.time - previousSample.time
            let scaleChange = abs(sample.bodyScale - previousSample.bodyScale)
                / max(0.001, previousSample.bodyScale)
            if sampleGap <= 0 || sampleGap > 0.6 || scaleChange > 0.25 {
                reset()
            }
        }

        let previous = previousSample
        previousSample = sample
        let deltaTime = previous.map { max(0.05, sample.time - $0.time) } ?? 0.2
        let coreSpeed = previous.map {
            hypot(sample.coreX - $0.coreX, sample.coreY - $0.coreY)
                / deltaTime / max(0.04, sample.bodyScale)
        } ?? 0
        // 걷기·이동·큰 몸통 회전은 어느 단계에서든 초기화한다.
        if coreSpeed > 0.9 {
            reset()
            previousSample = sample
            return currentSignal()
        }
        sequenceMinimumConfidence = min(sequenceMinimumConfidence, sample.confidence)

        switch phase {
        case .seekingAddress:
            let handSpeed = previous.map {
                hypot(sample.handX - $0.handX, sample.handY - $0.handY) / deltaTime
            } ?? 0
            if previous == nil || (handSpeed <= 0.25 && coreSpeed <= 0.20) {
                quietSince = quietSince ?? previous?.time ?? sample.time
                addressSamples += 1
                let weight = 1.0 / Double(addressSamples)
                addressX += (sample.handX - addressX) * weight
                addressY += (sample.handY - addressY) * weight
                if sample.time - (quietSince ?? sample.time) >= 0.60,
                   addressSamples >= 3 {
                    phase = .addressed
                    sequenceMinimumConfidence = sample.confidence
                }
            } else {
                quietSince = nil
                addressSamples = 1
                addressX = sample.handX
                addressY = sample.handY
            }

        case .addressed:
            let dx = sample.handX - addressX
            let dy = sample.handY - addressY
            let displacement = hypot(dx, dy)
            if displacement >= 0.06 {
                directionX = dx / displacement
                directionY = dy / displacement
                backswingStart = sample.time
                peakProgress = displacement
                previousProgress = displacement
                returningSamples = 0
                phase = .backswing
            }

        case .backswing:
            guard let backswingStart, sample.time - backswingStart <= 2.0 else {
                reset()
                return currentSignal()
            }
            let progress = (sample.handX - addressX) * directionX
                + (sample.handY - addressY) * directionY
            peakProgress = max(peakProgress, progress)
            // 아이언 반례 상한: 백스윙 폭이 퍼팅 범위를 넘으면 스윙으로 본다.
            if peakProgress > 0.60 {
                reset()
                return currentSignal()
            }
            let returnSpeed = (previousProgress - progress) / deltaTime
            let addressPassThreshold = max(0.04, peakProgress * 0.30)
            // 주소 지점을 너무 빠르게 통과하면 아이언/풀스윙 복귀로 본다.
            if progress <= addressPassThreshold && returnSpeed > 1.5 {
                reset()
                return currentSignal()
            }
            if sample.time - backswingStart >= 0.15,
               peakProgress >= 0.12,
               progress < previousProgress,
               returnSpeed >= 0.20 {
                returningSamples += 1
            } else if progress >= previousProgress {
                returningSamples = 0
            }
            previousProgress = progress
            if returningSamples >= 2 && progress <= addressPassThreshold {
                phase = .forwardStroke
                strokeTime = sample.time
                followThroughSamples = 0
            }

        case .forwardStroke:
            guard let strokeTime, sample.time - strokeTime <= 0.9 else {
                reset()
                return currentSignal()
            }
            let progress = (sample.handX - addressX) * directionX
                + (sample.handY - addressY) * directionY
            // 큰 팔로스루는 퍼팅이 아니라 스윙으로 본다.
            if progress <= -max(0.30, peakProgress * 1.2) {
                reset()
                return currentSignal()
            }
            if progress <= -max(0.025, peakProgress * 0.15) {
                followThroughSamples += 1
            }
            previousProgress = progress
            if followThroughSamples >= 2,
               sample.time - lastConfirmedTime >= 2.0 {
                phase = .confirmedStroke
                lastConfirmedTime = sample.time
                confirmedSignal = GolfPuttStrokeSignal(
                    phase: .confirmedStroke,
                    confidence: min(
                        sequenceMinimumConfidence,
                        min(1, 0.62 + peakProgress * 0.9)
                    ),
                    strokeTime: strokeTime
                )
            }

        case .confirmedStroke:
            // 한 스트로크 중복 방지: 확정 뒤에는 다시 정지 탐색부터 시작한다.
            let latched = confirmedSignal
            reset()
            confirmedSignal = latched
            previousSample = sample
        }

        return currentSignal()
    }

    public func currentSignal() -> GolfPuttStrokeSignal {
        switch phase {
        case .confirmedStroke:
            return confirmedSignal
                ?? GolfPuttStrokeSignal(phase: .confirmedStroke, confidence: 0, strokeTime: nil)
        case .forwardStroke:
            return GolfPuttStrokeSignal(
                phase: .forwardStroke,
                confidence: min(0.7, 0.4 + peakProgress),
                strokeTime: nil
            )
        case .backswing:
            return GolfPuttStrokeSignal(
                phase: .backswing,
                confidence: min(0.6, 0.3 + peakProgress),
                strokeTime: nil
            )
        case .addressed:
            return GolfPuttStrokeSignal(phase: .addressed, confidence: 0.3, strokeTime: nil)
        case .seekingAddress:
            return GolfPuttStrokeSignal(phase: .seekingAddress, confidence: 0, strokeTime: nil)
        }
    }
}
