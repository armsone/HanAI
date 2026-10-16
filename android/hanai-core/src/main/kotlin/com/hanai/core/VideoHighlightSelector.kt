package com.hanai.core

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * Reason why a video frame candidate was rejected from final selection.
 */
enum class VideoFrameRejectionReason {
    DUPLICATE_CANDIDATE_ID,
    INVALID_INDEX,
    NON_FINITE_OR_OUT_OF_RANGE_TIME,
    NON_FINITE_OR_OUT_OF_RANGE_QUALITY,
    NON_FINITE_OR_OUT_OF_RANGE_IMPORTANCE,
    BELOW_QUALITY_THRESHOLD,
    MINIMUM_SPACING_CONFLICT,
    LOCAL_NEAR_DUPLICATE_CONFLICT,
    REQUESTED_COUNT_REACHED
}

/**
 * Errors detected in configuration or video inputs.
 */
enum class VideoHighlightConfigurationError {
    INVALID_DURATION,
    INVALID_SIMILARITY_THRESHOLD,
    INVALID_MINIMUM_QUALITY,
    INVALID_MINIMUM_SPACING_SECONDS,
    INVALID_LOCAL_DIVERSITY_WINDOW_SECONDS,
    INVALID_PRE_ROLL_SECONDS,
    INVALID_POST_ROLL_SECONDS,
    INVALID_MAX_SEGMENT_DURATION_SECONDS,
    INVALID_TOTAL_DURATION_BUDGET_SECONDS,
    INVALID_FRAME_COUNT,
    INVALID_IMPORTANCE_WEIGHT
}

/**
 * Anonymous numeric metrics for a candidate video frame.
 */
data class VideoFrameCandidate(
    val index: Int,
    val timeSeconds: Double,
    val qualityScore: Double,
    val importanceScore: Double? = null
)

/**
 * Deterministic configuration parameters for highlight frame and segment selection.
 */
data class VideoHighlightConfiguration(
    val frameCount: Int,
    val similarityThreshold: Double,
    val minimumQuality: Double = 0.25,
    val minimumSpacingSeconds: Double = 1.0,
    val localDiversityWindowSeconds: Double = 30.0,
    val preRollSeconds: Double = 1.0,
    val postRollSeconds: Double = 2.0,
    val maxSegmentDurationSeconds: Double = 6.0,
    val totalDurationBudgetSeconds: Double = 30.0,
    val importanceWeight: Double = 0.3
)

/**
 * Exported video highlight segment around one or more anchor frames.
 * Represents a best-frame +/- context anchor, NOT an event-completeness claim.
 */
data class VideoHighlightSegment(
    val startSeconds: Double,
    val endSeconds: Double,
    val anchorIndices: List<Int>
)

/**
 * Complete highlight selection result.
 */
data class VideoHighlightResult(
    val selectedFrameIndices: List<Int>,
    val selectionOrderIndices: List<Int>,
    val segments: List<VideoHighlightSegment>,
    val scoreByIndex: Map<Int, Double>,
    val rejectionReasonsByIndex: Map<Int, VideoFrameRejectionReason>,
    val omittedSegmentFrameIndices: List<Int>,
    val configurationErrors: List<VideoHighlightConfigurationError>,
    val usedImportanceScores: Boolean = false
)

/**
 * Deterministic, framework-free video highlight selector.
 */
object VideoHighlightSelector {

    private data class PairKey(val lower: Int, val higher: Int) {
        companion object {
            fun of(a: Int, b: Int): PairKey =
                if (a <= b) PairKey(a, b) else PairKey(b, a)
        }
    }

