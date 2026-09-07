import Foundation

/// AiShot 골프 판정 모델 계보. 롤백은 `current` 값을 이전 케이스로 바꿔 수행한다.
///
/// 각 기능 플래그는 Kotlin `com.hanai.core.GolfModelVersion`과 같은 의미를 가져야 한다.
public enum GolfModelVersion: String, CaseIterable, Equatable, Sendable {
    case v0_1_0 = "0.1.0"
    case v0_2_0 = "0.2.0"
    case v0_2_1 = "0.2.1"
    case v0_3_0 = "0.3.0"
    case v0_4_0 = "0.4.0"
    case v0_5_0 = "0.5.0"
    case v0_5_1 = "0.5.1"
    case v0_6_0 = "0.6.0"
    case v0_7_0 = "0.7.0"

    /// 현재 배포 기준 모델. 불안정하면 이전 케이스로 되돌린다.
    public static let current: GolfModelVersion = .v0_7_0

    public var title: String {
        switch self {
        case .v0_1_0: "소리 중심 Ai"
        case .v0_2_0: "소리 + 화면 보조 Ai"
        case .v0_2_1: "798 영상 보정 Ai"
        case .v0_3_0: "소리·화면 독립 Ai"
        case .v0_4_0: "골프 동작 결합 Ai"
        case .v0_5_0: "몸동작 인식 Ai"
        case .v0_5_1: "몸동작 근거 보강 Ai"
        case .v0_6_0: "무음 퍼팅 안전망 Ai"
        case .v0_7_0: "혼합 클럽 연속 동작 Ai"
        }
    }

    public var releaseDate: String {
        switch self {
        case .v0_1_0, .v0_2_0, .v0_2_1: "2026.08.06"
        case .v0_3_0, .v0_4_0, .v0_5_0: "2026.08.20"
        case .v0_5_1, .v0_6_0: "2026.08.24"
        case .v0_7_0: "2026.09.07"
        }
    }

    public var featureSummary: String {
        switch self {
        case .v0_1_0:
            "소리의 피크, 갑작스러운 상승, 이어지는 반응을 중심으로 기억할 순간을 찾습니다."
        case .v0_2_0:
            "소리를 중심으로 보되, AiShot 촬영 중 화면의 움직임과 밝기 변화를 함께 참고해 더 좋은 순간을 찾습니다."
        case .v0_2_1:
            "798개 영상 공부 결과를 반영해, 소리의 피크보다 행복한 순간 뒤에 이어지는 반응과 화면 변화를 더 차분하게 함께 봅니다."
        case .v0_3_0:
            "소리가 없는 영상은 화면 움직임을 독립적으로 분석하고, 화면 변화가 적으면 중앙 구간을 정직하게 제안합니다."
        case .v0_4_0:
            "AiShot에서 정지 자세 뒤의 연속 스윙 움직임과 충격음을 함께 확인해, 준비 음성과 주변 타석 소리에 덜 반응합니다."
        case .v0_5_0:
            "기기 안에서 골퍼의 어깨·골반·팔 관절 흐름을 보조로 확인해, 단순 화면 변화보다 실제 몸동작이 있는 샷을 더 잘 구분합니다."
        case .v0_5_1:
            "화면 격자 움직임이 약해도 같은 순간의 몸동작 임팩트가 확실하면 시각 근거로 인정합니다. 충격음 판정은 항상 함께 요구하며, 자세를 충분히 보지 못하면 0.5.0과 같은 화면 움직임·소리 결합으로 판단합니다."
        case .v0_6_0:
            "타구음이 거의 없는 퍼팅을 위한 안전망을 더합니다. 준비 자세, 작은 백스윙, 전진 스트로크, 짧은 팔로스루가 시간 순서로 확인되고 화면이 안정적일 때만 소리 없이 촬영합니다. 일반 스윙의 충격음·몸동작 결합 판단은 0.5.1 그대로 유지합니다."
        case .v0_7_0:
            "드라이버부터 작은 어프로치까지 소리 크기보다 연속된 샷 동작을 함께 봅니다. 빗소리 속 약한 접촉음도 동작과 맞으면 살리고, 화면 근거가 없는 소리만으로는 촬영하지 않습니다. 짧은 퍼팅의 자세 근거를 조금 더 오래 유지합니다."
        }
    }

    // MARK: - 기능 플래그

    /// 실시간 촬영 중 화면 움직임·밝기 변화를 보조 근거로 사용한다.
    public var supportsRealtimeVisualAssist: Bool {
        self != .v0_1_0
    }

    /// 오프라인 하이라이트 점수에서 피크 뒤 반응 에너지를 가중한다.
    public var usesAudibleResponseWeight: Bool {
        self != .v0_1_0
    }

    /// 화면 움직임 상태기계(주소→백스윙→다운스윙)와 충격음을 결합한다.
    public var usesGolfSwingMotionFusion: Bool {
        [.v0_4_0, .v0_5_0, .v0_5_1, .v0_6_0, .v0_7_0].contains(self)
    }

    /// 기기 내 자세(관절) 신호를 보조 근거로 사용한다.
    public var usesBodyPoseAssist: Bool {
        [.v0_5_0, .v0_5_1, .v0_6_0, .v0_7_0].contains(self)
    }

    /// 화면 격자 움직임이 약해도 정렬된 자세 임팩트만으로 시각 근거를 인정한다.
    public var usesPoseBackedImpactEvidence: Bool {
        [.v0_5_1, .v0_6_0, .v0_7_0].contains(self)
    }

    /// 충격음 없이 자세 시퀀스만으로 퍼팅을 촬영하는 안전망을 켠다.
    public var supportsSoundlessPuttFallback: Bool {
        self == .v0_6_0 || self == .v0_7_0
    }

    /// 최근 영상이 없을 때 소리 단독 촬영을 막고 연속 동작 근거를 필수로 한다.
    public var requiresVisualShotEvidence: Bool {
        self == .v0_7_0
    }

    /// 약한 날카로운 접촉음도 정렬된 연속 동작이 있으면 샷 근거로 인정한다.
    public var supportsVisualBackedWeakImpact: Bool {
        self == .v0_7_0
    }
}
