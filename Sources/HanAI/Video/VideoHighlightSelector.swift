import Foundation

/// Reason why a video frame candidate was rejected from final selection.
public enum VideoFrameRejectionReason: String, Equatable, Sendable {
    case duplicateCandidateId
    case invalidIndex
    case nonFiniteOrOutOfRangeTime
    case nonFiniteOrOutOfRangeQuality
    case nonFiniteOrOutOfRangeImportance
    case belowQualityThreshold
    case minimumSpacingConflict
    case localNearDuplicateConflict
    case requestedCountReached
}

/// Errors detected in configuration or video inputs.
public enum VideoHighlightConfigurationError: String, Equatable, Sendable {
    case invalidDuration
    case invalidSimilarityThreshold
    case invalidMinimumQuality
    case invalidMinimumSpacingSeconds
    case invalidLocalDiversityWindowSeconds
    case invalidPreRollSeconds
    case invalidPostRollSeconds
    case invalidMaxSegmentDurationSeconds
    case invalidTotalDurationBudgetSeconds
    case invalidFrameCount
    case invalidImportanceWeight
}

/// Anonymous numeric metrics for a candidate video frame.
public struct VideoFrameCandidate: Equatable, Sendable {
    public let index: Int
    public let timeSeconds: Double
    public let qualityScore: Double
    public let importanceScore: Double?

    public init(
        index: Int,
        timeSeconds: Double,
        qualityScore: Double,
        importanceScore: Double? = nil
    ) {
        self.index = index
        self.timeSeconds = timeSeconds
        self.qualityScore = qualityScore
        self.importanceScore = importanceScore
    }
}

/// Deterministic configuration parameters for highlight frame and segment selection.
public struct VideoHighlightConfiguration: Equatable, Sendable {
    public let frameCount: Int
    public let similarityThreshold: Double
    public let minimumQuality: Double
    public let minimumSpacingSeconds: Double
    public let localDiversityWindowSeconds: Double
    public let preRollSeconds: Double
    public let postRollSeconds: Double
    public let maxSegmentDurationSeconds: Double
    public let totalDurationBudgetSeconds: Double
    public let importanceWeight: Double

    public init(
        frameCount: Int,
        similarityThreshold: Double,
        minimumQuality: Double = 0.25,
        minimumSpacingSeconds: Double = 1,
        localDiversityWindowSeconds: Double = 30,
        preRollSeconds: Double = 1,
        postRollSeconds: Double = 2,
        maxSegmentDurationSeconds: Double = 6,
        totalDurationBudgetSeconds: Double = 30,
        importanceWeight: Double = 0.3
    ) {
        self.frameCount = frameCount
        self.similarityThreshold = similarityThreshold
        self.minimumQuality = minimumQuality
        self.minimumSpacingSeconds = minimumSpacingSeconds
        self.localDiversityWindowSeconds = localDiversityWindowSeconds
        self.preRollSeconds = preRollSeconds
        self.postRollSeconds = postRollSeconds
        self.maxSegmentDurationSeconds = maxSegmentDurationSeconds
        self.totalDurationBudgetSeconds = totalDurationBudgetSeconds
        self.importanceWeight = importanceWeight
    }
}

/// Exported video highlight segment around one or more anchor frames.
/// Represents a best-frame +/- context anchor, NOT an event-completeness claim.
public struct VideoHighlightSegment: Equatable, Sendable {
    public let startSeconds: Double
    public let endSeconds: Double
    public let anchorIndices: [Int]

    public init(
        startSeconds: Double,
        endSeconds: Double,
        anchorIndices: [Int]
    ) {
        self.startSeconds = startSeconds
        self.endSeconds = endSeconds
        self.anchorIndices = anchorIndices
    }
}

/// Complete highlight selection result.
public struct VideoHighlightResult: Equatable, Sendable {
    /// Selected anchor indices sorted chronologically by time ascending, then index ascending.
    public let selectedFrameIndices: [Int]
    /// Greedy preference selection order of anchor indices.
    public let selectionOrderIndices: [Int]
    /// Exported non-overlapping highlight segments sorted chronologically.
    public let segments: [VideoHighlightSegment]
    /// Effective calculated score for eligible candidates.
    public let scoreByIndex: [Int: Double]
    /// Explicit rejection reason for rejected or unselected candidates.
    public let rejectionReasonsByIndex: [Int: VideoFrameRejectionReason]
    /// Selected anchor frame indices whose highlight segment was omitted due to budget exhaustion.
    public let omittedSegmentFrameIndices: [Int]
    /// Configuration validation errors; if non-empty, selection and segments are empty.
    public let configurationErrors: [VideoHighlightConfigurationError]
    /// Whether importance scores were used across all eligible candidates.
    /// Missing any eligible importance uses quality-only uniformly, setting this to false.
    public let usedImportanceScores: Bool

