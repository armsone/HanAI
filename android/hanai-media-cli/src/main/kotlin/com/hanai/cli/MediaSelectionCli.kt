package com.hanai.cli

import com.hanai.core.ImageDiversityGroup
import com.hanai.core.ImageDiversityRejectionReason
import com.hanai.core.ImageDiversityResult
import com.hanai.core.ImageDiversitySelector
import com.hanai.core.ImagePairDistance
import com.hanai.core.ImageQualityCandidate
import com.hanai.core.ImageSelectionRole
import com.hanai.core.VideoFrameCandidate
import com.hanai.core.VideoFrameRejectionReason
import com.hanai.core.VideoHighlightConfiguration
import com.hanai.core.VideoHighlightConfigurationError
import com.hanai.core.VideoHighlightResult
import com.hanai.core.VideoHighlightSegment
import com.hanai.core.VideoHighlightSelector
import java.io.BufferedReader
import java.io.InputStream
import java.io.InputStreamReader
import java.nio.charset.StandardCharsets
import kotlin.system.exitProcess

private const val PROTOCOL_HELP = """HanAI Media Selection CLI Bridge
Usage:
  java -jar hanai-media-cli.jar [options]
  Options:
    --help    Display this help text

Standard Input Protocol:
  Header line (strict uppercase, tab-separated):
    PHOTO\tcount\tthreshold
    VIDEO\tcount\tthreshold\tdurationSeconds\tbudgetSeconds[\timportanceWeight]
  Data lines (tab-separated, until EOF):
    PHOTO candidates:  I\tindex\tsharpness\texposureScore\tpixelCount\taestheticsOrEmpty
    VIDEO candidates:  F\tindex\ttimeSeconds\tqualityScore\timportanceOrEmpty
    Distances (both):  D\tfirst\tsecond\tdistance
    Shots (VIDEO):     B\tboundarySeconds

Bounds:
  Header count in 0..2000, threshold finite >= 0.
  Duration finite > 0, budget finite >= 0, optional importanceWeight finite in 0..1 (default 0.3).
  Max 2000 candidate records, 250000 distance records, 2000 boundary records.
  Max line length 256 characters, max 254002 total lines.
  No blank lines. Fixed sanitized error message on protocol violations.

Standard Output:
  Single line JSON object with protocolVersion: 1 and mode: "PHOTO" | "VIDEO"."""

private const val SANITIZED_ERROR_MESSAGE = "Invalid input protocol format or constraint violation."

private const val MAX_LINE_LENGTH = 256
private const val MAX_TOTAL_LINES = 254002
private const val MAX_CANDIDATE_RECORDS = 2000
private const val MAX_DISTANCE_RECORDS = 250000
private const val MAX_BOUNDARY_RECORDS = 2000

private class ProtocolViolationException(message: String = SANITIZED_ERROR_MESSAGE) : RuntimeException(message)

private class LimitedLineReader(private val reader: BufferedReader) {
    private var totalLinesRead = 0

    fun readLimitedLine(): String? {
        if (totalLinesRead >= MAX_TOTAL_LINES) {
            val nextChar = reader.read()
            if (nextChar != -1) {
                throw ProtocolViolationException()
            }
            return null
        }

        val sb = StringBuilder()
        while (true) {
            val c = reader.read()
            if (c == -1) {
                if (sb.isEmpty()) {
                    return null
                }
                break
            }
            if (c == '\n'.code) {
                break
            }
            if (c == '\r'.code) {
                reader.mark(1)
                val next = reader.read()
                if (next != '\n'.code && next != -1) {
                    reader.reset()
                }
                break
            }
            if (sb.length >= MAX_LINE_LENGTH) {
                throw ProtocolViolationException()
            }
            sb.append(c.toChar())
        }

        totalLinesRead++
        return sb.toString()
    }
}

private sealed class Header {
    data class Photo(val count: Int, val threshold: Double) : Header()
    data class Video(
        val count: Int,
        val threshold: Double,
        val durationSeconds: Double,
        val budgetSeconds: Double,
        val importanceWeight: Double = 0.3
    ) : Header()
}

fun main(args: Array<String>) {
    if (args.isNotEmpty()) {
        if (args.size == 1 && args[0] == "--help") {
            println(PROTOCOL_HELP)
            exitProcess(0)
        } else {
            System.err.println(SANITIZED_ERROR_MESSAGE)
            exitProcess(1)
        }
    }

    try {
        processInput(System.`in`)
    } catch (e: Exception) {
        System.err.println(SANITIZED_ERROR_MESSAGE)
        exitProcess(1)
    }
}

