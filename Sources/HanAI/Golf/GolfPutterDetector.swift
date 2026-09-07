import Foundation

/// 한 후보 창에서 계산한 퍼팅 전용 수치. 원본 관절 좌표는 보관하지 않는다.
public struct GolfPutterWindowFeatures: Equatable, Sendable {
    public let sampleCount: Int
    public let handRangeX: Double
    public let handRangeY: Double
    public let handPath: Double
    public let coreRange: Double
    public let peakHandSpeed: Double
    public let reversalCount: Int
    public let verticalRatio: Double

    public init(
        sampleCount: Int,
        handRangeX: Double,
        handRangeY: Double,
        handPath: Double,
        coreRange: Double,
        peakHandSpeed: Double,
        reversalCount: Int,
        verticalRatio: Double
    ) {
        self.sampleCount = sampleCount
        self.handRangeX = handRangeX
        self.handRangeY = handRangeY
        self.handPath = handPath
        self.coreRange = coreRange
        self.peakHandSpeed = peakHandSpeed
        self.reversalCount = reversalCount
        self.verticalRatio = verticalRatio
    }
}

public struct GolfPutterDetection: Equatable, Sendable {
    public let isCandidate: Bool
    public let confidence: Double
    public let strokeTime: Double?
    public let features: GolfPutterWindowFeatures?

    public init(
        isCandidate: Bool,
        confidence: Double,
        strokeTime: Double?,
        features: GolfPutterWindowFeatures?
    ) {
        self.isCandidate = isCandidate
        self.confidence = confidence
        self.strokeTime = strokeTime
        self.features = features
    }
}

/// 화면 peak가 아니라 사람의 짧은 왕복 동작을 후보로 고르는 전용 계층.
///
/// 이 타입은 최종 촬영 확정기가 아니다. 후보 창을 줄이는 단계이며, 반드시
/// `GolfPuttStrokeAnalyzer`와 앱의 화면·준비 상태 검증을 함께 통과해야 한다.
public enum GolfPutterDetector {
    /// 전수 포즈 스트림에서 주소-스트로크 순서를 만족한 퍼팅만 반환한다.
    /// 이동창 점수는 후보 회수용이고, 이 경로는 순서가 맞지 않는 비샷을 억제한다.
    public static func detectStrokes(
        samples: [GolfSwingPoseSample],
        modelVersion: GolfModelVersion = .current
    ) -> [GolfPutterDetection] {
        var analyzer = GolfPuttStrokeAnalyzer(modelVersion: modelVersion)
        var detections: [GolfPutterDetection] = []
        for sample in samples.sorted(by: { $0.time < $1.time }) {
            let signal = analyzer.observe(sample)
            guard signal.isConfirmedStroke, let strokeTime = signal.strokeTime else { continue }
            if detections.contains(where: { abs(($0.strokeTime ?? -.infinity) - strokeTime) < 1.0 }) {
                continue
            }
            detections.append(GolfPutterDetection(
                isCandidate: true,
                confidence: signal.confidence,
                strokeTime: strokeTime,
                features: nil
            ))
            analyzer.consumeConfirmedStroke()
        }
        return detections
    }

    /// 긴 후보 클립 안에서 실제 스트로크 중심을 다시 찾는다.
    /// 후보의 시작/끝을 그대로 믿지 않고 짧은 창을 이동시키는 것이 핵심이다.
    public static func evaluateSeries(
        samples: [GolfSwingPoseSample],
        windowSeconds: Double = 1.6,
        stepSeconds: Double = 0.2,
        minimumConfidence: Double = 0.45
    ) -> [GolfPutterDetection] {
        let ordered = samples.sorted { $0.time < $1.time }
        guard let first = ordered.first, let last = ordered.last,
              windowSeconds > 0, stepSeconds > 0,
              last.time - first.time >= windowSeconds
        else { return [] }

        var results: [GolfPutterDetection] = []
        var center = first.time + windowSeconds / 2.0
        while center <= last.time - windowSeconds / 2.0 {
            let half = windowSeconds / 2.0
            let window = ordered.filter { abs($0.time - center) <= half }
            let result = evaluate(samples: window, minimumConfidence: minimumConfidence)
            if result.isCandidate {
                results.append(GolfPutterDetection(
                    isCandidate: true,
                    confidence: result.confidence,
                    strokeTime: center,
                    features: result.features
                ))
            }
            center += stepSeconds
        }
        // Overlapping windows can describe the same short stroke. Keep only
        // the strongest detection in a one-second neighborhood so one putt
        // cannot inflate the candidate count.
        return results
            .sorted { lhs, rhs in
                if lhs.confidence == rhs.confidence {
                    return (lhs.strokeTime ?? .greatestFiniteMagnitude)
                        < (rhs.strokeTime ?? .greatestFiniteMagnitude)
                }
                return lhs.confidence > rhs.confidence
            }
            .reduce(into: [GolfPutterDetection]()) { kept, detection in
                guard let time = detection.strokeTime else { return }
                if kept.contains(where: {
                    guard let keptTime = $0.strokeTime else { return false }
                    return abs(keptTime - time) < 1.0
                }) {
                    return
                }
                kept.append(detection)
            }
            .sorted { ($0.strokeTime ?? .greatestFiniteMagnitude)
                < ($1.strokeTime ?? .greatestFiniteMagnitude) }
    }

