import java.util.Properties
import java.io.FileInputStream

plugins {
    id("com.android.application")
    id("dev.flutter.flutter-gradle-plugin")
}

val keyPropertiesFile = rootProject.file("key.properties")
val keyProperties = Properties()
if (keyPropertiesFile.exists()) {
    keyProperties.load(FileInputStream(keyPropertiesFile))
}

android {
    namespace = "com.avazbek.nemo_app"
    compileSdk = 36
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    signingConfigs {
        create("release") {
            keyAlias = keyProperties["keyAlias"] as String? ?: "nemo"
            keyPassword = keyProperties["keyPassword"] as String? ?: "nemo2025"
            storeFile = keyProperties["storeFile"]?.let { file(it as String) }
                ?: file("nemo-release.jks")
            storePassword = keyProperties["storePassword"] as String? ?: "nemo2025"
        }
    }

    defaultConfig {
        applicationId = "com.avazbek.nemo_app"
        minSdk = flutter.minSdkVersion
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
        // ONNX Runtime (openWakeWord) ships native libs for every ABI, bloating
        // the OTA APK. Restrict to arm64-v8a — every modern Android phone.
        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            isMinifyEnabled = false
            isShrinkResources = false
        }
    }

    // Flutter's --target-platform only arm64-filters its own engine; the
    // ONNX Runtime AAR still ships x86_64 + armeabi-v7a .so (~27MB dead weight
    // for any real phone). Drop them from the OTA package.
    packaging {
        jniLibs {
            excludes += listOf("lib/x86_64/**", "lib/armeabi-v7a/**")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

dependencies {
    // On-device wake word (openWakeWord, ONNX Runtime) — purpose-built keyword
    // spotter replacing the heavy Vosk STT. Fully offline, no vendor key.
    implementation("xyz.rementia:openwakeword:0.1.5")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.7.3")
}