internal fun processInput(inputStream: InputStream) {
    val reader = LimitedLineReader(BufferedReader(InputStreamReader(inputStream, StandardCharsets.UTF_8)))

    val firstLine = reader.readLimitedLine() ?: throw ProtocolViolationException()
    if (firstLine.isEmpty()) {
        throw ProtocolViolationException()
    }

    val headerParts = firstLine.split("\t", limit = 0)
    val header = parseHeader(headerParts)

    when (header) {
        is Header.Photo -> processPhoto(header, reader)
        is Header.Video -> processVideo(header, reader)
    }
}

private fun parseHeader(parts: List<String>): Header {
    if (parts.isEmpty()) throw ProtocolViolationException()

    when (parts[0]) {
        "PHOTO" -> {
            if (parts.size != 3) throw ProtocolViolationException()
            val count = parseIntExact(parts[1])
            if (count !in 0..2000) throw ProtocolViolationException()
            val threshold = parseDoubleExact(parts[2])
            if (!threshold.isFinite() || threshold < 0.0) throw ProtocolViolationException()
            return Header.Photo(count, threshold)
        }
        "VIDEO" -> {
            if (parts.size != 5 && parts.size != 6) throw ProtocolViolationException()
            val count = parseIntExact(parts[1])
            if (count !in 0..2000) throw ProtocolViolationException()
            val threshold = parseDoubleExact(parts[2])
            if (!threshold.isFinite() || threshold < 0.0) throw ProtocolViolationException()
            val durationSeconds = parseDoubleExact(parts[3])
            if (!durationSeconds.isFinite() || durationSeconds <= 0.0) throw ProtocolViolationException()
            val budgetSeconds = parseDoubleExact(parts[4])
            if (!budgetSeconds.isFinite() || budgetSeconds < 0.0) throw ProtocolViolationException()
            val importanceWeight = if (parts.size == 6) {
                if (parts[5].isEmpty()) throw ProtocolViolationException()
                val weight = parseDoubleExact(parts[5])
                if (!weight.isFinite() || weight < 0.0 || weight > 1.0) throw ProtocolViolationException()
                weight
            } else {
                0.3
            }
            return Header.Video(count, threshold, durationSeconds, budgetSeconds, importanceWeight)
        }
        else -> throw ProtocolViolationException()
    }
}

private fun processPhoto(header: Header.Photo, reader: LimitedLineReader) {
    val candidates = mutableListOf<ImageQualityCandidate>()
    val distances = mutableListOf<ImagePairDistance>()

    var line = reader.readLimitedLine()
    while (line != null) {
        if (line.isEmpty()) throw ProtocolViolationException()

        val parts = line.split("\t", limit = 0)
        if (parts.isEmpty()) throw ProtocolViolationException()

        when (parts[0]) {
            "I" -> {
                if (candidates.size >= MAX_CANDIDATE_RECORDS) throw ProtocolViolationException()
                if (parts.size != 6) throw ProtocolViolationException()
                val index = parseIntExact(parts[1])
                if (index < 0) throw ProtocolViolationException()
                val sharpness = parseDoubleExact(parts[2])
                if (!sharpness.isFinite() || sharpness < 0.0 || sharpness > 1.0) throw ProtocolViolationException()
                val exposureScore = parseDoubleExact(parts[3])
                if (!exposureScore.isFinite() || exposureScore < 0.0 || exposureScore > 1.0) throw ProtocolViolationException()
                val pixelCount = parseIntExact(parts[4])
                if (pixelCount <= 0) throw ProtocolViolationException()
                val aestheticsScore = if (parts[5].isEmpty()) {
                    null
                } else {
                    val aes = parseDoubleExact(parts[5])
                    if (!aes.isFinite() || aes < 0.0 || aes > 1.0) throw ProtocolViolationException()
                    aes
                }
                candidates.add(ImageQualityCandidate(index, sharpness, exposureScore, pixelCount, aestheticsScore))
            }
            "D" -> {
                if (distances.size >= MAX_DISTANCE_RECORDS) throw ProtocolViolationException()
                if (parts.size != 4) throw ProtocolViolationException()
                val first = parseIntExact(parts[1])
                val second = parseIntExact(parts[2])
                val dist = parseDoubleExact(parts[3])
                if (!dist.isFinite() || dist < 0.0) throw ProtocolViolationException()
                distances.add(ImagePairDistance(first, second, dist))
            }
            else -> throw ProtocolViolationException()
        }

        line = reader.readLimitedLine()
    }

    val result = ImageDiversitySelector.selectDiverse(
        candidates = candidates,
        distances = distances,
        threshold = header.threshold,
        n = header.count
    )

    println(serializePhotoResult(result))
}

