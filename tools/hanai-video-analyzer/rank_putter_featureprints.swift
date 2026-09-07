import AVFoundation
import Foundation
import Vision

struct Sources: Decodable { let paths: [String] }
struct Candidate: Decodable {
    let sourceIndex: Int
    let timeSeconds: Double
    let roiMotion: Double?
    let matMotion: Double?
    let audioPeakDb: Double?
}
struct CompositeFile: Decodable { let selected: [Candidate] }
struct CandidateFile: Decodable { let candidates: [LabeledCandidate] }
struct LabeledCandidate: Decodable {
    let id: String?
    let sourceIndex: Int
    let timeSeconds: Double
    let label: String?
}
struct LabelHistoryEvent: Decodable {
    let id: String
    let label: String
}
struct Ranked: Codable {
    let sourceIndex: Int
    let timeSeconds: Double
    let positiveDistance: Float
    let negativeDistance: Float
    let score: Float
    let roiMotion: Double?
    let matMotion: Double?
    let audioPeakDb: Double?
}
struct Reference {
    let candidate: LabeledCandidate
    let prints: [VNFeaturePrintObservation]
}

func feature(asset: AVAsset, time: Double, cropMode: String) throws -> VNFeaturePrintObservation {
    let generator = AVAssetImageGenerator(asset: asset)
    generator.appliesPreferredTrackTransform = true
    generator.requestedTimeToleranceBefore = CMTime(seconds: 0.2, preferredTimescale: 600)
    generator.requestedTimeToleranceAfter = CMTime(seconds: 0.2, preferredTimescale: 600)
    let image = try generator.copyCGImage(
        at: CMTime(seconds: max(0, time), preferredTimescale: 600), actualTime: nil
    )
    let sourceImage: CGImage
    if cropMode == "lower" {
        let width = CGFloat(image.width)
        let height = CGFloat(image.height)
        sourceImage = image.cropping(to: CGRect(
            x: width * 0.08, y: height * 0.35,
            width: width * 0.84, height: height * 0.62
        )) ?? image
    } else if cropMode == "action" {
        let width = CGFloat(image.width)
        let height = CGFloat(image.height)
        sourceImage = image.cropping(to: CGRect(
            x: width * 0.12, y: height * 0.20,
            width: width * 0.76, height: height * 0.72
        )) ?? image
    } else {
        sourceImage = image
    }
    let request = VNGenerateImageFeaturePrintRequest()
    try VNImageRequestHandler(cgImage: sourceImage, orientation: .up).perform([request])
    guard let result = request.results?.first as? VNFeaturePrintObservation else {
        throw NSError(domain: "HanAIPutterFeaturePrint", code: 1)
    }
    return result
}

func readCandidates(_ path: URL) throws -> [LabeledCandidate] {
    guard FileManager.default.fileExists(atPath: path.path) else { return [] }
    return try JSONDecoder().decode(CandidateFile.self, from: Data(contentsOf: path)).candidates
}

func readLatestLabeledCandidates(_ work: URL) throws -> [LabeledCandidate] {
    var latest: [String: String] = [:]
    for name in ["label-history.jsonl", "putter-label-history.jsonl", "putter-engine-label-history.jsonl"] {
        let path = work.appendingPathComponent(name)
        guard let text = try? String(contentsOf: path, encoding: .utf8) else { continue }
        for line in text.split(separator: "\n") {
            guard let data = line.data(using: .utf8),
                  let event = try? JSONDecoder().decode(LabelHistoryEvent.self, from: data)
            else { continue }
            latest[event.id] = event.label
        }
    }
let names = [
    "analysis.json",
    "putter-final-30.json",
    "putter-review.json",
    "putter-engine-review-30.json",
    "putter-engine-review-uncertain-15.json",
]
    var byID: [String: LabeledCandidate] = [:]
    for name in names {
        for candidate in try readCandidates(work.appendingPathComponent(name)) {
            guard let id = candidate.id else { continue }
            let label = latest[id] ?? candidate.label
            byID[id] = LabeledCandidate(
                id: id,
                sourceIndex: candidate.sourceIndex,
                timeSeconds: candidate.timeSeconds,
                label: label
            )
        }
    }
    return byID.values.sorted {
        ($0.sourceIndex, $0.timeSeconds, $0.id ?? "") <
        ($1.sourceIndex, $1.timeSeconds, $1.id ?? "")
    }
}