    fun select(
        candidates: List<VideoFrameCandidate>,
        distances: List<ImagePairDistance>,
        durationSeconds: Double,
        configuration: VideoHighlightConfiguration,
        shotBoundarySeconds: List<Double> = emptyList()
    ): VideoHighlightResult {
        // Step 1: Validate configuration and duration.
        val configErrors = mutableListOf<VideoHighlightConfigurationError>()

        if (!durationSeconds.isFinite() || durationSeconds <= 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_DURATION)
        }
        if (!configuration.similarityThreshold.isFinite() || configuration.similarityThreshold < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_SIMILARITY_THRESHOLD)
        }
        if (!configuration.minimumQuality.isFinite() || configuration.minimumQuality < 0.0 || configuration.minimumQuality > 1.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_MINIMUM_QUALITY)
        }
        if (!configuration.minimumSpacingSeconds.isFinite() || configuration.minimumSpacingSeconds < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_MINIMUM_SPACING_SECONDS)
        }
        if (!configuration.localDiversityWindowSeconds.isFinite() || configuration.localDiversityWindowSeconds < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_LOCAL_DIVERSITY_WINDOW_SECONDS)
        }
        if (!configuration.preRollSeconds.isFinite() || configuration.preRollSeconds < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_PRE_ROLL_SECONDS)
        }
        if (!configuration.postRollSeconds.isFinite() || configuration.postRollSeconds < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_POST_ROLL_SECONDS)
        }
        if (!configuration.maxSegmentDurationSeconds.isFinite() || configuration.maxSegmentDurationSeconds <= 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_MAX_SEGMENT_DURATION_SECONDS)
        }
        if (!configuration.totalDurationBudgetSeconds.isFinite() || configuration.totalDurationBudgetSeconds < 0.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_TOTAL_DURATION_BUDGET_SECONDS)
        }
        if (configuration.frameCount < 0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_FRAME_COUNT)
        }
        if (!configuration.importanceWeight.isFinite() || configuration.importanceWeight < 0.0 || configuration.importanceWeight > 1.0) {
            configErrors.add(VideoHighlightConfigurationError.INVALID_IMPORTANCE_WEIGHT)
        }

        if (configErrors.isNotEmpty()) {
            return VideoHighlightResult(
                selectedFrameIndices = emptyList(),
                selectionOrderIndices = emptyList(),
                segments = emptyList(),
                scoreByIndex = emptyMap(),
                rejectionReasonsByIndex = emptyMap(),
                omittedSegmentFrameIndices = emptyList(),
                configurationErrors = configErrors,
                usedImportanceScores = false
            )
        }

        // Step 2: Validate candidates and detect repeated IDs.
        val idCounts = mutableMapOf<Int, Int>()
        for (c in candidates) {
            idCounts[c.index] = (idCounts[c.index] ?: 0) + 1
        }
        val repeatedIds = idCounts.filter { it.value > 1 }.keys

        val rejectionReasons = mutableMapOf<Int, VideoFrameRejectionReason>()
        val validCandidates = mutableListOf<VideoFrameCandidate>()

        for (c in candidates) {
            if (c.index in repeatedIds) {
                rejectionReasons[c.index] = VideoFrameRejectionReason.DUPLICATE_CANDIDATE_ID
                continue
            }
            if (c.index < 0) {
                rejectionReasons[c.index] = VideoFrameRejectionReason.INVALID_INDEX
                continue
            }
            if (!c.timeSeconds.isFinite() || c.timeSeconds < 0.0 || c.timeSeconds > durationSeconds) {
                rejectionReasons[c.index] = VideoFrameRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_TIME
                continue
            }
            if (!c.qualityScore.isFinite() || c.qualityScore < 0.0 || c.qualityScore > 1.0) {
                rejectionReasons[c.index] = VideoFrameRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_QUALITY
                continue
            }
            if (c.importanceScore != null) {
                if (!c.importanceScore.isFinite() || c.importanceScore < 0.0 || c.importanceScore > 1.0) {
                    rejectionReasons[c.index] = VideoFrameRejectionReason.NON_FINITE_OR_OUT_OF_RANGE_IMPORTANCE
                    continue
                }
            }
            validCandidates.add(c)
        }

        // Step 3: Gate minimumQuality BEFORE any novelty / diversity.
        val eligibleCandidates = mutableListOf<VideoFrameCandidate>()
        for (c in validCandidates) {
            if (c.qualityScore < configuration.minimumQuality) {
                rejectionReasons[c.index] = VideoFrameRejectionReason.BELOW_QUALITY_THRESHOLD
            } else {
                eligibleCandidates.add(c)
            }
        }

        // Step 4: Missing importance scoring policy.
        // Missing any eligible importance uses quality-only uniformly.
        // Otherwise, (1.0 - configuration.importanceWeight) * quality + configuration.importanceWeight * importance.
        val allHaveImportance = eligibleCandidates.isNotEmpty() && eligibleCandidates.all { it.importanceScore != null }

        val scoreByIndex = mutableMapOf<Int, Double>()
        for (c in eligibleCandidates) {
            if (allHaveImportance && c.importanceScore != null) {
                scoreByIndex[c.index] = (1.0 - configuration.importanceWeight) * c.qualityScore + configuration.importanceWeight * c.importanceScore
            } else {
                scoreByIndex[c.index] = c.qualityScore
            }
        }

        // Step 5: Distance matrix processing.
        val eligibleIndexSet = eligibleCandidates.map { it.index }.toSet()
        val distanceMap = mutableMapOf<PairKey, Double>()
        for (d in distances) {
            if (d.firstIndex == d.secondIndex) continue
            if (d.firstIndex !in eligibleIndexSet || d.secondIndex !in eligibleIndexSet) continue
            if (!d.distance.isFinite() || d.distance < 0.0) continue
            val key = PairKey.of(d.firstIndex, d.secondIndex)
            val existing = distanceMap[key]
            if (existing != null) {
                distanceMap[key] = max(existing, d.distance)
            } else {
                distanceMap[key] = d.distance
            }
        }

        // Step 6: Rank candidates.
        val candidateMap = eligibleCandidates.associateBy { it.index }
        val rankedIndices = eligibleCandidates.map { it.index }.sortedWith { a, b ->
            val scoreA = scoreByIndex[a] ?: 0.0
            val scoreB = scoreByIndex[b] ?: 0.0
            if (scoreA != scoreB) {
                scoreB.compareTo(scoreA) // descending
            } else {
                val candA = candidateMap.getValue(a)
                val candB = candidateMap.getValue(b)
                if (candA.timeSeconds != candB.timeSeconds) {
                    candA.timeSeconds.compareTo(candB.timeSeconds) // ascending
                } else {
                    a.compareTo(b) // ascending
                }
            }
        }

        // Step 7: Greedy frame selection.
        val selectionOrderIndices = mutableListOf<Int>()

        for (candidateIndex in rankedIndices) {
            if (selectionOrderIndices.size >= configuration.frameCount) {
                rejectionReasons[candidateIndex] = VideoFrameRejectionReason.REQUESTED_COUNT_REACHED
                continue
            }

            val cand = candidateMap.getValue(candidateIndex)
            var conflictReason: VideoFrameRejectionReason? = null

            for (acceptedIndex in selectionOrderIndices) {
                val accepted = candidateMap.getValue(acceptedIndex)
                val timeDelta = abs(cand.timeSeconds - accepted.timeSeconds)

                // Check minimum spacing conflict
                if (timeDelta < configuration.minimumSpacingSeconds) {
                    conflictReason = VideoFrameRejectionReason.MINIMUM_SPACING_CONFLICT
                    break
                }

                // Check local near duplicate conflict
                if (timeDelta <= configuration.localDiversityWindowSeconds) {
                    val key = PairKey.of(cand.index, accepted.index)
                    val dist = distanceMap[key]
                    if (dist != null && dist <= configuration.similarityThreshold) {
                        conflictReason = VideoFrameRejectionReason.LOCAL_NEAR_DUPLICATE_CONFLICT
                        break
                    }
                }
            }

            if (conflictReason != null) {
                rejectionReasons[candidateIndex] = conflictReason
            } else {
                selectionOrderIndices.add(candidateIndex)
            }
        }

        // Chronologically sorted (time ascending, then index ascending).
        val selectedFrameIndices = selectionOrderIndices.sortedWith { a, b ->
            val candA = candidateMap.getValue(a)
            val candB = candidateMap.getValue(b)
            if (candA.timeSeconds != candB.timeSeconds) {
                candA.timeSeconds.compareTo(candB.timeSeconds)
            } else {
                a.compareTo(b)
            }
        }

        // Step 8: Segment generation & Budget allocation.
        val validShots = shotBoundarySeconds
            .filter { it.isFinite() && it > 0.0 && it < durationSeconds }
            .distinct()
            .sorted()

        // Helper to find shot interval [shotStart, shotEnd) for a given time (final shot ends at durationSeconds closed)
        fun findShotInterval(time: Double): Pair<Double, Double> {
            var shotStart = 0.0
            var shotEnd = durationSeconds
            for (cut in validShots) {
                if (cut <= time) {
                    shotStart = max(shotStart, cut)
                }
                if (cut > time) {
                    shotEnd = min(shotEnd, cut)
                    break
                }
            }
            return Pair(shotStart, shotEnd)
        }

        data class AnchorContext(
            val anchorIndex: Int,
            val anchorTime: Double,
            var start: Double,
            var end: Double
        )

        val contexts = mutableListOf<AnchorContext>()
        for (idx in selectedFrameIndices) {
            val cand = candidateMap.getValue(idx)
            val t = cand.timeSeconds
            val (shotStart, shotEnd) = findShotInterval(t)

            var rawStart = max(0.0, t - configuration.preRollSeconds)
            var rawEnd = min(durationSeconds, t + configuration.postRollSeconds)

            rawStart = max(rawStart, shotStart)
            rawEnd = min(rawEnd, shotEnd)

            rawStart = min(rawStart, t)
            rawEnd = max(rawEnd, t)

            if ((rawEnd - rawStart) > configuration.maxSegmentDurationSeconds) {
                val maxDur = configuration.maxSegmentDurationSeconds
                val leftAvailable = t - rawStart
                val rightAvailable = rawEnd - t
                val desiredLeft = min(leftAvailable, configuration.preRollSeconds)
                val desiredRight = min(rightAvailable, configuration.postRollSeconds)

                if (desiredLeft + desiredRight <= maxDur) {
                    rawStart = t - desiredLeft
                    rawEnd = t + desiredRight
                } else {
                    val totalDesired = desiredLeft + desiredRight
                    if (totalDesired > 0.0) {
                        val allocLeft = min(desiredLeft, maxDur * (desiredLeft / totalDesired))
                        val allocRight = maxDur - allocLeft
                        rawStart = t - allocLeft
                        rawEnd = t + allocRight
                    } else {
                        rawStart = t
                        rawEnd = min(durationSeconds, t + maxDur)
                    }
                }
            }

            contexts.add(AnchorContext(anchorIndex = idx, anchorTime = t, start = rawStart, end = rawEnd))
        }

        // Partition contexts at midpoints between adjacent anchors to ensure disjoint non-overlapping intervals
        if (contexts.size > 1) {
            for (i in 0 until (contexts.size - 1)) {
                val mid = (contexts[i].anchorTime + contexts[i + 1].anchorTime) / 2.0
                if (contexts[i].end > mid) {
                    contexts[i].end = max(contexts[i].anchorTime, mid)
                }
                if (contexts[i + 1].start < mid) {
                    contexts[i + 1].start = min(contexts[i + 1].anchorTime, mid)
                }
            }
        }

        data class TempSegment(
            var start: Double,
            var end: Double,
            val anchorIndices: MutableList<Int>
        )

        val unmergedSegments = mutableListOf<TempSegment>()
        for (ctx in contexts) {
            val (shotStart, shotEnd) = findShotInterval(ctx.anchorTime)
            val clampedStart = max(ctx.start, shotStart)
            val clampedEnd = min(ctx.end, shotEnd)
            unmergedSegments.add(TempSegment(start = clampedStart, end = clampedEnd, anchorIndices = mutableListOf(ctx.anchorIndex)))
        }

        // Merge adjacent segments if they are in the same shot and combined duration <= maxSegmentDurationSeconds
        val mergedSegments = mutableListOf<TempSegment>()
        for (seg in unmergedSegments) {
            if (mergedSegments.isEmpty()) {
                mergedSegments.add(seg)
            } else {
                val lastSeg = mergedSegments.last()
                val canMerge = abs(lastSeg.end - seg.start) < 1e-9
                val combinedDuration = seg.end - lastSeg.start
                val sameShot = findShotInterval(lastSeg.start) == findShotInterval(seg.end)

                if (canMerge && sameShot && combinedDuration <= configuration.maxSegmentDurationSeconds) {
                    lastSeg.end = seg.end
                    lastSeg.anchorIndices.addAll(seg.anchorIndices)
                } else {
                    mergedSegments.add(seg)
                }
            }
        }

        // Allocate budget in preference (selection order) order
        val anchorToMergedIndex = mutableMapOf<Int, Int>()
        for ((i, seg) in mergedSegments.withIndex()) {
            for (anchor in seg.anchorIndices) {
                anchorToMergedIndex[anchor] = i
            }
        }

        val activeMergedIndices = mutableSetOf<Int>()

        var remainingBudget = configuration.totalDurationBudgetSeconds
        if (remainingBudget > 0.0) {
            for (anchor in selectionOrderIndices) {
                val segIdx = anchorToMergedIndex[anchor] ?: continue
                if (segIdx in activeMergedIndices) {
                    continue
                }
                val seg = mergedSegments[segIdx]
                val dur = seg.end - seg.start

                if (dur <= remainingBudget) {
                    activeMergedIndices.add(segIdx)
                    remainingBudget -= dur
                } else if (remainingBudget > 0.0) {
                    val anchorTimes = seg.anchorIndices.map { candidateMap.getValue(it).timeSeconds }
                    val minAnchorTime = anchorTimes.minOrNull() ?: seg.start
                    val maxAnchorTime = anchorTimes.maxOrNull() ?: seg.end

                    if ((maxAnchorTime - minAnchorTime) <= remainingBudget) {
                        val needed = remainingBudget
                        val currentSpan = seg.end - seg.start
                        if (currentSpan > needed) {
                            val excess = currentSpan - needed
                            val leftCanShrink = minAnchorTime - seg.start
                            val rightCanShrink = seg.end - maxAnchorTime
                            val shrinkLeft = min(leftCanShrink, excess * (leftCanShrink / max(1e-9, leftCanShrink + rightCanShrink)))
                            val shrinkRight = excess - shrinkLeft
                            seg.start += shrinkLeft
                            seg.end -= shrinkRight
                        }
                        activeMergedIndices.add(segIdx)
                        remainingBudget = 0.0
                    }
                }
            }
        }

        // Final segments filtered and sorted chronologically
        val finalSegments = mutableListOf<VideoHighlightSegment>()
        for ((i, seg) in mergedSegments.withIndex()) {
            if (i in activeMergedIndices && seg.end > seg.start) {
                finalSegments.add(VideoHighlightSegment(
                    startSeconds = seg.start,
                    endSeconds = seg.end,
                    anchorIndices = seg.anchorIndices
                ))
            }
        }
        finalSegments.sortBy { it.startSeconds }

        // Derive omittedSegmentFrameIndices as selectionOrderIndices minus Set(all final segment anchorIndices)
        val exportedAnchorIndices = finalSegments.flatMap { it.anchorIndices }.toSet()
        val omittedFrameIndices = selectionOrderIndices.filter { it !in exportedAnchorIndices }

        return VideoHighlightResult(
            selectedFrameIndices = selectedFrameIndices,
            selectionOrderIndices = selectionOrderIndices,
            segments = finalSegments,
            scoreByIndex = scoreByIndex,
            rejectionReasonsByIndex = rejectionReasons,
            omittedSegmentFrameIndices = omittedFrameIndices,
            configurationErrors = emptyList(),
            usedImportanceScores = allHaveImportance
        )
    }
}
