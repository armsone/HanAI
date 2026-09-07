import AVFoundation
import Foundation
import Vision

struct SourceList: Decodable { let paths: [String] }

func point(_ observation: VNHumanBodyPoseObservation, _ joint: VNHumanBodyPoseObservation.JointName) -> (Double, Double, Double)? {
    guard let p = try? observation.recognizedPoint(joint), p.confidence >= 0.35 else { return nil }
    return (Double(p.location.x), Double(p.location.y), Double(p.confidence))
}

func average(_ values: [(Double, Double, Double)]) -> (Double, Double, Double)? {
    guard !values.isEmpty else { return nil }
    return (
        values.map(\.0).reduce(0, +) / Double(values.count),
        values.map(\.1).reduce(0, +) / Double(values.count),
        values.map(\.2).reduce(0, +) / Double(values.count)
    )
}

let args = CommandLine.arguments
guard args.count >= 2 else { fatalError("usage: scan_full_pose.swift WORK_DIR [STEP_SECONDS]") }
let work = URL(fileURLWithPath: args[1])
let stepSeconds = args.count >= 3 ? (Double(args[2]) ?? 0.2) : 0.2
let sources = try JSONDecoder().decode(
    SourceList.self,
    from: Data(contentsOf: work.appendingPathComponent("runtime-sources.json"))
).paths
var rows: [[String: Any]] = []

for (sourceIndex, path) in sources.enumerated() {
    let asset = AVAsset(url: URL(fileURLWithPath: path))
    let duration = asset.duration.seconds
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.requestedTimeToleranceBefore = CMTime(seconds: 0.05, preferredTimescale: 600)
    generator.requestedTimeToleranceAfter = CMTime(seconds: 0.05, preferredTimescale: 600)
    var time = 0.0
    var count = 0
    while time < duration {
        defer { time += stepSeconds }
        do {
            let image = try generator.copyCGImage(
                at: CMTime(seconds: time, preferredTimescale: 600), actualTime: nil
            )
            let request = VNDetectHumanBodyPoseRequest()
            try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request])
            guard let observation = request.results?.first else { continue }
            let hands = [point(observation, .leftWrist), point(observation, .rightWrist)].compactMap { $0 }
            let shoulders = [point(observation, .leftShoulder), point(observation, .rightShoulder)].compactMap { $0 }
            let hips = [point(observation, .leftHip), point(observation, .rightHip)].compactMap { $0 }
            guard let hand = average(hands), let shoulder = average(shoulders), let hip = average(hips) else { continue }
            let bodyScale = max(0.04, hypot(shoulder.0 - hip.0, shoulder.1 - hip.1))
            rows.append([
                "sourceIndex": sourceIndex,
                "time": time,
                "handX": hand.0,
                "handY": hand.1,
                "coreX": (shoulder.0 + hip.0) / 2.0,
                "coreY": (shoulder.1 + hip.1) / 2.0,
                "bodyScale": bodyScale,
                "confidence": min(hand.2, min(shoulder.2, hip.2))
            ])
            count += 1
        } catch { continue }
        if count % 100 == 0 {
            let currentText = String(format: "%.1f", time)
            let durationText = String(format: "%.1f", duration)
            print("source=\(sourceIndex) time=\(currentText)/\(durationText) samples=\(count)")
        }
    }
}

let output: [String: Any] = [
    "schemaVersion": 1,
    "kind": "golfFullSessionPoseSamples",
    "sampleIntervalSeconds": stepSeconds,
    "samples": rows
]
let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
let stepText = String(format: "%.1f", stepSeconds)
let outputName = stepSeconds == 0.5 ? "full-pose-samples.json" : "full-pose-samples-\(stepText).json"
try data.write(to: work.appendingPathComponent(outputName))
print("samples=\(rows.count)")
