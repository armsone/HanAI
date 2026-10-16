// HanAIApple: Apple 플랫폼 전용 선택형 adapter.
#if canImport(AVFoundation) && canImport(CoreGraphics)
import AVFoundation
import CoreGraphics
import CoreMedia

/// 기존 anchor 단일 프레임 추출을 대체하는 대표 프레임 선택 adapter.
///
/// anchor 자체와 그 양옆 ±0.25초(최대 3개, `0 <= t < duration`) 후보 중 가장
/// 선명한 한 장만 돌려준다. 이벤트/하이라이트 판정이나 구간 선택에는 전혀
/// 관여하지 않는다 — 대표 thumbnail 한 장을 고르는 역할뿐이다.
@available(iOS 17, macOS 14, macCatalyst 17, tvOS 17, visionOS 1, *)
public enum RepresentativeVideoFrame {
    public enum FrameError: Error {
        case extractionFailed
    }

    private static let candidateOffsetSeconds = 0.25
    private static let timescale: CMTimeScale = 600
    /// anchor 요청 시각이 PTS 유효 허용창 밖으로 벗어나지 않도록 쓰는 폭.
    private static let anchorToleranceSeconds = 0.25
    /// 대표 프레임 선명도 비교용 렌더 장축 상한.
    private static let sharpnessRenderLongSide = 64

    /// `anchor`는 호출자가 이미 계산해 둔 기존 trigger/anchor 시각(초)이다.
    public static func extract(
        asset: AVURLAsset,
        anchor: Double,
        appliesPreferredTrackTransform: Bool,
        maximumSize: CGSize
    ) async throws -> CGImage {
        try Task.checkCancellation()

        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = appliesPreferredTrackTransform
        generator.maximumSize = maximumSize
        // tolerance는 performExtraction 내부에서 baseline(default)과 추가
        // 후보(zero)마다 다시 설정한다.

        return try await withTaskCancellationHandler {
            try await performExtraction(generator: generator, anchor: anchor)
        } onCancel: {
            generator.cancelAllCGImageGeneration()
        }
    }

    private static func performExtraction(
        generator: AVAssetImageGenerator,
        anchor: Double
    ) async throws -> CGImage {
        guard anchor.isFinite else { throw FrameError.extractionFailed }

        let durationSeconds: Double?
        do {
            durationSeconds = try await generator.asset.load(.duration).seconds
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            durationSeconds = nil
        }
        try Task.checkCancellation()

        guard let durationSeconds, durationSeconds.isFinite, durationSeconds > 0
        else {
            // duration을 알 수 없으면 기존 default tolerance anchor 단일
            // 추출로 그대로 되돌아간다 (허용되는 유일한 단일 anchor 요청).
            generator.requestedTimeToleranceBefore = .positiveInfinity
            generator.requestedTimeToleranceAfter = .positiveInfinity
            let image = try singleFrame(generator: generator, at: max(0, anchor))
            try Task.checkCancellation()
            return image
        }

        // timescale 600 기준 마지막 유효 tick(= duration 이전)까지 clamp해
        // EOF 반올림으로 인한 copyCGImage 실패를 막는다.
        let lastValidTime = max(0, durationSeconds - 1.0 / Double(timescale))
        let clampedAnchor = min(max(0, anchor), lastValidTime)
        guard clampedAnchor.isFinite else { throw FrameError.extractionFailed }

        // 1) baseline: 기존 앱과 동일한 default tolerance anchor 단일 추출.
        // 항상 첫 요청으로 실행하며, 이 결과가 기존에 보존돼 온 "좋은 프레임"
        // 이므로 아래에서 명시한 조건을 모두 만족하지 않는 한 교체하지 않는다.
        generator.requestedTimeToleranceBefore = .positiveInfinity
        generator.requestedTimeToleranceAfter = .positiveInfinity

        var baselineImage: CGImage?
        var baselineActualTime: CMTime?
        do {
            let (image, actualTime) = try requestFrame(generator: generator, at: clampedAnchor)
            baselineImage = image
            baselineActualTime = actualTime
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            baselineImage = nil
        }
        try Task.checkCancellation()

        // baseline의 actualTime이 유한하고 측정 가능할 때만 거리 비교에 쓴다.
        // (window 통과 여부는 따로 요구하지 않는다 — baseline은 창 밖이어도
        // 기존 그림 fallback으로 보존돼야 하기 때문이다.)
        let baselineDistance: Double? = baselineActualTime.flatMap { time in
            guard time.isValid, time.isNumeric, time.seconds.isFinite else { return nil }
            return abs(time.seconds - clampedAnchor)
        }
        let baselineSharpness: Double? = baselineImage.map(sharpnessScore(of:))

        // 2) 추가 후보: clampedAnchor ± 0.25초, zero tolerance로 최대 2개만
        // 추가 요청한다 (baseline 1 + 추가 최대 2 = 총 3요청, 4번째 fallback 없음).
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero

        // baseline/추가 후보가 공유하는 공통 ±0.25초 안전창 — 추가 후보의
        // requested/actual PTS는 반드시 이 창과 0<=PTS<duration을 만족해야 한다.
        let windowLower = max(0, clampedAnchor - anchorToleranceSeconds)
        let windowUpper = min(durationSeconds, clampedAnchor + anchorToleranceSeconds)

        var extraRequestTimes: [Double] = []
        for offset in [-candidateOffsetSeconds, candidateOffsetSeconds] {
            let candidate = clampedAnchor + offset
            guard candidate >= 0, candidate <= lastValidTime else { continue }
            extraRequestTimes.append(candidate)
        }

        var seenActualTimes = Set<CMTime>()
        if let baselineActualTime { seenActualTimes.insert(baselineActualTime) }

        var validExtraCandidates: [(image: CGImage, sharpness: Double, distance: Double)] = []

        for requestedSeconds in extraRequestTimes {
            try Task.checkCancellation()

            let image: CGImage
            let actualTime: CMTime
            do {
                (image, actualTime) = try requestFrame(generator: generator, at: requestedSeconds)
            } catch {
                try Task.checkCancellation()
                continue
            }

            guard actualTime.isValid, actualTime.isNumeric,
                  actualTime.seconds.isFinite,
                  actualTime.seconds >= 0, actualTime.seconds < durationSeconds,
                  actualTime.seconds >= windowLower, actualTime.seconds <= windowUpper
            else { continue }

            // 중복 actualPTS는 비교 대상에서 제외한다.
            guard seenActualTimes.insert(actualTime).inserted else { continue }

            try Task.checkCancellation()

            let sharpness = sharpnessScore(of: image)
            let distance = abs(actualTime.seconds - clampedAnchor)
            validExtraCandidates.append((image, sharpness, distance))
        }

        try Task.checkCancellation()

        if let baselineImage {
            // baseline이 성공했으면 기본 반환값은 baseline이다. 교체는
            // (a) 선명도가 baseline보다 strict하게 높고,
            // (b) 시간오차가 baseline보다 나빠지지 않는(<=) 두 조건을
            // 모두 만족하는 Pareto-safe 후보가 있을 때만 일어난다.
            // baseline 시각이 비유한/미상이라 비교 불가면 baseline을 그대로
            // 보존하고 교체하지 않는다 (동률도 baseline 우선).
            if let baselineDistance, let baselineSharpness {
                let bestReplacement = validExtraCandidates
                    .filter { $0.distance <= baselineDistance && $0.sharpness > baselineSharpness }
                    .max { $0.sharpness < $1.sharpness }
                if let bestReplacement {
                    return bestReplacement.image
                }
            }
            return baselineImage
        }

        // 4) baseline 추출이 실패한 경우에만 유효한 추가 후보 중 품질 최선을
        // 반환한다. 세 요청 모두 실패했으면 고정 FrameError만 노출한다.
        if let bestExtra = validExtraCandidates.max(by: { $0.sharpness < $1.sharpness }) {
            return bestExtra.image
        }
        throw FrameError.extractionFailed
    }

