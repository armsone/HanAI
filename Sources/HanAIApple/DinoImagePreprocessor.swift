import Foundation

/// Pillow 12.3.0 (commit bb1d8e8) RGB8 bicubic resize + Transformers 5.20.1
/// (commit 0ea8779) DINOv2 전처리 계약의 순수 Swift 재구현.
/// 원 소스 라이선스: Pillow Resample.c/Image.py = MIT-CMU (Secret Labs AB /
/// Fredrik Lundh, Jeffrey "Alex" Clark and contributors). HF transformers
/// image_transforms.py/image_processing_backends.py = Apache-2.0.
/// 고정 계약 원문:
/// https://github.com/python-pillow/Pillow/blob/bb1d8e8ab8d29048624d96e3ee53cecf7c13d13d/src/libImaging/Resample.c
/// https://github.com/huggingface/transformers/blob/0ea877911a11499053df677befd4b35b172ff502/src/transformers/image_transforms.py
public enum DinoImagePreprocessor {

    public enum Error: Swift.Error {
        case invalidDimensions
        case dimensionTooLarge
        case pixelCountTooLarge
        case byteCountOverflow
        case byteCountMismatch
    }

    private static let maxDimension = 16_384
    private static let maxPixels = 16_000_000

    public static func normalizedInput(rgb: [UInt8], width: Int, height: Int) throws -> [Float] {
        guard width > 0, height > 0 else { throw Error.invalidDimensions }
        guard width <= maxDimension, height <= maxDimension else { throw Error.dimensionTooLarge }

        let (pixelCount, pixelOverflow) = width.multipliedReportingOverflow(by: height)
        guard !pixelOverflow else { throw Error.byteCountOverflow }
        guard pixelCount <= maxPixels else { throw Error.pixelCountTooLarge }

        let (byteCount, byteOverflow) = pixelCount.multipliedReportingOverflow(by: 3)
        guard !byteOverflow else { throw Error.byteCountOverflow }
        guard rgb.count == byteCount else { throw Error.byteCountMismatch }

        // 1. shortedge256 리사이즈 목표 크기 계산.
        let shortSide = min(width, height)
        let longSide = max(width, height)
        let newShort = 256
        let newLong = Int(Double(256 * longSide) / Double(shortSide))
        let resizedWidth: Int
        let resizedHeight: Int
        if width <= height {
            resizedWidth = newShort
            resizedHeight = newLong
        } else {
            resizedWidth = newLong
            resizedHeight = newShort
        }

        // 2. center crop 224x224 영역의 좌상단 좌표 (리사이즈 결과 좌표계).
        let cropTop = (resizedHeight - 224) / 2
        let cropLeft = (resizedWidth - 224) / 2

        let rgb224 = resizeAndCrop(
            rgb: rgb, srcWidth: width, srcHeight: height,
            dstWidth: resizedWidth, dstHeight: resizedHeight,
            cropLeft: cropLeft, cropTop: cropTop
        )

        // 3. rescale(1/255, float64) → float32, 이어서 mean/std normalize (float32).
        let mean: [Float] = [0.485, 0.456, 0.406]
        let std: [Float] = [0.229, 0.224, 0.225]
        var output = [Float](repeating: 0, count: 3 * 224 * 224)
        let plane = 224 * 224
        for c in 0..<3 {
            let m = mean[c]
            let s = std[c]
            for i in 0..<plane {
                let byte = rgb224[i * 3 + c]
                let scaled = Float(Double(byte) * 0.00392156862745098)
                output[c * plane + i] = (scaled - m) / s
            }
        }
        return output
    }

    /// 가로/세로 1D bicubic coefficient tap.
    private struct Tap {
        var start: Int
        var coeffs: [Int32] // Q22 양자화 계수
    }

    private static func kernel(_ x: Double) -> Double {
        let a = -0.5
        let t = abs(x)
        if t < 1.0 {
            return ((a + 2) * t - (a + 3)) * t * t + 1
        } else if t < 2.0 {
            return (((t - 5) * t + 8) * t - 4) * a
        } else {
            return 0.0
        }
    }

