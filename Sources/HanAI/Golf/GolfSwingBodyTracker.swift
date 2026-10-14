import Foundation

/// Keeps body continuity separate from hand-phase estimation. A quiet empty bay
/// is not an address, and a person just entering it is not yet swing evidence.
/// The adapter calls observe for valid target-selected poses and queries this
/// tracker at the current evaluation time, including when no pose was obtained.
public struct GolfSwingBodyTracker: Sendable {
    public static let observationWindow = 1.2
    public static let minimumObservationDuration = 0.55
    public static let maximumObservationGap = 0.6
    public static let maximumObservationAge = 0.35
    public static let maximumCoreTravel = 0.75

    private var samples: [GolfSwingPoseSample] = []

    public init() {}

    public mutating func reset() { samples.removeAll(keepingCapacity: true) }

    public mutating func observe(_ sample: GolfSwingPoseSample) {
        guard [sample.time, sample.handX, sample.handY, sample.coreX, sample.coreY,
               sample.bodyScale, sample.confidence].allSatisfy(\.isFinite),
              sample.confidence >= 0.45, sample.bodyScale >= 0.04 else { return }
        if let last = samples.last,
           sample.time <= last.time || sample.time - last.time > Self.maximumObservationGap {
            reset()
        }
        samples.removeAll { sample.time - $0.time > Self.observationWindow }
        samples.append(sample)
    }

    /// Explicit false can veto the screen-motion fallback in the fusion policy.
    /// This is body continuity, not a club label or proof of a completed shot.
    public func hasRecentTrackedBody(at time: Double) -> Bool {
        guard time.isFinite, let last = samples.last,
              time >= last.time, time - last.time <= Self.maximumObservationAge else { return false }
        let recent = samples.filter { time - $0.time <= Self.observationWindow }
        guard recent.count >= 3, let first = recent.first,
              last.time - first.time >= Self.minimumObservationDuration else { return false }
        // Pairwise travel also catches a walk out and back, unlike endpoint distance.
        for (index, sample) in recent.enumerated() {
            for other in recent.dropFirst(index + 1) {
                let travel = hypot(sample.coreX - other.coreX, sample.coreY - other.coreY)
                    / max(0.04, min(sample.bodyScale, other.bodyScale))
                if travel > Self.maximumCoreTravel { return false }
            }
        }
        return true
    }
}
