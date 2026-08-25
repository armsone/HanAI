package com.hanai.core

import com.hanai.core.fixtures.Fixtures
import com.hanai.core.fixtures.JsonObject
import com.hanai.core.fixtures.bool
import com.hanai.core.fixtures.boolOrNull
import com.hanai.core.fixtures.double
import com.hanai.core.fixtures.doubleOrNull
import com.hanai.core.fixtures.doubles
import com.hanai.core.fixtures.obj
import com.hanai.core.fixtures.objOrNull
import com.hanai.core.fixtures.objects
import com.hanai.core.fixtures.string
import com.hanai.core.fixtures.stringOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * `Fixtures/golf`의 JSON fixture를 그대로 재생해 Kotlin 코어가 계약을 지키는지 검증한다.
 * Swift `GolfFixtureTests`와 같은 fixture를 읽는다.
 */
class GolfFixtureTest {
    private fun motionPhase(raw: String) =
        GolfSwingMotionPhase.fromRawValue(raw) ?: error("unknown motion phase $raw")

    private fun posePhase(raw: String) =
        GolfSwingPosePhase.fromRawValue(raw) ?: error("unknown pose phase $raw")

    private fun puttPhase(raw: String) =
        GolfPuttStrokePhase.fromRawValue(raw) ?: error("unknown putt phase $raw")

    private fun modelVersion(raw: String) =
        GolfModelVersion.fromRawValue(raw) ?: error("unknown model version $raw")

    private fun sensitivity(raw: String) =
        AudioImpactSensitivity.fromRawValue(raw) ?: error("unknown sensitivity $raw")

    private fun assertCurrentModel(file: JsonObject) {
        assertEquals(GolfModelVersion.current.rawValue, file.string("golfModelVersion"))
    }

