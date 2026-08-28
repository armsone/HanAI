package com.hanai.core

import kotlin.math.ln

data class ImageSimilarityCandidate(
    val index: Int,
    val sharpness: Double,
    val pixelCount: Int
)

data class ImagePairDistance(
    val firstIndex: Int,
    val secondIndex: Int,
    val distance: Double
)

/** Platform adapters supply only anonymous numeric features; this core never receives images. */
object ImageSimilaritySelector {
    const val nearDuplicateThreshold: Double = 0.12

    fun representativeIndices(
        candidates: List<ImageSimilarityCandidate>,
        distances: List<ImagePairDistance>,
        threshold: Double = nearDuplicateThreshold
    ): List<Int> {
        if (threshold < 0.0 || candidates.isEmpty()) return candidates.map { it.index }
        val byIndex = candidates.associateBy { it.index }
        val distanceMap = distances.filter { it.distance >= 0.0 }.associate {
            PairKey.of(it.firstIndex, it.secondIndex) to it.distance
        }
        val representatives = mutableListOf<Int>()
        for (candidate in candidates) {
            val position = representatives.indexOfFirst { representative ->
                (distanceMap[PairKey.of(candidate.index, representative)] ?: Double.POSITIVE_INFINITY) <= threshold
            }
            if (position >= 0) {
                val current = byIndex.getValue(representatives[position])
                if (qualityScore(candidate) > qualityScore(current)) representatives[position] = candidate.index
            } else {
                representatives += candidate.index
            }
        }
        return representatives.sorted()
    }

    private fun qualityScore(candidate: ImageSimilarityCandidate): Double =
        candidate.sharpness + ln(maxOf(1, candidate.pixelCount).toDouble()) / 100.0

    private data class PairKey(val low: Int, val high: Int) {
        companion object {
            fun of(first: Int, second: Int) = PairKey(minOf(first, second), maxOf(first, second))
        }
    }
}
