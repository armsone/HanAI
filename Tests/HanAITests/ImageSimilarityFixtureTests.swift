import Foundation
import HanAI
import XCTest

final class ImageSimilarityFixtureTests: XCTestCase {
    private struct File: Decodable {
        let hanAIProductVersion: String
        let cases: [Case]
    }
    private struct Case: Decodable {
        let id: String
        let candidates: [Candidate]
        let distances: [Distance]
        let expected: [Int]
    }
    private struct Candidate: Decodable {
        let index: Int
        let sharpness: Double
        let pixelCount: Int
    }
    private struct Distance: Decodable {
        let firstIndex: Int
        let secondIndex: Int
        let distance: Double
    }

    func testSharedSimilarityFixture() throws {
        let url = Fixtures.rootDirectory.appendingPathComponent("image/similarity.json")
        let file = try JSONDecoder().decode(File.self, from: Data(contentsOf: url))
        XCTAssertEqual(file.hanAIProductVersion, HanAIVersion.product)
        for testCase in file.cases {
            let actual = ImageSimilaritySelector.representativeIndices(
                candidates: testCase.candidates.map {
                    .init(index: $0.index, sharpness: $0.sharpness, pixelCount: $0.pixelCount)
                },
                distances: testCase.distances.map {
                    .init(firstIndex: $0.firstIndex, secondIndex: $0.secondIndex, distance: $0.distance)
                }
            )
            XCTAssertEqual(actual, testCase.expected, testCase.id)
        }
    }
}
