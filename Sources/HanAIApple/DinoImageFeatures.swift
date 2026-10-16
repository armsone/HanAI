// HanAIApple: Apple 플랫폼 전용 선택형 adapter.
// CoreML이 없는 플랫폼(Linux 등)에서는 이 파일의 타입이 아예 선언되지 않는다.
#if canImport(CoreML)
import Foundation
import CoreML

/// 신뢰된 app-bundled 컴파일 모델(.mlmodelc) 전용 CoreML 이미지 feature adapter.
///
/// 이 타입은 입력/출력 모델 metadata가 기대 shape·dtype과 일치하는지만 검사한다.
/// 이는 암호학적 출처 검증이 아니다. 로드되는 compiled model이 신뢰된 것인지 보장하는
/// 책임은 Xcode/app 서명 및 배포 파이프라인의 artifact hash 검증에 있다.
///
/// 입력 전처리 계약: 호출자는 외부에서 256-shortest-edge bicubic resize 후
/// 224x224 center crop, 그리고 ImageNet mean/std 정규화를 적용한 RGB FP32 NCHW
/// 150528개 값(1x3x224x224)을 넘겨야 한다. 이 adapter는 디코딩이나 전처리를
/// 수행하지 않는다.
///
/// native FP32 CPU 실행 경로의 과거 Vision/ONNX 기반 threshold와의 수치적
/// parity는 이 타입의 책임 범위 밖이며 별도로 확인되어야 한다.
@available(iOS 17, macOS 14, macCatalyst 17, tvOS 17, visionOS 1, *)
public final class DinoImageFeatures {
    /// 이 adapter가 발생시키는 고정 메시지 오류. 원본 파일 경로는 포함하지 않는다.
    public enum FeatureError: Error {
        case modelLoadFailed
        case unexpectedModelInputContract
        case unexpectedModelOutputContract
        case invalidInputLength
        case nonFiniteInput
        case predictionFailed
        case nonFiniteOutput
        case zeroNormOutput
    }

    private let model: MLModel
    private let lock = NSLock()

    private static let expectedInputName = "pixel_values"
    private static let expectedOutputName = "embedding"
    private static let expectedInputShape: [Int] = [1, 3, 224, 224]
    private static let expectedOutputShape: [Int] = [1, 384]
    private static let expectedInputCount = 1 * 3 * 224 * 224
    private static let expectedOutputCount = 384

    public init(compiledModelURL: URL, computeUnits: MLComputeUnits = .cpuOnly) throws {
        let configuration = MLModelConfiguration()
        configuration.computeUnits = computeUnits
        let loaded: MLModel
        do {
            loaded = try MLModel(contentsOf: compiledModelURL, configuration: configuration)
        } catch {
            throw FeatureError.modelLoadFailed
        }

        let inputNames = loaded.modelDescription.inputDescriptionsByName
        guard Set(inputNames.keys) == [Self.expectedInputName],
              let inputDescription = inputNames[Self.expectedInputName],
              inputDescription.type == .multiArray,
              let inputConstraint = inputDescription.multiArrayConstraint,
              inputConstraint.dataType == .float32,
              inputConstraint.shape.map({ $0.intValue }) == Self.expectedInputShape
        else {
            throw FeatureError.unexpectedModelInputContract
        }

        let outputNames = loaded.modelDescription.outputDescriptionsByName
        guard Set(outputNames.keys) == [Self.expectedOutputName],
              let outputDescription = outputNames[Self.expectedOutputName],
              outputDescription.type == .multiArray,
              let outputConstraint = outputDescription.multiArrayConstraint,
              outputConstraint.dataType == .float32,
              outputConstraint.shape.map({ $0.intValue }) == Self.expectedOutputShape
        else {
            throw FeatureError.unexpectedModelOutputContract
        }

        self.model = loaded
    }

    /// 전처리된 RGB ImageNet 정규화 FP32 NCHW pixel 값(150528개)으로부터
    /// L2 CLS 임베딩 384개 float를 반환한다. 임계값·유사도·분류 판정은 하지 않는다.
    public func embedding(preprocessedPixels: [Float]) throws -> [Float] {
        guard preprocessedPixels.count == Self.expectedInputCount else {
            throw FeatureError.invalidInputLength
        }
        guard preprocessedPixels.allSatisfy({ $0.isFinite }) else {
            throw FeatureError.nonFiniteInput
        }

        let inputArray: MLMultiArray
        do {
            inputArray = try MLMultiArray(shape: Self.expectedInputShape.map { NSNumber(value: $0) }, dataType: .float32)
        } catch {
            throw FeatureError.predictionFailed
        }

        let expectedInputStrides = [150528, 50176, 224, 1]
        if inputArray.strides.map({ $0.intValue }) == expectedInputStrides {
            let inputPointer = inputArray.dataPointer.bindMemory(to: Float.self, capacity: Self.expectedInputCount)
            inputPointer.update(from: preprocessedPixels, count: Self.expectedInputCount)
        } else {
            for i in 0..<Self.expectedInputCount {
                let c = i / 50176
                let h = (i / 224) % 224
                let w = i % 224
                inputArray[[0, c, h, w] as [NSNumber]] = NSNumber(value: preprocessedPixels[i])
            }
        }

        let provider: MLDictionaryFeatureProvider
        do {
            provider = try MLDictionaryFeatureProvider(dictionary: [Self.expectedInputName: inputArray])
        } catch {
            throw FeatureError.predictionFailed
        }

        lock.lock()
        defer { lock.unlock() }

        let result: MLFeatureProvider
        do {
            result = try model.prediction(from: provider)
        } catch {
            throw FeatureError.predictionFailed
        }

        guard let outputValue = result.featureValue(for: Self.expectedOutputName),
              let outputArray = outputValue.multiArrayValue,
              outputArray.dataType == .float32,
              outputArray.shape.map({ $0.intValue }) == Self.expectedOutputShape,
              outputArray.count == Self.expectedOutputCount
        else {
            throw FeatureError.predictionFailed
        }

        var output = [Float](repeating: 0, count: Self.expectedOutputCount)
        for i in 0..<Self.expectedOutputCount {
            output[i] = outputArray[[0, i] as [NSNumber]].floatValue
        }

        guard output.allSatisfy({ $0.isFinite }) else {
            throw FeatureError.nonFiniteOutput
        }

        var normSquared: Double = 0
        for value in output {
            normSquared += Double(value) * Double(value)
        }
        guard normSquared.isFinite, normSquared > 0 else {
            throw FeatureError.zeroNormOutput
        }

        return output
    }
}
#endif