func distance(_ lhs: VNFeaturePrintObservation, _ rhs: VNFeaturePrintObservation) -> Float {
    var value: Float = 0
    try? lhs.computeDistance(&value, to: rhs)
    return value
}

func sequenceDistance(_ lhs: [VNFeaturePrintObservation], _ rhs: [VNFeaturePrintObservation]) -> Float {
    guard !lhs.isEmpty, !rhs.isEmpty else { return .greatestFiniteMagnitude }
    var best = Float.greatestFiniteMagnitude
    for shift in -1...1 {
        var values: [Float] = []
        for index in lhs.indices {
            let otherIndex = index + shift
            guard rhs.indices.contains(otherIndex) else { continue }
            values.append(distance(lhs[index], rhs[otherIndex]))
        }
        if !values.isEmpty {
            best = min(best, values.reduce(0, +) / Float(values.count))
        }
    }
    return best
}

func uniqueCandidates(_ candidates: [LabeledCandidate]) -> [LabeledCandidate] {
    var seen = Set<String>()
    return candidates.filter { candidate in
        let key = "\(candidate.sourceIndex):\(candidate.timeSeconds)"
        return seen.insert(key).inserted
    }
}

let work = URL(fileURLWithPath: CommandLine.arguments[1])
let output = URL(fileURLWithPath: CommandLine.arguments[2])
let cropMode = CommandLine.arguments.count > 3 ? CommandLine.arguments[3] : "full"
let quietOnly = CommandLine.arguments.contains("--quiet")
let matTieBreak = CommandLine.arguments.contains("--mat")
let allNegativeReferences = CommandLine.arguments.contains("--all-negative")
let negativeMinimumGap = CommandLine.arguments
    .first(where: { $0.hasPrefix("--negative-gap=") })
    .flatMap { Double($0.dropFirst("--negative-gap=".count)) }
    .map { max(0, $0) } ?? 0
let wideTemporalSequence = CommandLine.arguments.contains("--wide-sequence")
let allowNearReferences = CommandLine.arguments.contains("--allow-near-labels")
let blockHoldoutCount = CommandLine.arguments
    .first(where: { $0.hasPrefix("--block-holdout=") })
    .flatMap { Int($0.dropFirst("--block-holdout=".count)) }
    .map { max(0, $0) } ?? 0
let blockQuarantineSeconds = CommandLine.arguments
    .first(where: { $0.hasPrefix("--block-quarantine=") })
    .flatMap { Double($0.dropFirst("--block-quarantine=".count)) }
    .map { max(0, $0) } ?? 0
let robustTopK = CommandLine.arguments
    .first(where: { $0.hasPrefix("--topk=") })
    .flatMap { Int($0.dropFirst("--topk=".count)) }
    .map { max(1, $0) }
let positiveTopK = CommandLine.arguments
    .first(where: { $0.hasPrefix("--positive-topk=") })
    .flatMap { Int($0.dropFirst("--positive-topk=".count)) }
    .map { max(1, $0) } ?? robustTopK
let negativeTopK = CommandLine.arguments
    .first(where: { $0.hasPrefix("--negative-topk=") })
    .flatMap { Int($0.dropFirst("--negative-topk=".count)) }
    .map { max(1, $0) } ?? robustTopK
let compositeName = CommandLine.arguments
    .first(where: { $0.hasPrefix("--composite=") })
    .map { String($0.dropFirst("--composite=".count)) }
    ?? "full-pose-putter-composite-0.2.json"
let sources = try JSONDecoder().decode(
    Sources.self,
    from: Data(contentsOf: work.appendingPathComponent("runtime-sources.json"))
).paths
let composite = try JSONDecoder().decode(
    CompositeFile.self,
    from: Data(contentsOf: work.appendingPathComponent(compositeName))
).selected
let labeledCandidates = try readLatestLabeledCandidates(work)
// Always apply the latest human decision to the current reference files. The
// old file-local labels can lag behind label-history.jsonl after a re-review.
// `--history` is retained as an explicit compatibility flag; both paths now
// use the same corrected current-label view.
let referenceCandidates = labeledCandidates
let assets = sources.map { AVAsset(url: URL(fileURLWithPath: $0)) }
let sourceDurations = assets.map { asset in
    asset.duration.seconds.isFinite ? asset.duration.seconds : 0.0
}
let frameOffsets = wideTemporalSequence
    ? [-2.0, -1.0, 0.0, 1.0, 2.0]
    : [-1.0, 0.0, 1.0]

