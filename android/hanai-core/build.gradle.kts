import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    kotlin("jvm")
    `java-library`
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    // 런타임 의존성 없음. 테스트는 JUnit 4만 사용한다.
    testImplementation("junit:junit:4.13.2")
}

tasks.test {
    useJUnit()
    // Swift 테스트와 같은 저장소 루트 Fixtures/ 를 읽는다.
    systemProperty(
        "hanai.fixturesDir",
        rootDir.parentFile.resolve("Fixtures").absolutePath
    )
}
