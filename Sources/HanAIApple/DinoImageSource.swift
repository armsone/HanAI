// HanAIApple: Apple 플랫폼 전용 선택형 adapter.
#if canImport(ImageIO) && canImport(CoreGraphics)
import Foundation
import ImageIO
import CoreGraphics

/// app 로컬 image 파일(URL) 또는 Data를 `DinoImagePreprocessor.normalizedInput`이
/// 요구하는 oriented sRGB RGB8 바이트로 디코딩하는 adapter.
///
/// 디코딩 자체만 수행하며 유사도 판정이나 분류는 하지 않는다. 호출자는 이
/// 타입이 던지는 에러를 해당 배치 전체의 fallback 신호로 다뤄야 한다 — 소형
/// 원본이나 극단적인 가로세로비에 대해 억지로 업스케일하거나 치수 제약을
/// 새로 조정하지 않는다.
@available(iOS 17, macOS 14, macCatalyst 17, tvOS 17, visionOS 1, *)
public enum DinoImageSource {
    public enum SourceError: Error {
        case decodeFailed
        case unsupportedDimensions
        case unsupportedURL
    }

    public struct DecodedImage {
        public let rgb: [UInt8]
        public let width: Int
        public let height: Int
    }

    /// `DinoImagePreprocessor`에 넘길 장축 상한. 원본보다 크게 업스케일하지
    /// 않도록 항상 원본 장축과 비교해 더 작은 값을 실제 목표로 사용한다.
    private static let maxLongSide = 2048
    private static let minShortSide = 256

    /// 로컬 일반 파일만 허용한다 — 원격 URL을 이 API로 열면 네트워크 요청이
    /// 생기거나 호출자가 기대하지 않는 지연·실패 양상이 나타날 수 있다.
    public static func decode(contentsOf url: URL) throws -> DecodedImage {
        guard url.isFileURL else { throw SourceError.unsupportedURL }
        let resourceValues = try? url.resourceValues(forKeys: [.isRegularFileKey])
        guard resourceValues?.isRegularFile == true else {
            throw SourceError.unsupportedURL
        }
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            throw SourceError.decodeFailed
        }
        return try decode(source: source)
    }

    public static func decode(data: Data) throws -> DecodedImage {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil) else {
            throw SourceError.decodeFailed
        }
        return try decode(source: source)
    }

    private static func decode(source: CGImageSource) throws -> DecodedImage {
        guard
            let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil)
                as? [CFString: Any],
            let nativeWidth = properties[kCGImagePropertyPixelWidth] as? Int,
            let nativeHeight = properties[kCGImagePropertyPixelHeight] as? Int,
            nativeWidth > 0, nativeHeight > 0
        else {
            throw SourceError.decodeFailed
        }

        // shortedge>=256을 업스케일 없이 보장할 수 없는 원본은 바로 fallback.
        guard min(nativeWidth, nativeHeight) >= minShortSide else {
            throw SourceError.unsupportedDimensions
        }

        let targetLongSide = min(maxLongSide, max(nativeWidth, nativeHeight))
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: targetLongSide,
            kCGImageSourceShouldCacheImmediately: true
        ]
        guard let cgImage = CGImageSourceCreateThumbnailAtIndex(
            source, 0, options as CFDictionary
        ) else {
            throw SourceError.decodeFailed
        }

        let width = cgImage.width
        let height = cgImage.height
        // 극단적인 가로세로비는 장축을 줄이는 과정에서 단축이 256 밑으로
        // 떨어질 수 있다 — threshold를 다시 맞추는 대신 그대로 fallback한다.
        guard width > 0, height > 0, min(width, height) >= minShortSide else {
            throw SourceError.unsupportedDimensions
        }

        guard let colorSpace = CGColorSpace(name: CGColorSpace.sRGB) else {
            throw SourceError.decodeFailed
        }

        // CGContext는 RGB24(3바이트/픽셀, alpha 없음)를 모든 플랫폼에서
        // 안정적으로 지원하지 않아 decode가 상시 실패할 수 있다. Apple이
        // 보장하는 32bit RGBA(premultipliedLast + byteOrder32Big) 구성으로
        // 그린 뒤, 불투명 검정 배경 위에서 RGB 3바이트만 추출한다.
        let rgbaBytesPerRow = width * 4
        var rgba = [UInt8](repeating: 0, count: rgbaBytesPerRow * height)
        let didDraw = rgba.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: width,
                height: height,
                bitsPerComponent: 8,
                bytesPerRow: rgbaBytesPerRow,
                space: colorSpace,
                bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
                    | CGBitmapInfo.byteOrder32Big.rawValue
            ) else { return false }
            context.interpolationQuality = .high
            context.setFillColor(red: 0, green: 0, blue: 0, alpha: 1)
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard didDraw else { throw SourceError.decodeFailed }

        var rgb = [UInt8](repeating: 0, count: width * 3 * height)
        rgba.withUnsafeBytes { source in
            rgb.withUnsafeMutableBytes { destination in
                for y in 0..<height {
                    let srcRow = source.baseAddress!
                        .advanced(by: y * rgbaBytesPerRow)
                    let dstRow = destination.baseAddress!
                        .advanced(by: y * width * 3)
                    for x in 0..<width {
                        let srcPixel = srcRow.advanced(by: x * 4)
                            .assumingMemoryBound(to: UInt8.self)
                        let dstPixel = dstRow.advanced(by: x * 3)
                            .assumingMemoryBound(to: UInt8.self)
                        dstPixel[0] = srcPixel[0]
                        dstPixel[1] = srcPixel[1]
                        dstPixel[2] = srcPixel[2]
                    }
                }
            }
        }

        return DecodedImage(rgb: rgb, width: width, height: height)
    }
}
#endif