let explicitPositiveCandidates = uniqueCandidates(referenceCandidates.filter { $0.label == "putter" })
let labeledNonShotCandidates = uniqueCandidates(referenceCandidates.filter { $0.label == "non-shot" })
let labeledNonPutterCandidates = uniqueCandidates(referenceCandidates.filter { $0.label == "nonPutter" })
// A non-shot label close to a confirmed putter is context from the same
// putting sequence (address, follow-through, ball check), not a negative
// visual example. Treat it as positive context and keep unrelated non-shots
// as negatives.
let putterContextWindow = 12.0
let contextualPutterCandidates = labeledNonShotCandidates.filter { candidate in
    explicitPositiveCandidates.contains { positive in
        positive.sourceIndex == candidate.sourceIndex
            && abs(positive.timeSeconds - candidate.timeSeconds) <= putterContextWindow
    }
}
let positiveCandidates = uniqueCandidates(explicitPositiveCandidates + contextualPutterCandidates)
let negativeCandidates = uniqueCandidates(labeledNonShotCandidates.filter { candidate in
    !contextualPutterCandidates.contains { context in
        context.id == candidate.id
    }
} + labeledNonPutterCandidates)
var positiveReferences: [Reference] = []
var negativeReferences: [Reference] = []
for candidate in positiveCandidates {
    let prints = frameOffsets.compactMap { try? feature(asset: assets[candidate.sourceIndex], time: candidate.timeSeconds + $0, cropMode: cropMode) }
    positiveReferences.append(Reference(candidate: candidate, prints: prints))
}
let sortedNegativeCandidates = negativeCandidates.sorted {
    ($0.sourceIndex, $0.timeSeconds) < ($1.sourceIndex, $1.timeSeconds)
}
let spacedNegativeCandidates: [LabeledCandidate] = {
    guard negativeMinimumGap > 0 else { return sortedNegativeCandidates }
    var result: [LabeledCandidate] = []
    var lastTimeBySource: [Int: Double] = [:]
    for candidate in sortedNegativeCandidates {
        let last = lastTimeBySource[candidate.sourceIndex] ?? -.greatestFiniteMagnitude
        guard candidate.timeSeconds - last >= negativeMinimumGap else { continue }
        result.append(candidate)
        lastTimeBySource[candidate.sourceIndex] = candidate.timeSeconds
    }
    return result
}()
let negativeLimit = allNegativeReferences
    ? spacedNegativeCandidates.count
    : min(80, sortedNegativeCandidates.count)
let negativeReferenceCandidates: [LabeledCandidate]
if negativeLimit == 0 {
    negativeReferenceCandidates = []
} else {
    negativeReferenceCandidates = (0..<negativeLimit).map { index in
        let pool = allNegativeReferences ? spacedNegativeCandidates : sortedNegativeCandidates
        let position = negativeLimit == 1 ? 0 :
            index * (pool.count - 1) / (negativeLimit - 1)
        return pool[position]
    }
}
for candidate in negativeReferenceCandidates {
    let prints = frameOffsets.compactMap { try? feature(asset: assets[candidate.sourceIndex], time: candidate.timeSeconds + $0, cropMode: cropMode) }
    negativeReferences.append(Reference(candidate: candidate, prints: prints))
}