private fun processVideo(header: Header.Video, reader: LimitedLineReader) {
    val candidates = mutableListOf<VideoFrameCandidate>()
    val distances = mutableListOf<ImagePairDistance>()
    val boundaries = mutableListOf<Double>()

    var line = reader.readLimitedLine()
    while (line != null) {
        if (line.isEmpty()) throw ProtocolViolationException()

        val parts = line.split("\t", limit = 0)
        if (parts.isEmpty()) throw ProtocolViolationException()

        when (parts[0]) {
            "F" -> {
                if (candidates.size >= MAX_CANDIDATE_RECORDS) throw ProtocolViolationException()
                if (parts.size != 5) throw ProtocolViolationException()
                val index = parseIntExact(parts[1])
                if (index < 0) throw ProtocolViolationException()
                val timeSeconds = parseDoubleExact(parts[2])
                if (!timeSeconds.isFinite() || timeSeconds < 0.0) throw ProtocolViolationException()
                val qualityScore = parseDoubleExact(parts[3])
                if (!qualityScore.isFinite() || qualityScore < 0.0 || qualityScore > 1.0) throw ProtocolViolationException()
                val importanceScore = if (parts[4].isEmpty()) {
                    null
                } else {
                    val imp = parseDoubleExact(parts[4])
                    if (!imp.isFinite() || imp < 0.0 || imp > 1.0) throw ProtocolViolationException()
                    imp
                }
                candidates.add(VideoFrameCandidate(index, timeSeconds, qualityScore, importanceScore))
            }
            "D" -> {
                if (distances.size >= MAX_DISTANCE_RECORDS) throw ProtocolViolationException()
                if (parts.size != 4) throw ProtocolViolationException()
                val first = parseIntExact(parts[1])
                val second = parseIntExact(parts[2])
                val dist = parseDoubleExact(parts[3])
                if (!dist.isFinite() || dist < 0.0) throw ProtocolViolationException()
                distances.add(ImagePairDistance(first, second, dist))
            }
            "B" -> {
                if (boundaries.size >= MAX_BOUNDARY_RECORDS) throw ProtocolViolationException()
                if (parts.size != 2) throw ProtocolViolationException()
                val boundary = parseDoubleExact(parts[1])
                if (!boundary.isFinite() || boundary < 0.0) throw ProtocolViolationException()
                boundaries.add(boundary)
            }
            else -> throw ProtocolViolationException()
        }

        line = reader.readLimitedLine()
    }

    val config = VideoHighlightConfiguration(
        frameCount = header.count,
        similarityThreshold = header.threshold,
        totalDurationBudgetSeconds = header.budgetSeconds,
        importanceWeight = header.importanceWeight
    )

    val result = VideoHighlightSelector.select(
        candidates = candidates,
        distances = distances,
        durationSeconds = header.durationSeconds,
        configuration = config,
        shotBoundarySeconds = boundaries
    )

    println(serializeVideoResult(result))
}

private fun parseIntExact(s: String): Int {
    if (s.isEmpty()) throw ProtocolViolationException()
    return s.toIntOrNull() ?: throw ProtocolViolationException()
}

private fun parseDoubleExact(s: String): Double {
    if (s.isEmpty()) throw ProtocolViolationException()
    val v = s.toDoubleOrNull() ?: throw ProtocolViolationException()
    if (!v.isFinite()) throw ProtocolViolationException()
    return v
}

// JSON Serialization Helpers

private fun escapeJsonString(s: String): String {
    val sb = StringBuilder()
    for (c in s) {
        when (c) {
            '"' -> sb.append("\\\"")
            '\\' -> sb.append("\\\\")
            '\b' -> sb.append("\\b")
            '\u000C' -> sb.append("\\f")
            '\n' -> sb.append("\\n")
            '\r' -> sb.append("\\r")
            '\t' -> sb.append("\\t")
            else -> {
                if (c.code in 0..0x1F) {
                    val hex = Integer.toHexString(c.code).padStart(4, '0')
                    sb.append("\\u").append(hex)
                } else {
                    sb.append(c)
                }
            }
        }
    }
    return sb.toString()
}

private fun formatDouble(d: Double): String {
    if (!d.isFinite()) {
        throw ProtocolViolationException("Non-finite double encountered in JSON serialization")
    }
    val s = d.toString()
    return if (s.contains('.') || s.contains('e') || s.contains('E')) s else "$s.0"
}

