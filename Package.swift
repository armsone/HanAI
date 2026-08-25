// swift-tools-version:5.9
import PackageDescription

// HanAI: 플랫폼 중립 영상·사진 분석 판정 코어.
// Foundation 외 의존성이 없으며, 카메라·Vision·AVFoundation 같은 플랫폼 입력은
// 앱 쪽 adapter가 담당한다(docs/architecture.md, docs/integration.md 참고).
let package = Package(
    name: "HanAI",
    products: [
        .library(name: "HanAI", targets: ["HanAI"])
    ],
    targets: [
        .target(
            name: "HanAI",
            path: "Sources/HanAI"
        ),
        .testTarget(
            name: "HanAITests",
            dependencies: ["HanAI"],
            path: "Tests/HanAITests"
        )
    ]
)
