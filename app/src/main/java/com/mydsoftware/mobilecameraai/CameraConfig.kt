package com.mydsoftware.mobilecameraai

data class CameraConfig(
    val name: String,
    val host: String,
    val rtspPort: Int,
    val pathPrefix: String
) {
    fun rtspPath(stream: Int): String = "$pathPrefix/video$stream"
}
