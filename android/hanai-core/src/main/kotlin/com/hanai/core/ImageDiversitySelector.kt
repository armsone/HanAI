package com.hanai.core

import kotlin.math.ln

/**
 * Platform adapter supplied quality metrics.
 * Replicates ImageQualityCandidate in Swift.
 */
data class ImageQualityCandidate(
    val index: Int,
    val sharpness: Double,
    val exposureScore: Double,
    val pixelCount: Int,
    val aestheticsScore: Double? = null
)

/**
 * Numeric/enum rejection reasons for ineligible candidates.
 */
enum class ImageDiversityRejectionReason {
    DUPLICATE_CANDIDATE_ID,
    INVALID_INDEX,
    NON_FINITE_OR_OUT_OF_RANGE_SHARPNESS,
    NON_FINITE_OR_OUT_OF_RANGE_EXPOSURE,
    NON_POSITIVE_PIXEL_COUNT,
    NON_FINITE_OR_OUT_OF_RANGE_AESTHETICS
}

/**
 * Role assigned to selected images in diversity selection.
 */
enum class ImageSelectionRole {
    GROUP_REPRESENTATIVE,
    EXTRA_GROUP_MEMBER
}

/**
 * Explicit near-duplicate group representation.
 */
data class ImageDiversityGroup(
    val representativeIndex: Int,
    val memberIndices: List<Int>
)

/**
 * Deterministic diversity selection result.
 */
data class ImageDiversityResult(
    val selectedIndices: List<Int>,
    val qualityRankedIndices: List<Int>,
    val selectionOrderIndices: List<Int>,
    val scoreByIndex: Map<Int, Double>,
    val groups: List<ImageDiversityGroup>,
    val selectionRoleByIndex: Map<Int, ImageSelectionRole>,
    val rejectionReasonsByIndex: Map<Int, ImageDiversityRejectionReason>
)

/**
 * Quality-aware near-duplicate grouping and fixed-count diversity selector.
 * Caller supplies a mandatory calibrated finite nonnegative near-duplicate distance threshold (no universal default).
 * This groups near duplicates, NOT merely the same subject/topic.
 *
 * Missing aesthetics policy:
 * Apple Vision raw aesthetics overallScore [-1,1] must be normalized by adapter as (raw+1)/2 before this API.
 * Aesthetics blend (score = base * 0.4 + aesthetics * 0.6) is applied ONLY if EVERY eligible unique candidate
 * supplies a valid finite 0.0..1.0 aesthetics score. If any eligible unique candidate has null aesthetics,
 * all eligible candidates uniformly use the base quality score formula (sharpness * 0.4 + exposure * 0.2 + log(pixelCount)/100).
 * Supplied invalid (non-null and non-finite or outside 0.0..1.0) aesthetics excludes the candidate with NON_FINITE_OR_OUT_OF_RANGE_AESTHETICS.
 */
object ImageDiversitySelector {