    public static func evaluate(
        samples rawSamples: [GolfSwingPoseSample],
        minimumConfidence: Double = 0.45
    ) -> GolfPutterDetection {
        let samples = rawSamples
            .filter { $0.confidence >= minimumConfidence && $0.bodyScale >= 0.04 }
            .sorted { $0.time < $1.time }
        guard samples.count >= 7,
              samples.last!.time - samples.first!.time <= 4.0
        else {
            return GolfPutterDetection(isCandidate: false, confidence: 0, strokeTime: nil, features: nil)
        }

        var handMinX = samples[0].handX
        var handMaxX = samples[0].handX
        var handMinY = samples[0].handY
        var handMaxY = samples[0].handY
        var coreMinX = samples[0].coreX
        var coreMaxX = samples[0].coreX
        var coreMinY = samples[0].coreY
        var coreMaxY = samples[0].coreY
        var path = 0.0
        var peakSpeed = 0.0
        var reversals = 0
        var previousDeltaX: Double?

        for index in samples.indices {
            let sample = samples[index]
            handMinX = min(handMinX, sample.handX)
            handMaxX = max(handMaxX, sample.handX)
            handMinY = min(handMinY, sample.handY)
            handMaxY = max(handMaxY, sample.handY)
            coreMinX = min(coreMinX, sample.coreX)
            coreMaxX = max(coreMaxX, sample.coreX)
            coreMinY = min(coreMinY, sample.coreY)
            coreMaxY = max(coreMaxY, sample.coreY)
            guard index > samples.startIndex else { continue }

            let previous = samples[index - 1]
            let dt = max(0.05, sample.time - previous.time)
            let dx = sample.handX - previous.handX
            let dy = sample.handY - previous.handY
            path += hypot(dx, dy)
            peakSpeed = max(peakSpeed, hypot(dx, dy) / dt)
            if let previousDeltaX,
               abs(previousDeltaX) >= 0.008,
               abs(dx) >= 0.008,
               previousDeltaX.sign != dx.sign {
                reversals += 1
            }
            previousDeltaX = dx
        }

        let handRangeX = handMaxX - handMinX
        let handRangeY = handMaxY - handMinY
        let coreRange = hypot(coreMaxX - coreMinX, coreMaxY - coreMinY)
        let verticalRatio = handRangeY / max(handRangeX, 0.01)
        let hasShortHandTravel = handRangeX >= 0.05 && handRangeX <= 0.30
        let hasCompactBody = coreRange <= 0.32
        let hasControlledPath = path >= 0.08 && path <= 2.0
        let hasStrokeReversal = reversals >= 1
        let hasLateralStroke = verticalRatio <= 0.60
        let isCandidate = hasShortHandTravel && hasCompactBody && hasControlledPath
            && hasStrokeReversal && hasLateralStroke

        var confidence = 0.0
        confidence += hasShortHandTravel ? 0.28 : 0
        confidence += hasCompactBody ? 0.24 : 0
        confidence += hasControlledPath ? 0.20 : 0
        confidence += hasStrokeReversal ? 0.28 : 0
        confidence += hasLateralStroke ? 0.10 : 0

        let features = GolfPutterWindowFeatures(
            sampleCount: samples.count,
            handRangeX: handRangeX,
            handRangeY: handRangeY,
            handPath: path,
            coreRange: coreRange,
            peakHandSpeed: peakSpeed,
            reversalCount: reversals,
            verticalRatio: verticalRatio
        )
        let strokeTime = isCandidate ? samples[samples.count / 2].time : nil
        return GolfPutterDetection(
            isCandidate: isCandidate,
            confidence: confidence,
            strokeTime: strokeTime,
            features: features
        )
    }
}
