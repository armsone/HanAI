import Foundation

/// Reason why a candidate or selection result was categorized or rejected.
public enum ImageDiversityRejectionReason: String, Equatable, Sendable {
    case duplicateCandidateId
    case invalidIndex
    case nonFiniteOrOutOfRangeSharpness
    case nonFiniteOrOutOfRangeExposure
    case nonPositivePixelCount
    case nonFiniteOrOutOfRangeAesthetics
}

public enum ImageSelectionRole: String, Equatable, Sendable {
    case groupRepresentative
    case extraGroupMember
}

/// Explicit near-duplicate group details.
public struct ImageDiversityGroup: Equatable, Sendable {
    public let representativeIndex: Int
    public let memberIndices: [Int]

    public init(representativeIndex: Int, memberIndices: [Int]) {
        self.representativeIndex = representativeIndex
        self.memberIndices = memberIndices
    }
}

/// Deterministic diversity selection result.
public struct ImageDiversityResult: Equatable, Sendable {
    /// Selected indices sorted by original caller ordinal index ascending.
    public let selectedIndices: [Int]
    /// Quality ranking of eligible unique candidates (score descending, then index ascending).
    public let qualityRankedIndices: [Int]
    /// Selection preference order (round-robin across groups in quality order).
    public let selectionOrderIndices: [Int]
    /// Calculated quality score by candidate index for eligible unique candidates.
    public let scoreByIndex: [Int: Double]
    /// Explict near-duplicate groups formed via complete-link condition.
    public let groups: [ImageDiversityGroup]
    /// Selection role assigned to each selected candidate index.
    public let selectionRoleByIndex: [Int: ImageSelectionRole]
    /// Numeric/enum rejection reasons for ineligible candidates.
    public let rejectionReasonsByIndex: [Int: ImageDiversityRejectionReason]

    public init(
        selectedIndices: [Int],
        qualityRankedIndices: [Int],
        selectionOrderIndices: [Int],
        scoreByIndex: [Int: Double],
        groups: [ImageDiversityGroup],
        selectionRoleByIndex: [Int: ImageSelectionRole],
        rejectionReasonsByIndex: [Int: ImageDiversityRejectionReason]
    ) {
        self.selectedIndices = selectedIndices
        self.qualityRankedIndices = qualityRankedIndices
        self.selectionOrderIndices = selectionOrderIndices
        self.scoreByIndex = scoreByIndex
        self.groups = groups
        self.selectionRoleByIndex = selectionRoleByIndex
        self.rejectionReasonsByIndex = rejectionReasonsByIndex
    }
}

/// Quality-aware near-duplicate grouping and fixed-count diversity selector.
/// Caller supplies a mandatory calibrated finite nonnegative near-duplicate distance threshold (no universal default).
/// This groups near-duplicates, NOT merely the same subject/topic.
///
/// Missing aesthetics policy:
/// Apple Vision raw aesthetics overallScore [-1,1] must be normalized by adapter as (raw+1)/2 before this API.
/// Aesthetics blend (score = base * 0.4 + aesthetics * 0.6) is applied ONLY if EVERY eligible unique candidate
/// supplies a valid finite 0...1 aesthetics score. If any eligible unique candidate has nil aesthetics,
/// all eligible candidates uniformly use the base quality score formula (sharpness * 0.4 + exposure * 0.2 + log(pixelCount)/100).
/// Supplied invalid (non-nil and non-finite or outside 0...1) aesthetics excludes the candidate with .nonFiniteOrOutOfRangeAesthetics.
public enum ImageDiversitySelector {

