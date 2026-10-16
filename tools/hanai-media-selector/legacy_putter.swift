import Foundation
#if canImport(CoreFoundation)
import CoreFoundation
#endif

// Bounded, read-only numeric evaluation runner. Calls the actual
// GolfPutterDetector core; never reimplements its detection logic.

private let maxStdinBytes = 128 * 1024 * 1024
private let maxSamples = 1_000_000
private let maxCandidates = 10_000
private let windowHalfSeconds = 4.0

private enum RunnerError: Error {
    case fixed(Int32)
}

private func fail(_ code: Int32) -> Never {
    FileHandle.standardError.write("error\n".data(using: .utf8)!)
    exit(code)
}

private func isJSONBoolean(_ value: Any) -> Bool {
    #if canImport(CoreFoundation)
    if CFGetTypeID(value as CFTypeRef) == CFBooleanGetTypeID() {
        return true
    }
    return false
    #else
    return value is Bool
    #endif
}

private func asFiniteDouble(_ value: Any?) -> Double? {
    guard let value, !isJSONBoolean(value) else { return nil }
    if let n = value as? NSNumber {
        let d = n.doubleValue
        return d.isFinite ? d : nil
    }
    return nil
}

private func asNonNegativeInt(_ value: Any?) -> Int? {
    guard let value, !isJSONBoolean(value) else { return nil }
    if let n = value as? NSNumber {
        let d = n.doubleValue
        guard d.isFinite, d.truncatingRemainder(dividingBy: 1) == 0, d >= 0 else { return nil }
        let i = n.intValue
        return i
    }
    return nil
}

private struct RawSample {
    let bodyScale: Double
    let confidence: Double
    let coreX: Double
    let coreY: Double
    let handX: Double
    let handY: Double
    let sourceIndex: Int
    let time: Double
}

private struct RawCandidate {
    let clipId: Int
    let sourceIndex: Int
    let timeSeconds: Double
}

private func parseSample(_ dict: [String: Any]) -> RawSample? {
    guard let bodyScale = asFiniteDouble(dict["bodyScale"]), bodyScale >= 0,
          let confidence = asFiniteDouble(dict["confidence"]), confidence >= 0, confidence <= 1,
          let coreX = asFiniteDouble(dict["coreX"]),
          let coreY = asFiniteDouble(dict["coreY"]),
          let handX = asFiniteDouble(dict["handX"]),
          let handY = asFiniteDouble(dict["handY"]),
          let sourceIndex = asNonNegativeInt(dict["sourceIndex"]),
          let time = asFiniteDouble(dict["time"]), time >= 0
    else { return nil }
    return RawSample(
        bodyScale: bodyScale, confidence: confidence,
        coreX: coreX, coreY: coreY, handX: handX, handY: handY,
        sourceIndex: sourceIndex, time: time
    )
}

private func parseCandidate(_ dict: [String: Any]) -> RawCandidate? {
    guard let clipId = asNonNegativeInt(dict["clipId"]),
          let sourceIndex = asNonNegativeInt(dict["sourceIndex"]),
          let timeSeconds = asFiniteDouble(dict["timeSeconds"]), timeSeconds >= 0
    else { return nil }
    return RawCandidate(clipId: clipId, sourceIndex: sourceIndex, timeSeconds: timeSeconds)
}

private func toPoseSample(_ raw: RawSample) -> GolfSwingPoseSample {
    GolfSwingPoseSample(
        time: raw.time,
        handX: raw.handX,
        handY: raw.handY,
        coreX: raw.coreX,
        coreY: raw.coreY,
        bodyScale: raw.bodyScale,
        confidence: raw.confidence
    )
}

private func isEligible(_ raw: RawSample) -> Bool {
    raw.confidence >= 0.45 && raw.bodyScale >= 0.04
}

private struct CoverageMetrics {
    let sampleCount: Int
    let firstTimeSeconds: Double?
    let lastTimeSeconds: Double?
    let maxGapSeconds: Double?
}

private func coverage(for eligible: [RawSample]) -> CoverageMetrics {
    let times = eligible.map { $0.time }.sorted()
    guard let first = times.first, let last = times.last else {
        return CoverageMetrics(sampleCount: 0, firstTimeSeconds: nil, lastTimeSeconds: nil, maxGapSeconds: nil)
    }
    guard times.count >= 2 else {
        return CoverageMetrics(sampleCount: times.count, firstTimeSeconds: first, lastTimeSeconds: last, maxGapSeconds: nil)
    }
    var maxGap = 0.0
    for i in 1..<times.count {
        maxGap = max(maxGap, times[i] - times[i - 1])
    }
    return CoverageMetrics(sampleCount: times.count, firstTimeSeconds: first, lastTimeSeconds: last, maxGapSeconds: maxGap)
}

private func jsonValue(_ double: Double?) -> Any {
    double ?? NSNull()
}

