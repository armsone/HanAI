import Foundation

let sourceSHA256 = "7314788d937aad48dca2688b3c15df9633d27fcac61b562a870001732f086e28"
let instrumentedSHA256 = "72d3b761b367e71a431996d3adce7064085f79539f2a270beb13ca67bc97119f"

let maxStdinBytes = 128 * 1024 * 1024
let maxClips = 10_000
let maxFramesPerClip = 100_000

func fail(_ message: String) -> Never {
    FileHandle.standardError.write("error\n".data(using: .utf8)!)
    exit(1)
}

func isFiniteNumber(_ value: Any?) -> Double? {
    guard let num = value as? NSNumber else { return nil }
    if CFGetTypeID(num) == CFBooleanGetTypeID() { return nil }
    let d = num.doubleValue
    return d.isFinite ? d : nil
}

struct InputFrame {
    let time: Double
    let rms: Double
    let peak: Double
    let crossingRate: Double
}

struct InputClip {
    let clipId: String
    let duration: Double
    let frames: [InputFrame]
}

func readAllStdin() -> Data {
    var data = Data()
    let handle = FileHandle.standardInput
    while true {
        let chunk = handle.availableData
        if chunk.isEmpty { break }
        data.append(chunk)
        if data.count > maxStdinBytes { fail("input too large") }
    }
    return data
}

struct OutputAnchor: Encodable {
    let timeSeconds: Double
    let score: Double
}

struct OutputClip: Encodable {
    let clipId: String
    let modelVersion: String
    let rankedAnchors: [OutputAnchor]
}

struct Provenance: Encodable {
    let actualLegacySource: Bool
    let sourceSHA256: String
    let instrumentedSHA256: String
    let instrumentation: String
    let semantics: String
}

struct OutputRoot: Encodable {
    let schemaVersion: Int
    let clips: [OutputClip]
    let provenance: Provenance
}

@main
struct LegacyVideoTool {
    static func main() {
        let rawData = readAllStdin()
        guard !rawData.isEmpty else { fail("no input") }

        guard let rootAny = try? JSONSerialization.jsonObject(with: rawData, options: []),
              let root = rootAny as? [String: Any]
        else { fail("malformed input") }

        var requestedLimit = 12
        if let limitAny = root["limit"] {
            guard let limitDouble = isFiniteNumber(limitAny),
                  limitDouble == limitDouble.rounded(),
                  limitDouble >= 0, limitDouble <= 2000
            else { fail("invalid limit") }
            requestedLimit = Int(limitDouble)
        }

        guard let clipsAny = root["clips"] as? [Any] else { fail("missing clips") }
        guard clipsAny.count <= maxClips else { fail("too many clips") }

        var clips: [InputClip] = []
        var seenClipIds = Set<String>()

        for clipAny in clipsAny {
            guard let clipObj = clipAny as? [String: Any] else { fail("malformed clip") }
            guard let clipId = clipObj["clipId"] as? String, !clipId.isEmpty else { fail("missing clipId") }
            guard !seenClipIds.contains(clipId) else { fail("duplicate clipId") }
            seenClipIds.insert(clipId)

            guard let durationAny = clipObj["duration"],
                  let duration = isFiniteNumber(durationAny),
                  duration > 0
            else { fail("invalid duration") }

            guard let framesAny = clipObj["frames"] as? [Any] else { fail("missing frames") }
            guard framesAny.count <= maxFramesPerClip else { fail("too many frames") }

            var frames: [InputFrame] = []
            var lastTime: Double? = nil
            for frameAny in framesAny {
                guard let frameObj = frameAny as? [String: Any] else { fail("malformed frame") }
                guard let timeAny = frameObj["time"], let time = isFiniteNumber(timeAny),
                      time >= 0, time <= duration
                else { fail("invalid frame time") }
                guard let rmsAny = frameObj["rms"], let rms = isFiniteNumber(rmsAny),
                      rms >= 0, rms <= 1
                else { fail("invalid rms") }
                guard let peakAny = frameObj["peak"], let peak = isFiniteNumber(peakAny),
                      peak >= 0, peak <= 1
                else { fail("invalid peak") }
                guard let crAny = frameObj["zeroCrossingRate"], let cr = isFiniteNumber(crAny),
                      cr >= 0, cr <= 1
                else { fail("invalid zeroCrossingRate") }

                if let lastTime, time < lastTime { fail("frame times not nondecreasing") }
                lastTime = time

                frames.append(InputFrame(time: time, rms: rms, peak: peak, crossingRate: cr))
            }

            clips.append(InputClip(clipId: clipId, duration: duration, frames: frames))
        }

        var outputClips: [OutputClip] = []

        for clip in clips {
            if requestedLimit == 0 {
                outputClips.append(OutputClip(clipId: clip.clipId, modelVersion: "0.7.0", rankedAnchors: []))
                continue
            }

            let audioFrames = clip.frames.map {
                AudioImpactFrame(
                    time: $0.time,
                    metrics: AudioImpactMetrics(rms: $0.rms, peak: $0.peak, crossingRate: $0.crossingRate)
                )
            }

            let ranked = AudioImpactClassifier.rankedImpactTimes(
                frames: audioFrames,
                duration: clip.duration,
                limit: requestedLimit
            )

            let anchors = ranked.map { OutputAnchor(timeSeconds: $0.time, score: $0.score) }
            outputClips.append(OutputClip(clipId: clip.clipId, modelVersion: "0.7.0", rankedAnchors: anchors))
        }

        let output = OutputRoot(
            schemaVersion: 1,
            clips: outputClips,
            provenance: Provenance(
                actualLegacySource: true,
                sourceSHA256: sourceSHA256,
                instrumentedSHA256: instrumentedSHA256,
                instrumentation: "return-type-and-final-selected-return-only",
                semantics: "Actual legacy ranking on caller-provided common PCM feature policy; not byte-identical original decoder"
            )
        )

        let encoder = JSONEncoder()
        guard let outData = try? encoder.encode(output) else { fail("encode failure") }
        FileHandle.standardOutput.write(outData)
    }
}
