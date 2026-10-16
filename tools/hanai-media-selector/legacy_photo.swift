import Foundation
import CoreFoundation

#if canImport(Darwin)
import Darwin
#elseif canImport(Glibc)
import Glibc
#endif

private func failWithSanitizedError() -> Never {
    let message = "Invalid legacy photo input.\n"
    if let data = message.data(using: .utf8) {
        FileHandle.standardError.write(data)
    }
    exit(1)
}

private func extractExactInt(from value: Any?) -> Int? {
    guard let number = value as? NSNumber else { return nil }
    if CFGetTypeID(number) == CFBooleanGetTypeID() {
        return nil
    }
    var decimalValue = number.decimalValue
    guard decimalValue >= Decimal(Int.min), decimalValue <= Decimal(Int.max) else {
        return nil
    }
    var roundedValue = Decimal()
    NSDecimalRound(&roundedValue, &decimalValue, 0, .plain)
    guard roundedValue == decimalValue else {
        return nil
    }
    return Int(number.int64Value)
}

private func extractFiniteDouble(from value: Any?) -> Double? {
    guard let number = value as? NSNumber else { return nil }
    if CFGetTypeID(number) == CFBooleanGetTypeID() {
        return nil
    }
    let d = number.doubleValue
    guard d.isFinite else { return nil }
    return d
}

private struct CandidateInput {
    let index: Int
    let sharpness: Double
    let exposureScore: Double
    let pixelCount: Int
    let aestheticsScore: Double?
}

private struct DistanceInput {
    let firstIndex: Int
    let secondIndex: Int
    let distance: Double
}

private struct PairQueryInput {
    let firstIndex: Int
    let secondIndex: Int
}

private struct UnorderedPair: Hashable {
    let low: Int
    let high: Int
    init(_ a: Int, _ b: Int) {
        low = min(a, b)
        high = max(a, b)
    }
}

