import Foundation
import ImageIO
import Vision

struct ImageInput {
    let index: Int
    let path: String
}

func failInvalid() -> Never {
    FileHandle.standardError.write("Invalid Vision evaluation input.\n".data(using: .utf8)!)
    exit(1)
}

func parseInput(_ data: Data) -> [ImageInput] {
    guard let obj = try? JSONSerialization.jsonObject(with: data, options: []),
          let dict = obj as? [String: Any],
          let images = dict["images"] as? [Any] else {
        failInvalid()
    }

    var seenIndices = Set<Int>()
    var result: [ImageInput] = []

    for item in images {
        guard let entry = item as? [String: Any] else { failInvalid() }
        guard let path = entry["path"] as? String, !path.isEmpty else { failInvalid() }
        guard let rawIndex = entry["index"] else { failInvalid() }

        guard let number = rawIndex as? NSNumber else { failInvalid() }
        if CFGetTypeID(number as CFTypeRef) == CFBooleanGetTypeID() { failInvalid() }

        let decimal = number.decimalValue
        var wholePart = Decimal()
        var decimalCopy = decimal
        NSDecimalRound(&wholePart, &decimalCopy, 0, .down)
        if wholePart != decimal { failInvalid() }

        guard let intIndex = Int(exactly: NSDecimalNumber(decimal: decimal)) else { failInvalid() }
        if intIndex < 0 { failInvalid() }

        if seenIndices.contains(intIndex) { failInvalid() }
        seenIndices.insert(intIndex)

        if path.contains("://") { failInvalid() }

        result.append(ImageInput(index: intIndex, path: path))
    }

    return result
}

func cgOrientationFromEXIF(_ url: URL) -> CGImagePropertyOrientation {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
          let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
          let rawOrientation = properties[kCGImagePropertyOrientation] as? UInt32,
          let orientation = CGImagePropertyOrientation(rawValue: rawOrientation) else {
        return .up
    }
    return orientation
}

func loadFeaturePrint(for input: ImageInput) -> (observation: VNFeaturePrintObservation, revision: Int)? {
    var result: (VNFeaturePrintObservation, Int)? = nil

    autoreleasepool {
        let url = URL(fileURLWithPath: input.path)
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
            return
        }

        let orientation = cgOrientationFromEXIF(url)
        let request = VNGenerateImageFeaturePrintRequest()
        let handler = VNImageRequestHandler(cgImage: image, orientation: orientation, options: [:])

        do {
            try handler.perform([request])
        } catch {
            return
        }

        guard let observation = request.results?.first as? VNFeaturePrintObservation else {
            return
        }

        result = (observation, observation.requestRevision)
    }

    return result
}

@main
struct VisionEvaluation {
    static func main() {
        let stdinData = FileHandle.standardInput.readDataToEndOfFile()
        let inputs = parseInput(stdinData)

        var failures = Set<Int>()
        var observations: [Int: (VNFeaturePrintObservation, Int)] = [:]

        for input in inputs {
            if let loaded = loadFeaturePrint(for: input) {
                observations[input.index] = loaded
            } else {
                failures.insert(input.index)
            }
        }

        var distances: [[String: Any]] = []

        let orderedIndices = inputs.map { $0.index }.filter { observations[$0] != nil }

        for i in 0..<orderedIndices.count {
            for j in (i + 1)..<orderedIndices.count {
                let firstIndex = orderedIndices[i]
                let secondIndex = orderedIndices[j]
                guard let first = observations[firstIndex], let second = observations[secondIndex] else {
                    continue
                }

                if first.1 != second.1 {
                    failures.insert(firstIndex)
                    failures.insert(secondIndex)
                    continue
                }

                var distance: Float = 0
                do {
                    try first.0.computeDistance(&distance, to: second.0)
                } catch {
                    failures.insert(firstIndex)
                    failures.insert(secondIndex)
                    continue
                }

                if !distance.isFinite || distance < 0 {
                    failures.insert(firstIndex)
                    failures.insert(secondIndex)
                    continue
                }

                distances.append([
                    "firstIndex": firstIndex,
                    "secondIndex": secondIndex,
                    "distance": Double(distance)
                ])
            }
        }

        let revisionsArray = orderedIndices.map { index -> [String: Any] in
            ["index": index, "revision": observations[index]!.1]
        }

        let uniqueRevisions = Set(observations.values.map { $0.1 })
        let revision: Any = uniqueRevisions.count == 1 ? uniqueRevisions.first! : NSNull()

        let output: [String: Any] = [
            "distances": distances,
            "revision": revision,
            "revisions": revisionsArray,
            "failures": failures.sorted(),
            "method": "apple_vision_default_featureprint",
            "orientationPolicy": "imageio_exif"
        ]

        guard let jsonData = try? JSONSerialization.data(withJSONObject: output, options: [.sortedKeys]) else {
            failInvalid()
        }

        FileHandle.standardOutput.write(jsonData)
        FileHandle.standardOutput.write("\n".data(using: .utf8)!)
        exit(0)
    }
}