    /// 출력 길이 dstLen, 입력 길이 srcLen에 대한 전체 축 coefficient table.
    /// outStart..<outStart+outCount 구간만 계산한다(메모리 제약).
    private static func computeTaps(srcLen: Int, dstLen: Int, outStart: Int, outCount: Int) -> [Tap] {
        let s = Double(srcLen) / Double(dstLen)
        let filterscale = max(s, 1.0)
        let invFilterscale = 1.0 / filterscale
        let support = 2.0 * filterscale

        var taps = [Tap]()
        taps.reserveCapacity(outCount)
        for j in outStart..<(outStart + outCount) {
            let center = (Double(j) + 0.5) * s
            var start = Int((center - support + 0.5).roundedTowardZeroFloorLike())
            var end = Int((center + support + 0.5).roundedTowardZeroFloorLike())
            if start < 0 { start = 0 }
            if end > srcLen { end = srcLen }
            if end < start { end = start }

            var weights = [Double]()
            weights.reserveCapacity(max(end - start, 0))
            var sum = 0.0
            for i in start..<end {
                let w = kernel((Double(i) - center + 0.5) * invFilterscale)
                weights.append(w)
                sum += w
            }

            var coeffs = [Int32](repeating: 0, count: weights.count)
            if sum != 0.0 {
                for idx in 0..<weights.count {
                    let normalized = weights[idx] / sum
                    let q = normalized * 4194304.0 // 2^22
                    let k: Double = normalized >= 0 ? (q + 0.5).roundedTowardZeroFloorLike() : (q - 0.5).roundedTowardZeroFloorLike()
                    coeffs[idx] = Int32(k)
                }
            }
            taps.append(Tap(start: start, coeffs: coeffs))
        }
        return taps
    }

    private static func clip8(_ acc: Int64) -> UInt8 {
        let v = acc >> 22
        if v < 0 { return 0 }
        if v > 255 { return 255 }
        return UInt8(v)
    }

