package com.mydsoftware.mobilecameraai

data class OnvifProfile(
    val name: String,
    val token: String,
    val encoding: String?,
    val width: Int?,
    val height: Int?,
    val fps: Int?
)
