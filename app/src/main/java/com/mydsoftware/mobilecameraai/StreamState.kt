package com.mydsoftware.mobilecameraai

sealed interface StreamState {
    data object Idle : StreamState
    data object Connecting : StreamState
    data class Playing(val uri: String) : StreamState
    data class Error(val message: String) : StreamState
}