internal fun serializePhotoResult(result: ImageDiversityResult): String {
    val sb = StringBuilder()
    sb.append('{')
    sb.append("\"protocolVersion\":1,")
    sb.append("\"mode\":\"PHOTO\",")

    // selectedIndices
    sb.append("\"selectedIndices\":[")
    sb.append(result.selectedIndices.joinToString(","))
    sb.append("],")

    // qualityRankedIndices
    sb.append("\"qualityRankedIndices\":[")
    sb.append(result.qualityRankedIndices.joinToString(","))
    sb.append("],")

    // selectionOrderIndices
    sb.append("\"selectionOrderIndices\":[")
    sb.append(result.selectionOrderIndices.joinToString(","))
    sb.append("],")

    // scoreByIndex (string ID keys, sorted numerically)
    sb.append("\"scoreByIndex\":{")
    val sortedScores = result.scoreByIndex.entries.sortedBy { it.key }
    sb.append(sortedScores.joinToString(",") { "\"${it.key}\":${formatDouble(it.value)}" })
    sb.append("},")

    // groups [{representativeIndex,memberIndices}]
    sb.append("\"groups\":[")
    val groupsStr = result.groups.joinToString(",") { g ->
        "{\"representativeIndex\":${g.representativeIndex},\"memberIndices\":[${g.memberIndices.joinToString(",")}]}"
    }
    sb.append(groupsStr)
    sb.append("],")

    // selectionRoleByIndex
    sb.append("\"selectionRoleByIndex\":{")
    val sortedRoles = result.selectionRoleByIndex.entries.sortedBy { it.key }
    sb.append(sortedRoles.joinToString(",") { "\"${it.key}\":\"${escapeJsonString(it.value.name)}\"" })
    sb.append("},")

    // rejectionReasonsByIndex
    sb.append("\"rejectionReasonsByIndex\":{")
    val sortedRejections = result.rejectionReasonsByIndex.entries.sortedBy { it.key }
    sb.append(sortedRejections.joinToString(",") { "\"${it.key}\":\"${escapeJsonString(it.value.name)}\"" })
    sb.append("}")

    sb.append('}')
    return sb.toString()
}

internal fun serializeVideoResult(result: VideoHighlightResult): String {
    val sb = StringBuilder()
    sb.append('{')
    sb.append("\"protocolVersion\":1,")
    sb.append("\"mode\":\"VIDEO\",")

    // selectedFrameIndices
    sb.append("\"selectedFrameIndices\":[")
    sb.append(result.selectedFrameIndices.joinToString(","))
    sb.append("],")

    // selectionOrderIndices
    sb.append("\"selectionOrderIndices\":[")
    sb.append(result.selectionOrderIndices.joinToString(","))
    sb.append("],")

    // segments [{startSeconds,endSeconds,anchorIndices}]
    sb.append("\"segments\":[")
    val segmentsStr = result.segments.joinToString(",") { seg ->
        "{\"startSeconds\":${formatDouble(seg.startSeconds)},\"endSeconds\":${formatDouble(seg.endSeconds)},\"anchorIndices\":[${seg.anchorIndices.joinToString(",")}]}"
    }
    sb.append(segmentsStr)
    sb.append("],")

    // scoreByIndex
    sb.append("\"scoreByIndex\":{")
    val sortedScores = result.scoreByIndex.entries.sortedBy { it.key }
    sb.append(sortedScores.joinToString(",") { "\"${it.key}\":${formatDouble(it.value)}" })
    sb.append("},")

    // rejectionReasonsByIndex
    sb.append("\"rejectionReasonsByIndex\":{")
    val sortedRejections = result.rejectionReasonsByIndex.entries.sortedBy { it.key }
    sb.append(sortedRejections.joinToString(",") { "\"${it.key}\":\"${escapeJsonString(it.value.name)}\"" })
    sb.append("},")

    // omittedSegmentFrameIndices
    sb.append("\"omittedSegmentFrameIndices\":[")
    sb.append(result.omittedSegmentFrameIndices.joinToString(","))
    sb.append("],")

    // configurationErrors
    sb.append("\"configurationErrors\":[")
    sb.append(result.configurationErrors.joinToString(",") { "\"${escapeJsonString(it.name)}\"" })
    sb.append("],")

    // usedImportanceScores
    sb.append("\"usedImportanceScores\":${result.usedImportanceScores}")

    sb.append('}')
    return sb.toString()
}
