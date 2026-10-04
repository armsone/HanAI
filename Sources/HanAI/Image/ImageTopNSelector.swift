import Foundation

/// 플랫폼 adapter가 계산한 순수 수치 품질 지표만 받는다.
/// 선명도/노출/해상도는 adapter가 산출하고, Aesthetics 점수는 iOS18+ Vision
/// (CalculateImageAestheticsScoresRequest)이 있을 때만 adapter가 채운다.
public struct ImageQualityCandidate: Equatable, Sendable {
    public let index: Int
    public let sharpness: Double
    public let exposureScore: Double
    public let pixelCount: Int
    public let aestheticsScore: Double?

    public init(
        index: Int,
        sharpness: Double,
        exposureScore: Double,
        pixelCount: Int,
        aestheticsScore: Double? = nil
    ) {
        self.index = index
        self.sharpness = sharpness
        self.exposureScore = exposureScore
        self.pixelCount = pixelCount
        self.aestheticsScore = aestheticsScore
    }
}

public struct ImageRankingResult: Equatable, Sendable {
    /// 품질 순위로 뽑힌 N장을 "입력 순서"로 되돌린 인덱스 목록.
    public let selectedIndices: [Int]
    /// 점수 내림차순(동점이면 원래 인덱스 오름차순)으로 정렬한 전체 순위.
    public let rankedIndices: [Int]
    public let scoreByIndex: [Int: Double]

    public init(
        selectedIndices: [Int],
        rankedIndices: [Int],
        scoreByIndex: [Int: Double]
    ) {
        self.selectedIndices = selectedIndices
        self.rankedIndices = rankedIndices
        self.scoreByIndex = scoreByIndex
    }
}

/// 입력으로 들어온 평가 가능한(eligible) 장수 안에서 상위 N장을 고르는 정책.
/// 중복 억제는 하지 않는다 — 결과 수는 항상 min(N, eligible candidates 수)다.
public enum ImageTopNSelector {
    public static func selectTopN(
        candidates: [ImageQualityCandidate],
        n: Int
    ) -> ImageRankingResult {
        guard n > 0, !candidates.isEmpty else {
            return ImageRankingResult(
                selectedIndices: [],
                rankedIndices: [],
                scoreByIndex: [:]
            )
        }

        let scored = candidates.map { ($0.index, qualityScore($0)) }
        let scoreByIndex = Dictionary(uniqueKeysWithValues: scored)

        let ranked = scored
            .sorted { lhs, rhs in
                if lhs.1 != rhs.1 { return lhs.1 > rhs.1 }
                return lhs.0 < rhs.0
            }
            .map(\.0)

        let topCount = min(n, ranked.count)
        let selectedSet = Set(ranked.prefix(topCount))
        let selected = candidates
            .map(\.index)
            .filter { selectedSet.contains($0) }
            .sorted()

        return ImageRankingResult(
            selectedIndices: selected,
            rankedIndices: ranked,
            scoreByIndex: scoreByIndex
        )
    }

    private static func qualityScore(_ candidate: ImageQualityCandidate) -> Double {
        let resolutionBonus = log(Double(max(1, candidate.pixelCount))) / 100
        let base = candidate.sharpness * 0.4
            + candidate.exposureScore * 0.2
            + resolutionBonus
        guard let aesthetics = candidate.aestheticsScore else { return base }
        return base * 0.4 + aesthetics * 0.6
    }
}