    /// horizontal pass: srcWidth x height RGB8 -> dstWidth x height RGB8.
    private static func resizeHorizontal(rgb: [UInt8], srcWidth: Int, height: Int, taps: [Tap], dstWidth: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: dstWidth * height * 3)
        for y in 0..<height {
            let rowBase = y * srcWidth * 3
            let outRowBase = y * dstWidth * 3
            for x in 0..<dstWidth {
                let tap = taps[x]
                for c in 0..<3 {
                    var acc: Int64 = 1 << 21
                    for (idx, k) in tap.coeffs.enumerated() {
                        let srcX = tap.start + idx
                        let pixel = Int64(rgb[rowBase + srcX * 3 + c])
                        acc += pixel * Int64(k)
                    }
                    out[outRowBase + x * 3 + c] = clip8(acc)
                }
            }
        }
        return out
    }

    /// vertical pass: width x srcHeight RGB8 -> width x dstHeight RGB8.
    private static func resizeVertical(rgb: [UInt8], width: Int, srcHeight: Int, taps: [Tap], dstHeight: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: width * dstHeight * 3)
        for y in 0..<dstHeight {
            let tap = taps[y]
            let outRowBase = y * width * 3
            for x in 0..<width {
                for c in 0..<3 {
                    var acc: Int64 = 1 << 21
                    for (idx, k) in tap.coeffs.enumerated() {
                        let srcY = tap.start + idx
                        let pixel = Int64(rgb[(srcY * width + x) * 3 + c])
                        acc += pixel * Int64(k)
                    }
                    out[outRowBase + x * 3 + c] = clip8(acc)
                }
            }
        }
        return out
    }

    /// shortedge256 리사이즈 + crop224을 중간 전체 할당 없이 수행한다.
    /// 224폭/224높이에 해당하는 coefficient만 계산하고, 중간 버퍼는
    /// (원본축 x 224) 한 장만 만든다. H>100*W이고 H'<H인 극단 tall 예외는
    /// vertical을 먼저 수행한다(Pillow 12.3.0 wrapper 계약).
    private static func resizeAndCrop(
        rgb: [UInt8], srcWidth: Int, srcHeight: Int,
        dstWidth: Int, dstHeight: Int,
        cropLeft: Int, cropTop: Int
    ) -> [UInt8] {
        let tallException = srcHeight > 100 * srcWidth && dstHeight < srcHeight

        if dstWidth == srcWidth && dstHeight == srcHeight {
            // 전체 크기가 같으면 copy 후 crop.
            return crop(rgb: rgb, width: srcWidth, height: srcHeight, left: cropLeft, top: cropTop)
        }

        if tallException {
            // vertical first: (srcWidth, dstHeight) 중간, crop된 224행만.
            let needVertical = dstHeight != srcHeight
            let needHorizontal = dstWidth != srcWidth

            var midFullWidth: [UInt8]
            if needVertical {
                let vTaps = computeTaps(srcLen: srcHeight, dstLen: dstHeight, outStart: cropTop, outCount: 224)
                midFullWidth = resizeVertical(rgb: rgb, width: srcWidth, srcHeight: srcHeight, taps: vTaps, dstHeight: 224)
            } else {
                midFullWidth = cropRows(rgb: rgb, width: srcWidth, top: cropTop, rowCount: 224)
            }

            if needHorizontal {
                let hTaps = computeTaps(srcLen: srcWidth, dstLen: dstWidth, outStart: cropLeft, outCount: 224)
                return resizeHorizontal(rgb: midFullWidth, srcWidth: srcWidth, height: 224, taps: hTaps, dstWidth: 224)
            } else {
                return cropCols(rgb: midFullWidth, width: srcWidth, height: 224, left: cropLeft, colCount: 224)
            }
        } else {
            // horizontal first: (224, srcHeight) 중간만 만든다(crop된 224열).
            let needHorizontal = dstWidth != srcWidth
            let needVertical = dstHeight != srcHeight

            var midCroppedCols: [UInt8]
            if needHorizontal {
                let hTaps = computeTaps(srcLen: srcWidth, dstLen: dstWidth, outStart: cropLeft, outCount: 224)
                midCroppedCols = resizeHorizontal(rgb: rgb, srcWidth: srcWidth, height: srcHeight, taps: hTaps, dstWidth: 224)
            } else {
                midCroppedCols = cropCols(rgb: rgb, width: srcWidth, height: srcHeight, left: cropLeft, colCount: 224)
            }

            if needVertical {
                let vTaps = computeTaps(srcLen: srcHeight, dstLen: dstHeight, outStart: cropTop, outCount: 224)
                return resizeVertical(rgb: midCroppedCols, width: 224, srcHeight: srcHeight, taps: vTaps, dstHeight: 224)
            } else {
                return cropRows(rgb: midCroppedCols, width: 224, top: cropTop, rowCount: 224)
            }
        }
    }

    private static func crop(rgb: [UInt8], width: Int, height: Int, left: Int, top: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: 224 * 224 * 3)
        for y in 0..<224 {
            let srcRowBase = (top + y) * width * 3 + left * 3
            let dstRowBase = y * 224 * 3
            for i in 0..<(224 * 3) {
                out[dstRowBase + i] = rgb[srcRowBase + i]
            }
        }
        return out
    }

    private static func cropRows(rgb: [UInt8], width: Int, top: Int, rowCount: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: width * rowCount * 3)
        let rowBytes = width * 3
        for y in 0..<rowCount {
            let srcBase = (top + y) * rowBytes
            let dstBase = y * rowBytes
            for i in 0..<rowBytes {
                out[dstBase + i] = rgb[srcBase + i]
            }
        }
        return out
    }

    private static func cropCols(rgb: [UInt8], width: Int, height: Int, left: Int, colCount: Int) -> [UInt8] {
        var out = [UInt8](repeating: 0, count: colCount * height * 3)
        for y in 0..<height {
            let srcRowBase = y * width * 3 + left * 3
            let dstRowBase = y * colCount * 3
            for i in 0..<(colCount * 3) {
                out[dstRowBase + i] = rgb[srcRowBase + i]
            }
        }
        return out
    }
}

private extension Double {
    /// Pillow의 `(int)` C cast와 동일한 0방향 절삭(trunc).
    func roundedTowardZeroFloorLike() -> Double {
        self < 0 ? self.rounded(.up) : self.rounded(.down)
    }
}