    private static func singleFrame(
        generator: AVAssetImageGenerator,
        at seconds: Double
    ) throws -> CGImage {
        do {
            return try generator.copyCGImage(
                at: CMTime(seconds: seconds, preferredTimescale: timescale),
                actualTime: nil
            )
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw FrameError.extractionFailed
        }
    }

    private static func requestFrame(
        generator: AVAssetImageGenerator,
        at seconds: Double
    ) throws -> (CGImage, CMTime) {
        var actualTime = CMTime.zero
        do {
            let image = try generator.copyCGImage(
                at: CMTime(seconds: seconds, preferredTimescale: timescale),
                actualTime: &actualTime
            )
            return (image, actualTime)
        } catch is CancellationError {
            throw CancellationError()
        } catch {
            throw FrameError.extractionFailed
        }
    }

    /// Laplacian 분산을 선명도 대리값으로 쓴다 — 기존 다른 선명도 판정
    /// 로직과 독립적인, 대표 프레임 후보 비교용 지표일 뿐이다. 모든 후보가
    /// 0으로 묶여 비교가 무의미해지는 사고를 막기 위해 고정 장축(64px)으로
    /// bounded, aspect-preserving DeviceGray 렌더를 사용한다.
    private static func sharpnessScore(of image: CGImage) -> Double {
        guard image.width > 0, image.height > 0 else { return 0 }
        let grayColorSpace = CGColorSpaceCreateDeviceGray()

        let longSide = max(image.width, image.height)
        let scale = Double(sharpnessRenderLongSide) / Double(longSide)
        let renderWidth = max(1, Int((Double(image.width) * scale).rounded()))
        let renderHeight = max(1, Int((Double(image.height) * scale).rounded()))
        guard renderWidth > 2, renderHeight > 2 else { return 0 }

        let bytesPerRow = renderWidth
        var gray = [UInt8](repeating: 0, count: bytesPerRow * renderHeight)
        let didDraw = gray.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(
                data: buffer.baseAddress,
                width: renderWidth,
                height: renderHeight,
                bitsPerComponent: 8,
                bytesPerRow: bytesPerRow,
                space: grayColorSpace,
                bitmapInfo: CGImageAlphaInfo.none.rawValue
            ) else { return false }
            context.interpolationQuality = .high
            context.draw(
                image,
                in: CGRect(x: 0, y: 0, width: renderWidth, height: renderHeight)
            )
            return true
        }
        guard didDraw else { return 0 }

        var sum = 0.0
        var sumSquares = 0.0
        var count = 0
        for y in 1..<(renderHeight - 1) {
            let row = y * bytesPerRow
            let prevRow = row - bytesPerRow
            let nextRow = row + bytesPerRow
            for x in 1..<(renderWidth - 1) {
                let laplacian = Int(gray[row + x]) * 4
                    - Int(gray[row + x - 1])
                    - Int(gray[row + x + 1])
                    - Int(gray[prevRow + x])
                    - Int(gray[nextRow + x])
                let value = Double(laplacian)
                sum += value
                sumSquares += value * value
                count += 1
            }
        }
        guard count > 0 else { return 0 }

        let mean = sum / Double(count)
        return sumSquares / Double(count) - mean * mean
    }
}
#endif