@main
struct LegacyPhotoEvaluationAdapter {
    static func main() {
        let inputData: Data
        do {
            inputData = try FileHandle.standardInput.readToEnd() ?? Data()
        } catch {
            failWithSanitizedError()
        }

        guard !inputData.isEmpty else {
            failWithSanitizedError()
        }

        let jsonObject: Any
        do {
            jsonObject = try JSONSerialization.jsonObject(with: inputData, options: [])
        } catch {
            failWithSanitizedError()
        }

        guard let rootDict = jsonObject as? [String: Any] else {
            failWithSanitizedError()
        }

        guard let rawCandidates = rootDict["candidates"] as? [Any],
              let rawDistances = rootDict["distances"] as? [Any],
              let rawN = rootDict["n"],
              let rawThreshold = rootDict["threshold"] else {
            failWithSanitizedError()
        }

        guard let n = extractExactInt(from: rawN), n >= 0 else {
            failWithSanitizedError()
        }

        guard let threshold = extractFiniteDouble(from: rawThreshold) else {
            failWithSanitizedError()
        }

        var candidates: [CandidateInput] = []
        var candidateIndicesSet = Set<Int>()

        for rawCandidate in rawCandidates {
            guard let candDict = rawCandidate as? [String: Any] else {
                failWithSanitizedError()
            }

            guard let rawIndex = candDict["index"],
                  let rawSharpness = candDict["sharpness"],
                  let rawExposure = candDict["exposureScore"],
                  let rawPixelCount = candDict["pixelCount"] else {
                failWithSanitizedError()
            }

            guard let index = extractExactInt(from: rawIndex), index >= 0 else {
                failWithSanitizedError()
            }

            if candidateIndicesSet.contains(index) {
                failWithSanitizedError()
            }
            candidateIndicesSet.insert(index)

            guard let sharpness = extractFiniteDouble(from: rawSharpness),
                  let exposureScore = extractFiniteDouble(from: rawExposure),
                  let pixelCount = extractExactInt(from: rawPixelCount), pixelCount >= 0 else {
                failWithSanitizedError()
            }

            var aestheticsScore: Double? = nil
            if let rawAesthetics = candDict["aestheticsScore"] {
                if !(rawAesthetics is NSNull) {
                    guard let aestheticVal = extractFiniteDouble(from: rawAesthetics) else {
                        failWithSanitizedError()
                    }
                    aestheticsScore = aestheticVal
                }
            }

            candidates.append(CandidateInput(
                index: index,
                sharpness: sharpness,
                exposureScore: exposureScore,
                pixelCount: pixelCount,
                aestheticsScore: aestheticsScore
            ))
        }

        var distances: [DistanceInput] = []
        var seenDistancePairs = Set<UnorderedPair>()

        for rawDistance in rawDistances {
            guard let distDict = rawDistance as? [String: Any] else {
                failWithSanitizedError()
            }

            guard let rawFirst = distDict["firstIndex"],
                  let rawSecond = distDict["secondIndex"],
                  let rawDistanceVal = distDict["distance"] else {
                failWithSanitizedError()
            }

            guard let firstIndex = extractExactInt(from: rawFirst),
                  let secondIndex = extractExactInt(from: rawSecond),
                  let distance = extractFiniteDouble(from: rawDistanceVal) else {
                failWithSanitizedError()
            }

            guard firstIndex != secondIndex else {
                failWithSanitizedError()
            }

            guard candidateIndicesSet.contains(firstIndex), candidateIndicesSet.contains(secondIndex) else {
                failWithSanitizedError()
            }

            let pairKey = UnorderedPair(firstIndex, secondIndex)
            if seenDistancePairs.contains(pairKey) {
                failWithSanitizedError()
            }
            seenDistancePairs.insert(pairKey)

            distances.append(DistanceInput(
                firstIndex: firstIndex,
                secondIndex: secondIndex,
                distance: distance
            ))
        }

        var pairQueries: [PairQueryInput] = []
        if let rawPairQueries = rootDict["pairQueries"] {
            guard let queriesArray = rawPairQueries as? [Any] else {
                failWithSanitizedError()
            }

            for rawQuery in queriesArray {
                guard let queryDict = rawQuery as? [String: Any] else {
                    failWithSanitizedError()
                }

                guard let rawFirst = queryDict["firstIndex"],
                      let rawSecond = queryDict["secondIndex"] else {
                    failWithSanitizedError()
                }

                guard let firstIndex = extractExactInt(from: rawFirst),
                      let secondIndex = extractExactInt(from: rawSecond) else {
                    failWithSanitizedError()
                }

                guard firstIndex != secondIndex else {
                    failWithSanitizedError()
                }

                guard candidateIndicesSet.contains(firstIndex), candidateIndicesSet.contains(secondIndex) else {
                    failWithSanitizedError()
                }

                pairQueries.append(PairQueryInput(firstIndex: firstIndex, secondIndex: secondIndex))
            }
        }

        let qualityCandidates = candidates.map {
            ImageQualityCandidate(
                index: $0.index,
                sharpness: $0.sharpness,
                exposureScore: $0.exposureScore,
                pixelCount: $0.pixelCount,
                aestheticsScore: $0.aestheticsScore
            )
        }

        let ranking = ImageTopNSelector.selectTopN(candidates: qualityCandidates, n: n)
        let topNSelectedIndices = ranking.selectedIndices
        let qualityRankedIndices = ranking.rankedIndices

        let similarityCandidates = candidates.map {
            ImageSimilarityCandidate(
                index: $0.index,
                sharpness: $0.sharpness,
                pixelCount: $0.pixelCount
            )
        }

        let similarityDistances = distances.map {
            ImagePairDistance(
                firstIndex: $0.firstIndex,
                secondIndex: $0.secondIndex,
                distance: $0.distance
            )
        }

        let legacyRepresentativeIndices = ImageSimilaritySelector.representativeIndices(
            candidates: similarityCandidates,
            distances: similarityDistances,
            threshold: threshold
        )

        var candidateMap: [Int: ImageSimilarityCandidate] = [:]
        for c in similarityCandidates {
            candidateMap[c.index] = c
        }

        var predictedPhotoPairs: [[String: Any]] = []
        for query in pairQueries {
            guard let c1 = candidateMap[query.firstIndex],
                  let c2 = candidateMap[query.secondIndex] else {
                continue
            }

            let pairCandidates: [ImageSimilarityCandidate]
            if let idx1 = candidates.firstIndex(where: { $0.index == query.firstIndex }),
               let idx2 = candidates.firstIndex(where: { $0.index == query.secondIndex }) {
                if idx1 <= idx2 {
                    pairCandidates = [c1, c2]
                } else {
                    pairCandidates = [c2, c1]
                }
            } else {
                pairCandidates = [c1, c2]
            }

            let targetPair = UnorderedPair(query.firstIndex, query.secondIndex)
            let pairDistances = similarityDistances.filter {
                UnorderedPair($0.firstIndex, $0.secondIndex) == targetPair
            }

            let reps = ImageSimilaritySelector.representativeIndices(
                candidates: pairCandidates,
                distances: pairDistances,
                threshold: threshold
            )
            let isSimilar = (reps.count == 1)

            predictedPhotoPairs.append([
                "firstIndex": query.firstIndex,
                "secondIndex": query.secondIndex,
                "similar": isSimilar
            ])
        }

        let outputObject: [String: Any] = [
            "topNSelectedIndices": topNSelectedIndices,
            "qualityRankedIndices": qualityRankedIndices,
            "legacyRepresentativeIndices": legacyRepresentativeIndices,
            "predictedPhotoPairs": predictedPhotoPairs
        ]

        let outputData: Data
        do {
            outputData = try JSONSerialization.data(withJSONObject: outputObject, options: [.sortedKeys])
        } catch {
            failWithSanitizedError()
        }

        FileHandle.standardOutput.write(outputData)
        if let newline = "\n".data(using: .utf8) {
            FileHandle.standardOutput.write(newline)
        }
    }
}
