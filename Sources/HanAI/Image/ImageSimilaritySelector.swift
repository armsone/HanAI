import Foundation

/// 플랫폼 adapter가 원본 사진에서 추출한 익명 수치 특징만 받는다.
public struct ImageSimilarityCandidate: Equatable, Sendable {
    public let index: Int
    public let sharpness: Double
    public let pixelCount: Int

    public init(index: Int, sharpness: Double, pixelCount: Int) {
        self.index = index
        self.sharpness = sharpness
        self.pixelCount = pixelCount
    }
}

public struct ImagePairDistance: Equatable, Sendable {
    public let firstIndex: Int
    public let secondIndex: Int
    public let distance: Double

    public init(firstIndex: Int, secondIndex: Int, distance: Double) {
        self.firstIndex = firstIndex
        self.secondIndex = secondIndex
        self.distance = distance
    }
}

/// 가까운 중복만 제거하는 보수적 정책. 사진·Vision/CoreML 의존성은 앱 adapter에 둔다.
public enum ImageSimilaritySelector {
    public static let nearDuplicateThreshold = 0.12

    public static func representativeIndices(
        candidates: [ImageSimilarityCandidate],
        distances: [ImagePairDistance],
        threshold: Double = nearDuplicateThreshold
    ) -> [Int] {
        guard threshold >= 0, !candidates.isEmpty else { return candidates.map(\.index) }
        let candidateByIndex = Dictionary(uniqueKeysWithValues: candidates.map { ($0.index, $0) })
        var distanceMap: [PairKey: Double] = [:]
        for pair in distances where pair.distance >= 0 {
            distanceMap[PairKey(pair.firstIndex, pair.secondIndex)] = pair.distance
        }

        var representatives: [Int] = []
        for candidate in candidates {
            if let position = representatives.firstIndex(where: { representative in
                guard let distance = distanceMap[PairKey(candidate.index, representative)] else { return false }
                return distance <= threshold
            }) {
                let current = candidateByIndex[representatives[position]]!
                if qualityScore(candidate) > qualityScore(current) {
                    representatives[position] = candidate.index
                }
            } else {
                representatives.append(candidate.index)
            }
        }
        return representatives.sorted()
    }

    private static func qualityScore(_ candidate: ImageSimilarityCandidate) -> Double {
        let resolutionBonus = log(Double(max(1, candidate.pixelCount))) / 100
        return candidate.sharpness + resolutionBonus
    }

    private struct PairKey: Hashable {
        let low: Int
        let high: Int
        init(_ first: Int, _ second: Int) {
            low = min(first, second)
            high = max(first, second)
        }
    }
}
