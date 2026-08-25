package com.hanai.core

/**
 * HanAI 제품 버전과 현재 선택된 골프 판정 모델 계보.
 *
 * - [product]: HanAI 패키지(제품) 버전. 모델 계보와 독립적으로 올린다.
 * - [golfModel]: AiShot 골프 기능이 사용하는 판정 모델 계보 버전.
 */
object HanAIVersion {
    const val product: String = "0.1.0"

    val golfModel: String
        get() = GolfModelVersion.current.rawValue
}
