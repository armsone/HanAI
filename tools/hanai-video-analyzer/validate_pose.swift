import Foundation

struct PoseCase: Decodable {
    let id: String
    let label: String
    let samples: [PoseSample]
}
struct PoseSample: Decodable {
    let time: Double
    let handX: Double
    let handY: Double
    let coreX: Double
    let coreY: Double
    let bodyScale: Double
    let confidence: Double
}
struct PoseFile: Decodable { let cases: [PoseCase] }

@main
struct ValidatePose {
    static func main() throws {
        let path = CommandLine.arguments[1]
        let file = try JSONDecoder().decode(PoseFile.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
        var results: [String: (total: Int, confirmed: Int)] = [:]
        for item in file.cases {
            var confirmed = false
            for start in item.samples.indices {
                var analyzer = GolfPuttStrokeAnalyzer()
                for raw in item.samples.dropFirst(start) {
                    let signal = analyzer.observe(GolfSwingPoseSample(time: raw.time, handX: raw.handX, handY: raw.handY,
                                                                       coreX: raw.coreX, coreY: raw.coreY,
                                                                       bodyScale: raw.bodyScale, confidence: raw.confidence))
                    if signal.isConfirmedStroke { confirmed = true; break }
                }
                if confirmed { break }
            }
            let old = results[item.label] ?? (0, 0)
            results[item.label] = (old.total + 1, old.confirmed + (confirmed ? 1 : 0))
        }
        for key in results.keys.sorted() {
            let value = results[key]!
            print("\(key): \(value.confirmed)/\(value.total) confirmed")
        }
    }
}
