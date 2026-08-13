package com.mydsoftware.mobilecameraai

data class CameraConfig(
    val name: String,
    val host: String,
    val onvifPort: Int,
    val rtspPort: Int,
    val streamPath: String = "/media/video1"
)

fun CameraConfig.rtspUri(username: String, password: String): String {
    val safeUser = java.net.URLEncoder.encode(username, "UTF-8")
        .replace("+", "%20")
    val safePassword = java.net.URLEncoder.encode(password, "UTF-8")
        .replace("+", "%20")
    return "rtsp://$safeUser:$safePassword@$host:$rtspPort$streamPath"
}
