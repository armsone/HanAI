import AVFoundation
import Foundation
import Vision

struct Candidate: Decodable {
    let id: String
    let sourceIndex: Int
    let timeSeconds: Double
    let label: String?
    let reviewed: Bool
}

struct WorkFile: Decodable { let candidates: [Candidate] }

func point(_ observation: VNHumanBodyPoseObservation, _ joint: VNHumanBodyPoseObservation.JointName) -> (Double, Double, Double)? {
    guard let p = try? observation.recognizedPoint(joint), p.confidence >= 0.35 else { return nil }
    return (Double(p.location.x), Double(p.location.y), Double(p.confidence))
}

func average(_ values: [(Double, Double, Double)]) -> (Double, Double, Double)? {
    guard !values.isEmpty else { return nil }
    return (values.map(\.0).reduce(0, +) / Double(values.count),
            values.map(\.1).reduce(0, +) / Double(values.count),
            values.map(\.2).reduce(0, +) / Double(values.count))
}

func extract(asset: AVAsset, center: Double, stepSeconds: Double) -> [[String: Any]] {
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.requestedTimeToleranceBefore = .zero
    generator.requestedTimeToleranceAfter = .zero
    var samples: [[String: Any]] = []
    for offset in stride(from: -3.0, through: 3.0, by: stepSeconds) {
        let time = max(0, center + offset)
        do {
            let image = try generator.copyCGImage(at: CMTime(seconds: time, preferredTimescale: 600), actualTime: nil)
            let request = VNDetectHumanBodyPoseRequest()
            try VNImageRequestHandler(cgImage: image, orientation: .up).perform([request])
            guard let observation = request.results?.first else { continue }
            let wrists = [
                point(observation, .leftWrist), point(observation, .rightWrist)
            ].compactMap { $0 }
            let shoulders = [
                point(observation, .leftShoulder), point(observation, .rightShoulder)
            ].compactMap { $0 }
            let hips = [
                point(observation, .leftHip), point(observation, .rightHip)
            ].compactMap { $0 }
            guard let hand = average(wrists), let shoulder = average(shoulders), let hip = average(hips) else { continue }
            let scale = hypot(shoulder.0 - (point(observation, .rightShoulder)?.0 ?? shoulder.0),
                              shoulder.1 - (point(observation, .rightShoulder)?.1 ?? shoulder.1))
            let bodyScale = max(0.04, scale * 2.0)
            let confidence = min(hand.2, min(shoulder.2, hip.2))
            samples.append([
                "time": time - center,
                "handX": hand.0, "handY": hand.1,
                "coreX": (shoulder.0 + hip.0) / 2.0,
                "coreY": (shoulder.1 + hip.1) / 2.0,
                "bodyScale": bodyScale,
                "confidence": confidence
            ])
        } catch { continue }
    }
    return samples
}

let args = CommandLine.arguments
guard args.count >= 2 else { fatalError("usage: extract_pose.swift WORK_DIR [STEP_SECONDS]") }
let work = URL(fileURLWithPath: args[1])
let stepSeconds = args.count >= 3 ? (Double(args[2]) ?? 0.1) : 0.1
let requestedLabels = args.count >= 4 ? Set(args[3].split(separator: ",").map(String.init)) : nil
let sources = try JSONDecoder().decode([String: [String]].self, from: Data(contentsOf: work.appendingPathComponent("runtime-sources.json"))) ["paths"]!
let review = try JSONDecoder().decode(WorkFile.self, from: Data(contentsOf: work.appendingPathComponent("putter-review.json")))
let base = try JSONDecoder().decode(WorkFile.self, from: Data(contentsOf: work.appendingPathComponent("analysis.json")))
var cases: [[String: Any]] = []
var seen = Set<String>()
let allCandidates = base.candidates + review.candidates
for candidate in allCandidates where !seen.contains("\(candidate.sourceIndex)-\(candidate.timeSeconds)-\(candidate.label ?? "unreviewed")") {
    if let requestedLabels, !requestedLabels.contains(candidate.label ?? "") { continue }
    seen.insert("\(candidate.sourceIndex)-\(candidate.timeSeconds)-\(candidate.label ?? "unreviewed")")
    let asset = AVAsset(url: URL(fileURLWithPath: sources[candidate.sourceIndex]))
    let label: String
    switch candidate.label {
    case "putter": label = "putter-positive"
    case "nonPutter": label = "putter-negative"
    case "non-shot": label = "non-shot-negative"
    case "driver": label = "driver-positive"
    case "wood": label = "wood-positive"
    case "iron": label = "iron-positive"
    case "wedge": label = "wedge-positive"
    default: label = "unreviewed"
    }
    cases.append([
        "id": candidate.id,
        "label": label,
        "samples": extract(asset: asset, center: candidate.timeSeconds, stepSeconds: stepSeconds)
    ])
    print("processed=\(cases.count)/\(allCandidates.count)")
}
let output: [String: Any] = [
    "schemaVersion": 1,
    "kind": "golfShotObservedPose",
    "source": "local screen-golf clips; numeric pose summary only",
    "sampleIntervalSeconds": stepSeconds,
    "cases": cases
]
let data = try JSONSerialization.data(withJSONObject: output, options: [.prettyPrinted, .sortedKeys])
let stepText = String(format: "%.1f", stepSeconds)
let outputName = stepSeconds == 0.5 ? "putter-pose-observed.json" : "putter-pose-observed-\(stepText).json"
try data.write(to: work.appendingPathComponent(outputName))
print("cases=\(cases.count)")
