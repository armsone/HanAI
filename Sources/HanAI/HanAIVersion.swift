import Foundation

/// HanAI 제품 버전과 현재 선택된 골프 판정 모델 계보를 한곳에서 노출한다.
///
/// - `product`: HanAI 패키지(제품) 버전. 모델 계보와 독립적으로 올린다.
/// - `golfModel`: AiShot 골프 기능이 사용하는 판정 모델 계보 버전.
public enum HanAIVersion {
    public static let product = "0.1.0"

    public static var golfModel: String {
        GolfModelVersion.current.rawValue
    }
}