@main
struct LegacyPutterRunner {
    static func main() {
        let inputData = FileHandle.standardInput.readData(ofLength: maxStdinBytes + 1)
        guard inputData.count <= maxStdinBytes else { fail(2) }

        let rootAny: Any
        do {
            rootAny = try JSONSerialization.jsonObject(with: inputData, options: [])
        } catch {
            fail(2)
        }

        guard let root = rootAny as? [String: Any],
              let samplesRaw = root["samples"] as? [Any],
              let candidatesRaw = root["candidates"] as? [Any],
              samplesRaw.count <= maxSamples,
              candidatesRaw.count <= maxCandidates
        else { fail(2) }

        var invalidSampleCount = 0
        var samplesBySource: [Int: [RawSample]] = [:]
        var allSamplesBySource: [Int: [GolfSwingPoseSample]] = [:]

        for entry in samplesRaw {
            guard let dict = entry as? [String: Any], let parsed = parseSample(dict) else {
                invalidSampleCount += 1
                continue
            }
            samplesBySource[parsed.sourceIndex, default: []].append(parsed)
            allSamplesBySource[parsed.sourceIndex, default: []].append(toPoseSample(parsed))
        }

        var candidates: [RawCandidate] = []
        var seenClipIds = Set<Int>()
        for entry in candidatesRaw {
            guard let dict = entry as? [String: Any], let parsed = parseCandidate(dict) else {
                fail(3)
            }
            guard !seenClipIds.contains(parsed.clipId) else { fail(3) }
            seenClipIds.insert(parsed.clipId)
            candidates.append(parsed)
        }

        var candidateOutputs: [[String: Any]] = []
        for candidate in candidates {
            let sourceSamples = samplesBySource[candidate.sourceIndex] ?? []
            let windowSamplesRaw = sourceSamples.filter {
                abs($0.time - candidate.timeSeconds) <= windowHalfSeconds
            }
            let rawSampleCount = windowSamplesRaw.count
            let eligible = windowSamplesRaw.filter(isEligible)
            let cov = coverage(for: eligible)

            let eligibleSpan: Double? = {
                guard let f = cov.firstTimeSeconds, let l = cov.lastTimeSeconds else { return nil }
                return l - f
            }()

            var score: Double? = nil
            var status = "insufficient-pose"
            if eligible.count >= 7, let span = eligibleSpan, span >= 1.6 {
                let poseSamples = windowSamplesRaw.map(toPoseSample)
                let detections = GolfPutterDetector.evaluateSeries(samples: poseSamples)
                let maxConfidence = detections
                    .map { $0.confidence }
                    .filter { $0.isFinite }
                    .max()
                score = maxConfidence ?? 0
                status = "ok"
            }

            candidateOutputs.append([
                "clipId": candidate.clipId,
                "score": jsonValue(score),
                "status": status,
                "sampleCount": cov.sampleCount,
                "timeSpanSeconds": jsonValue(eligibleSpan),
                "rawSampleCount": rawSampleCount,
                "firstTimeSeconds": jsonValue(cov.firstTimeSeconds),
                "lastTimeSeconds": jsonValue(cov.lastTimeSeconds),
                "maxGapSeconds": jsonValue(cov.maxGapSeconds),
            ])
        }

        var detectionOutputs: [[String: Any]] = []
        var sourceCoverageOutputs: [[String: Any]] = []
        for sourceIndex in allSamplesBySource.keys.sorted() {
            let poseSamples = allSamplesBySource[sourceIndex] ?? []
            let detections = GolfPutterDetector.detectStrokes(samples: poseSamples)
            for detection in detections {
                guard let strokeTime = detection.strokeTime, strokeTime.isFinite,
                      detection.confidence.isFinite
                else { continue }
                detectionOutputs.append([
                    "sourceIndex": sourceIndex,
                    "timeSeconds": strokeTime,
                    "confidence": detection.confidence,
                ])
            }

            let eligible = (samplesBySource[sourceIndex] ?? []).filter(isEligible)
            let cov = coverage(for: eligible)
            sourceCoverageOutputs.append([
                "sourceIndex": sourceIndex,
                "sampleCount": cov.sampleCount,
                "firstTimeSeconds": jsonValue(cov.firstTimeSeconds),
                "lastTimeSeconds": jsonValue(cov.lastTimeSeconds),
                "maxGapSeconds": jsonValue(cov.maxGapSeconds),
            ])
        }

        detectionOutputs.sort { lhs, rhs in
            let ls = lhs["sourceIndex"] as! Int
            let rs = rhs["sourceIndex"] as! Int
            if ls != rs { return ls < rs }
            let lt = lhs["timeSeconds"] as! Double
            let rt = rhs["timeSeconds"] as! Double
            return lt < rt
        }

        let output: [String: Any] = [
            "modelVersion": GolfModelVersion.current.rawValue,
            "candidates": candidateOutputs,
            "detections": detectionOutputs,
            "sourceCoverage": sourceCoverageOutputs,
            "invalidSampleCount": invalidSampleCount,
        ]

        guard JSONSerialization.isValidJSONObject(output) else { fail(4) }
        do {
            let outData = try JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
            FileHandle.standardOutput.write(outData)
            FileHandle.standardOutput.write("\n".data(using: .utf8)!)
        } catch {
            fail(4)
        }
    }
}
