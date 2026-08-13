package com.mydsoftware.mobilecameraai

data class CameraConfig(
    val name: String,
    val host: String,
    val onvifPort: Int,
    val rtspPort: Int,
    val username: String,
    val password: String
)