    public static func selectDiverse(
        candidates: [ImageQualityCandidate],
        distances: [ImagePairDistance],
        threshold: Double,
        n: Int
    ) -> ImageDiversityResult {
        // Step 1: Detect duplicate candidate IDs across the whole input.
        // For duplicate candidate IDs, exclude ALL records for that ID with duplicateCandidateId reason.
        var idCounts: [Int: Int] = [:]
        for c in candidates {
            idCounts[c.index, default: 0] += 1
        }
        let duplicateIds = Set(idCounts.compactMap { $0.value > 1 ? $0.key : nil })

        var rejectionReasons: [Int: ImageDiversityRejectionReason] = [:]
        var eligibleCandidates: [ImageQualityCandidate] = []

        for c in candidates {
            if duplicateIds.contains(c.index) {
                rejectionReasons[c.index] = .duplicateCandidateId
                continue
            }
            if c.index < 0 {
                rejectionReasons[c.index] = .invalidIndex
                continue
            }
            if !c.sharpness.isFinite || c.sharpness < 0.0 || c.sharpness > 1.0 {
                rejectionReasons[c.index] = .nonFiniteOrOutOfRangeSharpness
                continue
            }
            if !c.exposureScore.isFinite || c.exposureScore < 0.0 || c.exposureScore > 1.0 {
                rejectionReasons[c.index] = .nonFiniteOrOutOfRangeExposure
                continue
            }
            if c.pixelCount <= 0 {
                rejectionReasons[c.index] = .nonPositivePixelCount
                continue
            }
            if let aes = c.aestheticsScore {
                if !aes.isFinite || aes < 0.0 || aes > 1.0 {
                    rejectionReasons[c.index] = .nonFiniteOrOutOfRangeAesthetics
                    continue
                }
            }
            eligibleCandidates.append(c)
        }

        // Step 2: Calculate scores using missing-aesthetics policy.
        let allHaveAesthetics = !eligibleCandidates.isEmpty && eligibleCandidates.allSatisfy { $0.aestheticsScore != nil }

        var scoreByIndex: [Int: Double] = [:]
        for c in eligibleCandidates {
            let resBonus = log(Double(c.pixelCount)) / 100.0
            let base = c.sharpness * 0.4 + c.exposureScore * 0.2 + resBonus
            if allHaveAesthetics, let aes = c.aestheticsScore {
                scoreByIndex[c.index] = base * 0.4 + aes * 0.6
            } else {
                scoreByIndex[c.index] = base
            }
        }

        // Step 3: Quality ranking (score descending, then index ascending).
        let qualityRanked = eligibleCandidates.map(\.index).sorted { a, b in
            let scoreA = scoreByIndex[a] ?? 0.0
            let scoreB = scoreByIndex[b] ?? 0.0
            if scoreA != scoreB {
                return scoreA > scoreB
            }
            return a < b
        }

        // Step 4: Sanitize distance map among eligible candidate IDs.
        // Self-pairs and unknown-ID pairs ignored.
        // Invalid/nonfinite/negative pair records are ignored.
        // Conflicting duplicate pair records conservatively use the MAXIMUM valid distance.
        let eligibleIdSet = Set(eligibleCandidates.map(\.index))
        var distanceMap: [PairKey: Double] = [:]

        for pair in distances {
            let u = pair.firstIndex
            let v = pair.secondIndex
            guard u != v else { continue }
            guard eligibleIdSet.contains(u) && eligibleIdSet.contains(v) else { continue }
            guard pair.distance.isFinite && pair.distance >= 0.0 else { continue }

            let key = PairKey(u, v)
            if let existing = distanceMap[key] {
                distanceMap[key] = max(existing, pair.distance)
            } else {
                distanceMap[key] = pair.distance
            }
        }

        // Step 5: Near-duplicate grouping via complete-link condition in quality order.
        // Threshold check: caller supplies a mandatory calibrated finite nonnegative threshold.
        // If threshold is negative or not finite, no near-duplicate grouping is allowed (each candidate in own group).
        let isThresholdValid = threshold.isFinite && threshold >= 0.0

        var groups: [GroupBuilder] = []
        for candIndex in qualityRanked {
            var placed = false
            if isThresholdValid {
                for g in groups {
                    // Complete-link condition: candidate can join a group ONLY when a known
                    // finite nonnegative distance <= threshold exists to EVERY current member.
                    var canJoin = true
                    for member in g.members {
                        guard let dist = distanceMap[PairKey(candIndex, member)], dist <= threshold else {
                            canJoin = false
                            break
                        }
                    }
                    if canJoin {
                        g.members.append(candIndex)
                        placed = true
                        break
                    }
                }
            }
            if !placed {
                groups.append(GroupBuilder(representative: candIndex, members: [candIndex]))
            }
        }

        let outputGroups = groups.map {
            ImageDiversityGroup(representativeIndex: $0.representative, memberIndices: $0.members)
        }

        // Step 6: Selection round-robin across groups.
        // Select best member (representative) of each group in quality order before selecting any second member;
        // then second round across groups before third, etc.
        var selectionPreference: [Int] = []
        var maxMembers = 0
        for g in groups {
            if g.members.count > maxMembers {
                maxMembers = g.members.count
            }
        }

        for round in 0..<maxMembers {
            for g in groups {
                if round < g.members.count {
                    selectionPreference.append(g.members[round])
                }
            }
        }

        // Target count: exactly min(max(0, n), eligible unique IDs count).
        let k = min(max(0, n), eligibleCandidates.count)
        let chosenIds = Array(selectionPreference.prefix(k))

        let selectedIndices = chosenIds.sorted() // sorted index ascending (original ordinal)

        // Assign selection roles.
        var selectionRoleByIndex: [Int: ImageSelectionRole] = [:]
        let repSet = Set(groups.map(\.representative))
        for id in chosenIds {
            if repSet.contains(id) {
                selectionRoleByIndex[id] = .groupRepresentative
            } else {
                selectionRoleByIndex[id] = .extraGroupMember
            }
        }

        return ImageDiversityResult(
            selectedIndices: selectedIndices,
            qualityRankedIndices: qualityRanked,
            selectionOrderIndices: selectionPreference,
            scoreByIndex: scoreByIndex,
            groups: outputGroups,
            selectionRoleByIndex: selectionRoleByIndex,
            rejectionReasonsByIndex: rejectionReasons
        )
    }

    private final class GroupBuilder {
        let representative: Int
        var members: [Int]

        init(representative: Int, members: [Int]) {
            self.representative = representative
            self.members = members
        }
    }

    private struct PairKey: Hashable {
        let low: Int
        let high: Int

        init(_ first: Int, _ second: Int) {
            self.low = min(first, second)
            self.high = max(first, second)
        }
    }
}
