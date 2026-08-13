package com.mydsoftware.mobilecameraai

object CameraRegistry {
    val cameras = listOf(
        CameraConfig(
            name = "Camera 1",
            host = "37.202.152.217",
            onvifPort = 8001,
            rtspPort = 8554,
            username = "admin",
            password = ""
        ),
        CameraConfig(
            name = "Camera 2",
            host = "37.202.152.217",
            onvifPort = 8002,
            rtspPort = 8552,
            username = "admin",
            password = ""
        )
    )
}
