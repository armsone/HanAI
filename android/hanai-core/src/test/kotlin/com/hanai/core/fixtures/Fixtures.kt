package com.hanai.core.fixtures

import com.hanai.core.AudioImpactMetrics
import com.hanai.core.GolfSwingPoseSample
import com.hanai.core.GolfSwingVisualSample
import java.io.File

/** 저장소 루트 `Fixtures/golf`의 JSON fixture를 읽는다. Swift 테스트와 같은 파일을 공유한다. */
internal object Fixtures {
    private val directory: File by lazy {
        val candidates = listOfNotNull(
            System.getProperty("hanai.fixturesDir")?.let(::File),
            File("../Fixtures"),
            File("../../Fixtures"),
            File("Fixtures")
        )
        candidates.firstOrNull { it.isDirectory }
            ?: error("Fixtures 디렉터리를 찾지 못했습니다: ${candidates.map { it.absolutePath }}")
    }

    fun load(name: String): JsonObject {
        val file = File(directory, "golf/$name")
        return MiniJson.parse(file.readText()) as JsonObject
    }

    fun loadPath(relativePath: String): JsonObject {
        val file = File(directory, relativePath)
        return MiniJson.parse(file.readText()) as JsonObject
    }

    fun visualSample(raw: JsonObject, defaults: JsonObject): GolfSwingVisualSample = GolfSwingVisualSample(
        time = raw.double("time"),
        localMotion = raw.double("localMotion"),
        globalMotion = raw.doubleOrNull("globalMotion") ?: defaults.double("globalMotion"),
        widespreadMotion = raw.doubleOrNull("widespreadMotion") ?: defaults.double("widespreadMotion"),
        concentration = raw.doubleOrNull("concentration") ?: defaults.double("concentration"),
        brightnessChange = raw.doubleOrNull("brightnessChange") ?: defaults.double("brightnessChange"),
        dominantRegion = raw.intOrNull("dominantRegion") ?: defaults.int("dominantRegion")
    )

    fun poseSample(raw: JsonObject, defaults: JsonObject): GolfSwingPoseSample = GolfSwingPoseSample(
        time = raw.double("time"),
        handX = raw.double("handX"),
        handY = raw.doubleOrNull("handY") ?: defaults.double("handY"),
        coreX = raw.doubleOrNull("coreX") ?: defaults.double("coreX"),
        coreY = raw.doubleOrNull("coreY") ?: defaults.double("coreY"),
        bodyScale = raw.doubleOrNull("bodyScale") ?: defaults.double("bodyScale"),
        confidence = raw.doubleOrNull("confidence") ?: defaults.double("confidence")
    )

    fun metrics(raw: JsonObject): AudioImpactMetrics = AudioImpactMetrics(
        rms = raw.double("rms"),
        peak = raw.double("peak"),
        crossingRate = raw.double("crossingRate")
    )
}
