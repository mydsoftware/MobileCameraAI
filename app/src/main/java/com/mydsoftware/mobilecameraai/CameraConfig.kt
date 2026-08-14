package com.mydsoftware.mobilecameraai

data class CameraConfig(
    val name: String,
    val host: String,
    val wsPort: Int,
    val pathPrefix: String
) {
    fun wsPath(stream: Int): String = "$pathPrefix/flv/video$stream"
}
