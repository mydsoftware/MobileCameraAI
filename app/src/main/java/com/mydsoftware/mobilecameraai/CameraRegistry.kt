package com.mydsoftware.mobilecameraai

object CameraRegistry {
    val cameras = listOf(
        CameraConfig(
            name = "Camera 1",
            host = "37.202.152.217",
            rtspPort = 8554,
            pathPrefix = "/media"
        ),
        CameraConfig(
            name = "Camera 2",
            host = "37.202.152.217",
            rtspPort = 8554,
            pathPrefix = "/media2"
        )
    )
}
