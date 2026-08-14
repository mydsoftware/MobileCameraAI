plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.mydsoftware.mobilecameraai"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.mydsoftware.mobilecameraai"
        minSdk = 26
        targetSdk = 36
        versionCode = 5
        versionName = "4.0.0"
    }

    buildFeatures { compose = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += "/META-INF/{AL2.0,LGPL2.1}" }
}

tasks.withType<JavaCompile>().configureEach {
    sourceCompatibility = "17"
    targetCompatibility = "17"
}

dependencies {
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.activity:activity-compose:1.11.0")
    implementation("androidx.compose.ui:ui:1.9.0")
    implementation("androidx.compose.material3:material3:1.3.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.9.3")

    // ExoPlayer is used only for playback of the local MPEG-TS bridge.
    implementation("androidx.media3:media3-exoplayer:1.8.0")
    implementation("androidx.media3:media3-ui:1.8.0")

    // Maintained native FFmpegKit. FFmpeg handles the camera's RTSP/HEVC SDP;
    // ExoPlayer receives the resulting MPEG-TS stream through localhost.
    implementation("dev.ffmpegkit-maintained:ffmpeg-kit-full:8.1.7")
}