    fun selectDiverse(
        candidates: List<ImageQualityCandidate>,
        distances: List<ImagePairDistance>,
        threshold: Double,
        n: Int
    ): ImageDiversityResult {
        // Step 1: Detect duplicate candidate IDs.
        // Exclude ALL records for that ID with DUPLICATE_CANDIDATE_ID reason.
        val idCounts = mutableMapOf<Int, Int>()
        for (c in candidates) {
            idCounts[c.index] = (idCounts[c.index] ?: 0) + 1
        }
        val duplicateIds = idCounts.filterValues { it > 1 }.keys

        val rejectionReasons = mutableMapOf<Int, ImageDiversityRejectionReason>()
        val eligibleCandidates = mutableListOf<ImageQualityCandidate>()

        for (c in candidates) {
            if (c.index in duplicateIds) {
                rejectionReasons[c.index] = ImageDiversityRejectionReason.DUPLICATE_CANDIDATE_ID
                continue
            }
            if (c.index < 0) {
                rejectionReasons[c.index] = ImageDiversityRejectionReason.INVALID_INDEX
                continue
            }
            if (!c.sharpness.isFinite() || c.sharpness < 0.0 || c.sharpness > 1.0) {
                rejectionReasons[c.index] = ImageDiversityRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_SHARPNESS
                continue
            }
            if (!c.exposureScore.isFinite() || c.exposureScore < 0.0 || c.exposureScore > 1.0) {
                rejectionReasons[c.index] = ImageDiversityRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_EXPOSURE
                continue
            }
            if (c.pixelCount <= 0) {
                rejectionReasons[c.index] = ImageDiversityRejectionReason.NON_POSITIVE_PIXEL_COUNT
                continue
            }
            val aes = c.aestheticsScore
            if (aes != null) {
                if (!aes.isFinite() || aes < 0.0 || aes > 1.0) {
                    rejectionReasons[c.index] = ImageDiversityRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_AESTHETICS
                    continue
                }
            }
            eligibleCandidates.add(c)
        }

        // Step 2: Calculate scores using missing-aesthetics policy.
        val allHaveAesthetics = eligibleCandidates.isNotEmpty() && eligibleCandidates.all { it.aestheticsScore != null }

        val scoreByIndex = mutableMapOf<Int, Double>()
        for (c in eligibleCandidates) {
            val resBonus = ln(c.pixelCount.toDouble()) / 100.0
            val base = c.sharpness * 0.4 + c.exposureScore * 0.2 + resBonus
            if (allHaveAesthetics && c.aestheticsScore != null) {
                scoreByIndex[c.index] = base * 0.4 + c.aestheticsScore * 0.6
            } else {
                scoreByIndex[c.index] = base
            }
        }

        // Step 3: Quality ranking (score descending, then index ascending).
        val qualityRanked = eligibleCandidates.map { it.index }.sortedWith(
            Comparator { a, b ->
                val scoreA = scoreByIndex[a] ?: 0.0
                val scoreB = scoreByIndex[b] ?: 0.0
                if (scoreA != scoreB) {
                    scoreB.compareTo(scoreA)
                } else {
                    a.compareTo(b)
                }
            }
        )

        // Step 4: Sanitize distance map among eligible candidate IDs.
        // Self-pairs and unknown-ID pairs ignored.
        // Invalid/nonfinite/negative pair records are ignored.
        // Conflicting duplicate pair records conservatively use the MAXIMUM valid distance.
        val eligibleIdSet = eligibleCandidates.map { it.index }.toSet()
        val distanceMap = mutableMapOf<PairKey, Double>()

        for (pair in distances) {
            val u = pair.firstIndex
            val v = pair.secondIndex
            if (u == v) continue
            if (u !in eligibleIdSet || v !in eligibleIdSet) continue
            if (!pair.distance.isFinite() || pair.distance < 0.0) continue

            val key = PairKey.of(u, v)
            val existing = distanceMap[key]
            if (existing != null) {
                distanceMap[key] = maxOf(existing, pair.distance)
            } else {
                distanceMap[key] = pair.distance
            }
        }

        // Step 5: Near-duplicate grouping via complete-link condition in quality order.
        val isThresholdValid = threshold.isFinite() && threshold >= 0.0
        val groups = mutableListOf<GroupBuilder>()

        for (candIndex in qualityRanked) {
            var placed = false
            if (isThresholdValid) {
                for (g in groups) {
                    var canJoin = true
                    for (member in g.members) {
                        val dist = distanceMap[PairKey.of(candIndex, member)]
                        if (dist == null || dist > threshold) {
                            canJoin = false
                            break
                        }
                    }
                    if (canJoin) {
                        g.members.add(candIndex)
                        placed = true
                        break
                    }
                }
            }
            if (!placed) {
                groups.add(GroupBuilder(representative = candIndex, members = mutableListOf(candIndex)))
            }
        }

        val outputGroups = groups.map {
            ImageDiversityGroup(representativeIndex = it.representative, memberIndices = it.members.toList())
        }

        // Step 6: Selection round-robin across groups.
        val selectionPreference = mutableListOf<Int>()
        var maxMembers = 0
        for (g in groups) {
            if (g.members.size > maxMembers) {
                maxMembers = g.members.size
            }
        }

        for (round in 0 until maxMembers) {
            for (g in groups) {
                if (round < g.members.size) {
                    selectionPreference.add(g.members[round])
                }
            }
        }

        // Target count: exactly min(max(0, n), eligible unique IDs count).
        val k = minOf(maxOf(0, n), eligibleCandidates.size)
        val chosenIds = selectionPreference.take(k)

        val selectedIndices = chosenIds.sorted() // sorted index ascending (original ordinal)

        // Assign selection roles.
        val selectionRoleByIndex = mutableMapOf<Int, ImageSelectionRole>()
        val repSet = groups.map { it.representative }.toSet()
        for (id in chosenIds) {
            if (id in repSet) {
                selectionRoleByIndex[id] = ImageSelectionRole.GROUP_REPRESENTATIVE
            } else {
                selectionRoleByIndex[id] = ImageSelectionRole.EXTRA_GROUP_MEMBER
            }
        }

        return ImageDiversityResult(
            selectedIndices = selectedIndices,
            qualityRankedIndices = qualityRanked,
            selectionOrderIndices = selectionPreference,
            scoreByIndex = scoreByIndex,
            groups = outputGroups,
            selectionRoleByIndex = selectionRoleByIndex,
            rejectionReasonsByIndex = rejectionReasons
        )
    }

    private class GroupBuilder(
        val representative: Int,
        val members: MutableList<Int>
    )

    private data class PairKey(val low: Int, val high: Int) {
        companion object {
            fun of(first: Int, second: Int): PairKey =
                PairKey(minOf(first, second), maxOf(first, second))
        }
    }
}