    @Test
    fun swingMotionFixture() {
        val file = Fixtures.load("swing-motion.json")
        assertCurrentModel(file)
        val defaults = file.obj("defaults")

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val analyzer = GolfSwingMotionAnalyzer()
            var signal = analyzer.currentSignal(0.0)
            for (raw in testCase.objects("samples")) {
                signal = analyzer.observe(Fixtures.visualSample(raw, defaults))
            }
            val expect = testCase.obj("expect")
            expect.stringOrNull("phase")?.let { assertEquals(id, motionPhase(it), signal.phase) }
            assertEquals(id, expect.bool("isImpactWindow"), signal.isImpactWindow)
            expect.doubleOrNull("minConfidence")?.let {
                assertTrue("$id confidence ${signal.confidence} < $it", signal.confidence >= it)
            }
            expect.doubleOrNull("impactTime")?.let {
                assertNotNull(id, signal.impactTime)
                assertEquals(id, it, signal.impactTime!!, 1e-9)
            }
            expect.doubleOrNull("lastGlobalChangeTime")?.let {
                assertEquals(id, it, analyzer.lastGlobalChangeTime, 1e-9)
            }
        }
    }

    @Test
    fun swingPoseFixture() {
        val file = Fixtures.load("swing-pose.json")
        assertCurrentModel(file)
        val defaults = file.obj("defaults")

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val analyzer = GolfSwingPoseAnalyzer()
            var signal = analyzer.currentSignal(0.0)
            for (raw in testCase.objects("samples")) {
                signal = analyzer.observe(Fixtures.poseSample(raw, defaults))
            }
            val expect = testCase.obj("expect")
            assertEquals(id, posePhase(expect.string("phase")), signal.phase)
            expect.doubleOrNull("minConfidence")?.let {
                assertTrue("$id confidence ${signal.confidence} < $it", signal.confidence >= it)
            }
            expect.doubleOrNull("impactWindowStart")?.let {
                assertNotNull(id, signal.impactWindowStart)
                assertEquals(id, it, signal.impactWindowStart!!, 1e-9)
            }
            expect.doubleOrNull("impactWindowEnd")?.let {
                assertNotNull(id, signal.impactWindowEnd)
                assertEquals(id, it, signal.impactWindowEnd!!, 1e-9)
            }
            for (time in expect.doubles("isImpactWindowAt")) {
                assertTrue("$id @$time", signal.isImpactWindow(time))
            }
            for (time in expect.doubles("notImpactWindowAt")) {
                assertFalse("$id @$time", signal.isImpactWindow(time))
            }
        }
    }

    @Test
    fun puttStrokeFixture() {
        val file = Fixtures.load("putt-stroke.json")
        assertCurrentModel(file)
        val defaults = file.obj("defaults")

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val analyzer = GolfPuttStrokeAnalyzer()
            var signal = analyzer.currentSignal()
            for (raw in testCase.objects("samples")) {
                signal = analyzer.observe(Fixtures.poseSample(raw, defaults))
            }
            val expect = testCase.obj("expect")
            assertEquals(id, puttPhase(expect.string("phase")), signal.phase)
            assertEquals(id, expect.bool("isConfirmedStroke"), signal.isConfirmedStroke)
            expect.doubleOrNull("minConfidence")?.let {
                assertTrue("$id confidence ${signal.confidence} < $it", signal.confidence >= it)
            }
            expect.doubleOrNull("strokeTime")?.let {
                assertNotNull(id, signal.strokeTime)
                assertEquals(id, it, signal.strokeTime!!, 1e-9)
            }
            for (time in expect.doubles("latchedAt")) {
                assertNotNull("$id latched @$time", analyzer.latchedConfirmedStroke(time))
            }
            for (time in expect.doubles("notLatchedAt")) {
                assertNull("$id not latched @$time", analyzer.latchedConfirmedStroke(time))
            }
        }
    }

    @Test
    fun audioImpactFixture() {
        val file = Fixtures.load("audio-impact.json")
        assertCurrentModel(file)

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val decision = AudioImpactClassifier.detectImpact(
                metrics = Fixtures.metrics(testCase.obj("metrics")),
                baseline = testCase.double("baseline"),
                previousRecentLevel = testCase.double("previousRecentLevel"),
                sensitivity = sensitivity(testCase.string("sensitivity"))
            )
            val expect = testCase.obj("expect")
            assertEquals(id, expect.bool("isTriggered"), decision.isTriggered)
            if (expect.boolOrNull("confidenceIsZero") == true) {
                assertEquals(id, 0.0, decision.confidence, 0.0)
            }
            expect.doubleOrNull("minConfidence")?.let {
                assertTrue("$id confidence ${decision.confidence} < $it", decision.confidence >= it)
            }
        }
    }

    @Test
    fun swingFusionFixture() {
        val file = Fixtures.load("swing-fusion.json")
        assertCurrentModel(file)

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val decisionRaw = testCase.obj("decision")
            val motionRaw = testCase.obj("motion")
            val motion = GolfSwingMotionSignal(
                phase = motionPhase(motionRaw.string("phase")),
                confidence = motionRaw.double("confidence"),
                impactTime = motionRaw.doubleOrNull("impactTime")
            )
            val pose = testCase.objOrNull("pose")?.let { raw ->
                GolfSwingPoseSignal(
                    phase = posePhase(raw.string("phase")),
                    confidence = raw.double("confidence"),
                    impactWindowStart = raw.doubleOrNull("impactWindowStart"),
                    impactWindowEnd = raw.doubleOrNull("impactWindowEnd")
                )
            }
            val result = GolfSwingFusionPolicy.shouldTrigger(
                decision = AudioImpactDecision(
                    isTriggered = decisionRaw.bool("isTriggered"),
                    confidence = decisionRaw.double("confidence")
                ),
                metrics = Fixtures.metrics(testCase.obj("metrics")),
                motion = motion,
                pose = pose,
                referenceTime = testCase.double("referenceTime"),
                requiresPoseConfirmation = testCase.bool("requiresPoseConfirmation"),
                hasRecentVisualFrame = testCase.bool("hasRecentVisualFrame"),
                isInsideReadyPromptWindow = testCase.bool("isInsideReadyPromptWindow"),
                modelVersion = modelVersion(testCase.string("modelVersion"))
            )
            assertEquals(id, testCase.bool("expect"), result)
        }
    }

    @Test
    fun puttFusionFixture() {
        val file = Fixtures.load("putt-fusion.json")
        assertCurrentModel(file)

        for (testCase in file.objects("cases")) {
            val id = testCase.string("id")
            val stroke = testCase.objOrNull("stroke")?.let { raw ->
                GolfPuttStrokeSignal(
                    phase = puttPhase(raw.string("phase")),
                    confidence = raw.double("confidence"),
                    strokeTime = raw.doubleOrNull("strokeTime")
                )
            }
            val result = GolfPuttFusionPolicy.shouldTrigger(
                stroke = stroke,
                poseObservationConfidence = testCase.double("poseObservationConfidence"),
                secondsSinceLatestPose = testCase.double("secondsSinceLatestPose"),
                secondsSinceLatestVisualFrame = testCase.double("secondsSinceLatestVisualFrame"),
                secondsSinceLastGlobalChange = testCase.double("secondsSinceLastGlobalChange"),
                isReady = testCase.bool("isReady"),
                isInsideReadyPromptWindow = testCase.bool("isInsideReadyPromptWindow"),
                isTriggerPending = testCase.bool("isTriggerPending"),
                modelVersion = modelVersion(testCase.string("modelVersion"))
            )
            assertEquals(id, testCase.bool("expect"), result)
        }
    }
}
