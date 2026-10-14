package com.hanai.core

import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

/** Body continuity independent of noisy hand-phase estimation. */
class GolfSwingBodyTracker {
    companion object {
        const val OBSERVATION_WINDOW = 1.2
        const val MINIMUM_OBSERVATION_DURATION = 0.55
        const val MAXIMUM_OBSERVATION_GAP = 0.6
        const val MAXIMUM_OBSERVATION_AGE = 0.35
        const val MAXIMUM_CORE_TRAVEL = 0.75
    }

    private val samples = mutableListOf<GolfSwingPoseSample>()

    fun reset() { samples.clear() }

    fun observe(sample: GolfSwingPoseSample) {
        if (!listOf(sample.time, sample.handX, sample.handY, sample.coreX, sample.coreY,
                sample.bodyScale, sample.confidence).all { it.isFinite() } ||
            sample.confidence < 0.45 || sample.bodyScale < 0.04) return
        samples.lastOrNull()?.let { last ->
            if (sample.time <= last.time || sample.time - last.time > MAXIMUM_OBSERVATION_GAP) reset()
        }
        samples.removeAll { sample.time - it.time > OBSERVATION_WINDOW }
        samples.add(sample)
    }

    /** Body continuity is not a club label or proof of a completed shot. */
    fun hasRecentTrackedBody(time: Double): Boolean {
        val last = samples.lastOrNull() ?: return false
        if (!time.isFinite() || time < last.time || time - last.time > MAXIMUM_OBSERVATION_AGE) return false
        val recent = samples.filter { time - it.time <= OBSERVATION_WINDOW }
        if (recent.size < 3 || last.time - recent.first().time < MINIMUM_OBSERVATION_DURATION) return false
        for (index in recent.indices) {
            for (other in recent.drop(index + 1)) {
                val sample = recent[index]
                val travel = hypot(sample.coreX - other.coreX, sample.coreY - other.coreY) /
                    max(0.04, min(sample.bodyScale, other.bodyScale))
                if (travel > MAXIMUM_CORE_TRAVEL) return false
            }
        }
        return true
    }
}
