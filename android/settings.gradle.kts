// HanAI Kotlin/JVM 순수 라이브러리. Android SDK·Compose에 의존하지 않는다.
pluginManagement {
    repositories {
        gradlePluginPortal()
        mavenCentral()
    }
}

dependencyResolutionManagement {
    repositories {
        mavenCentral()
    }
}

rootProject.name = "hanai-android"
include(":hanai-core")