    public init(
        selectedFrameIndices: [Int],
        selectionOrderIndices: [Int],
        segments: [VideoHighlightSegment],
        scoreByIndex: [Int: Double],
        rejectionReasonsByIndex: [Int: VideoFrameRejectionReason],
        omittedSegmentFrameIndices: [Int],
        configurationErrors: [VideoHighlightConfigurationError],
        usedImportanceScores: Bool = false
    ) {
        self.selectedFrameIndices = selectedFrameIndices
        self.selectionOrderIndices = selectionOrderIndices
        self.segments = segments
        self.scoreByIndex = scoreByIndex
        self.rejectionReasonsByIndex = rejectionReasonsByIndex
        self.omittedSegmentFrameIndices = omittedSegmentFrameIndices
        self.configurationErrors = configurationErrors
        self.usedImportanceScores = usedImportanceScores
    }
}

private struct PairKey: Hashable {
    let lower: Int
    let higher: Int

    init(_ a: Int, _ b: Int) {
        if a <= b {
            self.lower = a
            self.higher = b
        } else {
            self.lower = b
            self.higher = a
        }
    }
}

/// Deterministic, framework-free video highlight selector.
public enum VideoHighlightSelector {

    public static func select(
        candidates: [VideoFrameCandidate],
        distances: [ImagePairDistance],
        durationSeconds: Double,
        configuration: VideoHighlightConfiguration,
        shotBoundarySeconds: [Double] = []
    ) -> VideoHighlightResult {
        // Step 1: Validate configuration and duration.
        var configErrors: [VideoHighlightConfigurationError] = []

        if !durationSeconds.isFinite || durationSeconds <= 0 {
            configErrors.append(.invalidDuration)
        }
        if !configuration.similarityThreshold.isFinite || configuration.similarityThreshold < 0 {
            configErrors.append(.invalidSimilarityThreshold)
        }
        if !configuration.minimumQuality.isFinite || configuration.minimumQuality < 0 || configuration.minimumQuality > 1 {
            configErrors.append(.invalidMinimumQuality)
        }
        if !configuration.minimumSpacingSeconds.isFinite || configuration.minimumSpacingSeconds < 0 {
            configErrors.append(.invalidMinimumSpacingSeconds)
        }
        if !configuration.localDiversityWindowSeconds.isFinite || configuration.localDiversityWindowSeconds < 0 {
            configErrors.append(.invalidLocalDiversityWindowSeconds)
        }
        if !configuration.preRollSeconds.isFinite || configuration.preRollSeconds < 0 {
            configErrors.append(.invalidPreRollSeconds)
        }
        if !configuration.postRollSeconds.isFinite || configuration.postRollSeconds < 0 {
            configErrors.append(.invalidPostRollSeconds)
        }
        if !configuration.maxSegmentDurationSeconds.isFinite || configuration.maxSegmentDurationSeconds <= 0 {
            configErrors.append(.invalidMaxSegmentDurationSeconds)
        }
        if !configuration.totalDurationBudgetSeconds.isFinite || configuration.totalDurationBudgetSeconds < 0 {
            configErrors.append(.invalidTotalDurationBudgetSeconds)
        }
        if configuration.frameCount < 0 {
            configErrors.append(.invalidFrameCount)
        }
        if !configuration.importanceWeight.isFinite || configuration.importanceWeight < 0 || configuration.importanceWeight > 1 {
            configErrors.append(.invalidImportanceWeight)
        }

        if !configErrors.isEmpty {
            return VideoHighlightResult(
                selectedFrameIndices: [],
                selectionOrderIndices: [],
                segments: [],
                scoreByIndex: [:],
                rejectionReasonsByIndex: [:],
                omittedSegmentFrameIndices: [],
                configurationErrors: configErrors,
                usedImportanceScores: false
            )
        }

        // Step 2: Validate candidates and detect repeated IDs.
        var idCounts: [Int: Int] = [:]
        for c in candidates {
            idCounts[c.index, default: 0] += 1
        }
        let repeatedIds = Set(idCounts.compactMap { $0.value > 1 ? $0.key : nil })

        var rejectionReasons: [Int: VideoFrameRejectionReason] = [:]
        var validCandidates: [VideoFrameCandidate] = []

        for c in candidates {
            if repeatedIds.contains(c.index) {
                rejectionReasons[c.index] = .duplicateCandidateId
                continue
            }
            if c.index < 0 {
                rejectionReasons[c.index] = .invalidIndex
                continue
            }
            if !c.timeSeconds.isFinite || c.timeSeconds < 0 || c.timeSeconds > durationSeconds {
                rejectionReasons[c.index] = .nonFiniteOrOutOfRangeTime
                continue
            }
            if !c.qualityScore.isFinite || c.qualityScore < 0 || c.qualityScore > 1 {
                rejectionReasons[c.index] = .nonFiniteOrOutOfRangeQuality
                continue
            }
            if let imp = c.importanceScore {
                if !imp.isFinite || imp < 0 || imp > 1 {
                    rejectionReasons[c.index] = .nonFiniteOrOutOfRangeImportance
                    continue
                }
            }
            validCandidates.append(c)
        }

        // Step 3: Gate minimumQuality BEFORE any novelty / diversity.
        var eligibleCandidates: [VideoFrameCandidate] = []
        for c in validCandidates {
            if c.qualityScore < configuration.minimumQuality {
                rejectionReasons[c.index] = .belowQualityThreshold
            } else {
                eligibleCandidates.append(c)
            }
        }

        // Step 4: Missing importance scoring policy.
        // If not all eligible candidates have importance, score quality alone for ALL.
        // Otherwise, (1.0 - configuration.importanceWeight) * quality + configuration.importanceWeight * importance.
        let allHaveImportance = !eligibleCandidates.isEmpty && eligibleCandidates.allSatisfy { $0.importanceScore != nil }

        var scoreByIndex: [Int: Double] = [:]
        for c in eligibleCandidates {
            if allHaveImportance, let imp = c.importanceScore {
                scoreByIndex[c.index] = (1.0 - configuration.importanceWeight) * c.qualityScore + configuration.importanceWeight * imp
            } else {
                scoreByIndex[c.index] = c.qualityScore
            }
        }

        // Step 5: Distance matrix processing.
        // Distances invalid/unknown/self ignored; multiple pair values conservatively max finite nonnegative.
        let eligibleIndexSet = Set(eligibleCandidates.map(\.index))
        var distanceMap: [PairKey: Double] = [:]
        for d in distances {
            if d.firstIndex == d.secondIndex {
                continue
            }
            guard eligibleIndexSet.contains(d.firstIndex) && eligibleIndexSet.contains(d.secondIndex) else {
                continue
            }
            guard d.distance.isFinite && d.distance >= 0 else {
                continue
            }
            let key = PairKey(d.firstIndex, d.secondIndex)
            if let existing = distanceMap[key] {
                distanceMap[key] = max(existing, d.distance)
            } else {
                distanceMap[key] = d.distance
            }
        }

        // Step 6: Rank candidates.
        // Rank by score descending, then time ascending, index ascending.
        let candidateMap = Dictionary(uniqueKeysWithValues: eligibleCandidates.map { ($0.index, $0) })
        let rankedIndices = eligibleCandidates.map(\.index).sorted { a, b in
            let scoreA = scoreByIndex[a] ?? 0.0
            let scoreB = scoreByIndex[b] ?? 0.0
            if scoreA != scoreB {
                return scoreA > scoreB
            }
            let candA = candidateMap[a]!
            let candB = candidateMap[b]!
            if candA.timeSeconds != candB.timeSeconds {
                return candA.timeSeconds < candB.timeSeconds
            }
            return a < b
        }

        // Step 7: Greedy frame selection.
        var selectionOrderIndices: [Int] = []

        for candidateIndex in rankedIndices {
            if selectionOrderIndices.count >= configuration.frameCount {
                rejectionReasons[candidateIndex] = .requestedCountReached
                continue
            }

            let cand = candidateMap[candidateIndex]!
            var conflictReason: VideoFrameRejectionReason? = nil

            for acceptedIndex in selectionOrderIndices {
                let accepted = candidateMap[acceptedIndex]!
                let timeDelta = abs(cand.timeSeconds - accepted.timeSeconds)

                // Check minimum spacing conflict
                if timeDelta < configuration.minimumSpacingSeconds {
                    conflictReason = .minimumSpacingConflict
                    break
                }

                // Check local near duplicate conflict
                if timeDelta <= configuration.localDiversityWindowSeconds {
                    let key = PairKey(cand.index, accepted.index)
                    if let dist = distanceMap[key], dist <= configuration.similarityThreshold {
                        conflictReason = .localNearDuplicateConflict
                        break
                    }
                }
            }

            if let reason = conflictReason {
                rejectionReasons[candidateIndex] = reason
            } else {
                selectionOrderIndices.append(candidateIndex)
            }
        }

        // Selected frame indices sorted chronologically (time ascending, then index ascending).
        let selectedFrameIndices = selectionOrderIndices.sorted { a, b in
            let candA = candidateMap[a]!
            let candB = candidateMap[b]!
            if candA.timeSeconds != candB.timeSeconds {
                return candA.timeSeconds < candB.timeSeconds
            }
            return a < b
        }

        // Step 8: Segment generation & Budget allocation.
        // Shot boundaries: only valid finite interior cuts (0 < cut < durationSeconds) deduplicated and sorted.
        var validShots: [Double] = []
        for s in shotBoundarySeconds {
            if s.isFinite && s > 0 && s < durationSeconds {
                validShots.append(s)
            }
        }
        validShots = Array(Set(validShots)).sorted()

        // Helper to find shot interval [shotStart, shotEnd) for a given time (final shot ends at durationSeconds closed)
        func findShotInterval(for time: Double) -> (Double, Double) {
            var shotStart = 0.0
            var shotEnd = durationSeconds

            for cut in validShots {
                if cut <= time {
                    shotStart = max(shotStart, cut)
                }
                if cut > time {
                    shotEnd = min(shotEnd, cut)
                    break
                }
            }
            return (shotStart, shotEnd)
        }

        // Generate desired segment for each selected anchor in chronological order
        struct AnchorContext {
            let anchorIndex: Int
            let anchorTime: Double
            var start: Double
            var end: Double
        }

        var contexts: [AnchorContext] = []
        for idx in selectedFrameIndices {
            let cand = candidateMap[idx]!
            let t = cand.timeSeconds
            let (shotStart, shotEnd) = findShotInterval(for: t)

            var rawStart = max(0.0, t - configuration.preRollSeconds)
            var rawEnd = min(durationSeconds, t + configuration.postRollSeconds)

            // Clamp to shot boundary
            rawStart = max(rawStart, shotStart)
            rawEnd = min(rawEnd, shotEnd)

            // Ensure anchor is within interval
            rawStart = min(rawStart, t)
            rawEnd = max(rawEnd, t)

            // Clamp duration to maxSegmentDurationSeconds while preserving anchor
            if (rawEnd - rawStart) > configuration.maxSegmentDurationSeconds {
                let maxDur = configuration.maxSegmentDurationSeconds
                let leftAvailable = t - rawStart
                let rightAvailable = rawEnd - t
                let desiredLeft = min(leftAvailable, configuration.preRollSeconds)
                let desiredRight = min(rightAvailable, configuration.postRollSeconds)

                if desiredLeft + desiredRight <= maxDur {
                    rawStart = t - desiredLeft
                    rawEnd = t + desiredRight
                } else {
                    let totalDesired = desiredLeft + desiredRight
                    if totalDesired > 0 {
                        let allocLeft = min(desiredLeft, maxDur * (desiredLeft / totalDesired))
                        let allocRight = maxDur - allocLeft
                        rawStart = t - allocLeft
                        rawEnd = t + allocRight
                    } else {
                        rawStart = t
                        rawEnd = min(durationSeconds, t + maxDur)
                    }
                }
            }

            contexts.append(AnchorContext(anchorIndex: idx, anchorTime: t, start: rawStart, end: rawEnd))
        }

        // Partition contexts at midpoints between adjacent anchors to guarantee disjoint non-overlapping intervals
        if contexts.count > 1 {
            for i in 0..<(contexts.count - 1) {
                let mid = (contexts[i].anchorTime + contexts[i + 1].anchorTime) / 2.0
                if contexts[i].end > mid {
                    contexts[i].end = max(contexts[i].anchorTime, mid)
                }
                if contexts[i + 1].start < mid {
                    contexts[i + 1].start = min(contexts[i + 1].anchorTime, mid)
                }
            }
        }

        // Disjoint candidate segments from contexts
        struct TempSegment {
            var start: Double
            var end: Double
            var anchorIndices: [Int]
        }

        var unmergedSegments: [TempSegment] = []
        for ctx in contexts {
            let (shotStart, shotEnd) = findShotInterval(for: ctx.anchorTime)
            let clampedStart = max(ctx.start, shotStart)
            let clampedEnd = min(ctx.end, shotEnd)
            unmergedSegments.append(TempSegment(start: clampedStart, end: clampedEnd, anchorIndices: [ctx.anchorIndex]))
        }

        // Merge adjacent segments if they are in the same shot and combined duration <= maxSegmentDurationSeconds
        var mergedSegments: [TempSegment] = []
        for seg in unmergedSegments {
            if mergedSegments.isEmpty {
                mergedSegments.append(seg)
            } else {
                let lastIndex = mergedSegments.count - 1
                let lastSeg = mergedSegments[lastIndex]
                let canMerge = abs(lastSeg.end - seg.start) < 1e-9
                let combinedDuration = seg.end - lastSeg.start
                let sameShot = findShotInterval(for: lastSeg.start) == findShotInterval(for: seg.end)

                if canMerge && sameShot && combinedDuration <= configuration.maxSegmentDurationSeconds {
                    mergedSegments[lastIndex].end = seg.end
                    mergedSegments[lastIndex].anchorIndices.append(contentsOf: seg.anchorIndices)
                } else {
                    mergedSegments.append(seg)
                }
            }
        }

        // Allocate budget in preference (selection order) order
        // Map anchor to its merged segment index
        var anchorToMergedIndex: [Int: Int] = [:]
        for (i, seg) in mergedSegments.enumerated() {
            for anchor in seg.anchorIndices {
                anchorToMergedIndex[anchor] = i
            }
        }

        var activeMergedIndices = Set<Int>()

        var remainingBudget = configuration.totalDurationBudgetSeconds
        // If initial budget is > 0, allocate to segments
        if remainingBudget > 0 {
            for anchor in selectionOrderIndices {
                guard let segIdx = anchorToMergedIndex[anchor] else { continue }
                if activeMergedIndices.contains(segIdx) {
                    continue
                }
                let seg = mergedSegments[segIdx]
                let dur = seg.end - seg.start

                if dur <= remainingBudget {
                    activeMergedIndices.insert(segIdx)
                    remainingBudget -= dur
                } else if remainingBudget > 0 {
                    // Shrink segment to remainingBudget while preserving anchor(s)
                    var s = seg.start
                    var e = seg.end
                    let minAnchorTime = seg.anchorIndices.map { candidateMap[$0]!.timeSeconds }.min()!
                    let maxAnchorTime = seg.anchorIndices.map { candidateMap[$0]!.timeSeconds }.max()!

                    if (maxAnchorTime - minAnchorTime) <= remainingBudget {
                        // Fit both anchors
                        let needed = remainingBudget
                        let currentSpan = e - s
                        if currentSpan > needed {
                            let excess = currentSpan - needed
                            let leftCanShrink = minAnchorTime - s
                            let rightCanShrink = e - maxAnchorTime
                            let shrinkLeft = min(leftCanShrink, excess * (leftCanShrink / max(1e-9, leftCanShrink + rightCanShrink)))
                            let shrinkRight = excess - shrinkLeft
                            s += shrinkLeft
                            e -= shrinkRight
                        }
                        mergedSegments[segIdx].start = s
                        mergedSegments[segIdx].end = e
                        activeMergedIndices.insert(segIdx)
                        remainingBudget = 0.0
                    }
                }
            }
        }

        // Final segments filtered and sorted chronologically
        var finalSegments: [VideoHighlightSegment] = []
        for (i, seg) in mergedSegments.enumerated() {
            if activeMergedIndices.contains(i) && seg.end > seg.start {
                finalSegments.append(VideoHighlightSegment(
                    startSeconds: seg.start,
                    endSeconds: seg.end,
                    anchorIndices: seg.anchorIndices
                ))
            }
        }
        finalSegments.sort { $0.startSeconds < $1.startSeconds }

        // Derive omittedSegmentFrameIndices as selectionOrderIndices minus Set(all final segment anchorIndices)
        let exportedAnchorIndices = Set(finalSegments.flatMap(\.anchorIndices))
        let omittedFrameIndices = selectionOrderIndices.filter { !exportedAnchorIndices.contains($0) }

        return VideoHighlightResult(
            selectedFrameIndices: selectedFrameIndices,
            selectionOrderIndices: selectionOrderIndices,
            segments: finalSegments,
            scoreByIndex: scoreByIndex,
            rejectionReasonsByIndex: rejectionReasons,
            omittedSegmentFrameIndices: omittedFrameIndices,
            configurationErrors: [],
            usedImportanceScores: allHaveImportance
        )
    }
}
