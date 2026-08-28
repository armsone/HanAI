package com.hanai.core

import com.hanai.core.fixtures.Fixtures
import com.hanai.core.fixtures.double
import com.hanai.core.fixtures.int
import com.hanai.core.fixtures.objects
import com.hanai.core.fixtures.string
import org.junit.Assert.assertEquals
import org.junit.Test

class ImageSimilarityFixtureTest {
    @Test
    fun sharedSimilarityFixture() {
        val file = Fixtures.loadPath("image/similarity.json")
        assertEquals(HanAIVersion.product, file.string("hanAIProductVersion"))
        for (testCase in file.objects("cases")) {
            val candidates = testCase.objects("candidates").map {
                ImageSimilarityCandidate(it.int("index"), it.double("sharpness"), it.int("pixelCount"))
            }
            val distances = testCase.objects("distances").map {
                ImagePairDistance(it.int("firstIndex"), it.int("secondIndex"), it.double("distance"))
            }
            val expected = (testCase["expected"] as List<*>).map { (it as Double).toInt() }
            assertEquals(testCase.string("id"), expected, ImageSimilaritySelector.representativeIndices(candidates, distances))
        }
    }
}