var ranked: [Ranked] = []
for candidate in composite {
    if quietOnly && (candidate.audioPeakDb ?? 0.0) > -10.0 { continue }
    let currentPrints = frameOffsets.compactMap { try? feature(asset: assets[candidate.sourceIndex], time: candidate.timeSeconds + $0, cropMode: cropMode) }
    guard !currentPrints.isEmpty, !positiveReferences.isEmpty, !negativeReferences.isEmpty
    else { continue }
    func isInSameHoldoutBlock(_ reference: Reference) -> Bool {
        guard blockHoldoutCount > 0,
              reference.candidate.sourceIndex == candidate.sourceIndex,
              sourceDurations[candidate.sourceIndex] > 0
        else { return false }
        let duration = sourceDurations[candidate.sourceIndex]
        let blockLength = duration / Double(blockHoldoutCount)
        let candidateBlock = min(blockHoldoutCount - 1, max(0, Int(candidate.timeSeconds / blockLength)))
        let referenceBlock = min(blockHoldoutCount - 1, max(0, Int(reference.candidate.timeSeconds / blockLength)))
        if candidateBlock == referenceBlock { return true }
        guard abs(candidateBlock - referenceBlock) == 1, blockQuarantineSeconds > 0 else { return false }
        let blockStart = Double(candidateBlock) * blockLength
        let blockEnd = Double(candidateBlock + 1) * blockLength
        let referenceStart = Double(referenceBlock) * blockLength
        let referenceEnd = Double(referenceBlock + 1) * blockLength
        let nearBoundary = abs(candidate.timeSeconds - blockStart) <= blockQuarantineSeconds
            || abs(blockEnd - candidate.timeSeconds) <= blockQuarantineSeconds
            || abs(reference.candidate.timeSeconds - referenceStart) <= blockQuarantineSeconds
            || abs(referenceEnd - reference.candidate.timeSeconds) <= blockQuarantineSeconds
        return nearBoundary
    }
    var positiveDistances: [Float] = []
    var negativeDistances: [Float] = []
    for reference in positiveReferences
        where !isInSameHoldoutBlock(reference)
            && (allowNearReferences || reference.candidate.sourceIndex != candidate.sourceIndex
                || abs(reference.candidate.timeSeconds - candidate.timeSeconds) > 6.0) {
        positiveDistances.append(sequenceDistance(currentPrints, reference.prints))
    }
    for reference in negativeReferences
        where !isInSameHoldoutBlock(reference)
            && (allowNearReferences || reference.candidate.sourceIndex != candidate.sourceIndex
                || abs(reference.candidate.timeSeconds - candidate.timeSeconds) > 6.0) {
        negativeDistances.append(sequenceDistance(currentPrints, reference.prints))
    }
    guard !positiveDistances.isEmpty, !negativeDistances.isEmpty else { continue }
    let positiveDistance: Float
    let negativeDistance: Float
    if positiveTopK != nil || negativeTopK != nil {
        if let positiveTopK {
            positiveDistance = positiveDistances.sorted().prefix(positiveTopK).reduce(0, +)
                / Float(min(positiveTopK, positiveDistances.count))
        } else {
            positiveDistance = positiveDistances.min() ?? .greatestFiniteMagnitude
        }
        if let negativeTopK {
            negativeDistance = negativeDistances.sorted().prefix(negativeTopK).reduce(0, +)
                / Float(min(negativeTopK, negativeDistances.count))
        } else {
            negativeDistance = negativeDistances.min() ?? .greatestFiniteMagnitude
        }
    } else {
        positiveDistance = positiveDistances.min() ?? .greatestFiniteMagnitude
        negativeDistance = negativeDistances.min() ?? .greatestFiniteMagnitude
    }
    let audioPenalty = max(0, ((candidate.audioPeakDb ?? -40.0) + 3.0) / 3.0)
    let roiPenalty = max(0, ((candidate.roiMotion ?? 0.0) - 10.0) / 10.0)
    let matMotion = candidate.matMotion ?? 0.0
    let matScore = matMotion > 0 ? exp(-pow((matMotion - 6.5) / 2.8, 2)) : 0.0
    let matPenalty = max(0, (matMotion - 10.0) / 10.0)
    // Keep mat motion for diagnostics. The full-video holdout test showed that
    // using it as a ranking term reduced putter recall, so it is not scored yet.
    _ = matScore
    _ = matPenalty
    ranked.append(Ranked(
        sourceIndex: candidate.sourceIndex,
        timeSeconds: candidate.timeSeconds,
        positiveDistance: positiveDistance,
        negativeDistance: negativeDistance,
        score: negativeDistance - positiveDistance
            - Float(0.10 * audioPenalty + (matTieBreak ? 0.20 : 0.05) * roiPenalty)
            + Float(matTieBreak ? 0.10 * matScore - 0.0 * matPenalty : 0.0),
        roiMotion: candidate.roiMotion,
        matMotion: candidate.matMotion,
        audioPeakDb: candidate.audioPeakDb
    ))
}
ranked.sort { $0.score > $1.score }
try JSONEncoder().encode(ranked).write(to: output)
print("references positive=\(positiveReferences.count) negative=\(negativeReferences.count) ranked=\(ranked.count)")
