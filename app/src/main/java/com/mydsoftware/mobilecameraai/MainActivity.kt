package com.mydsoftware.mobilecameraai

import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.ProgressiveMediaSource
import androidx.media3.ui.PlayerView
import com.arthenica.ffmpegkit.FFmpegKit
import com.arthenica.ffmpegkit.FFmpegSession
import com.arthenica.ffmpegkit.ReturnCode
import java.net.ServerSocket
import java.util.concurrent.Executors

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MobileCameraAIApp() }
    }
}

private val cameras = listOf(
    CameraConfig("Camera 1", "37.202.152.217", 8554, "/media"),
    CameraConfig("Camera 2", "37.202.152.217", 8554, "/media2")
)

@Composable
private fun MobileCameraAIApp() {
    var username by remember { mutableStateOf("admin") }
    var password by remember { mutableStateOf("") }
    var selectedCamera by remember { mutableIntStateOf(0) }
    var selectedStream by remember { mutableIntStateOf(1) }

    MaterialTheme {
        Surface(Modifier.fillMaxSize()) {
            Column(Modifier.fillMaxSize().padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("MobileCameraAI", style = MaterialTheme.typography.headlineSmall)
                Text("Uniview RTSP • Native FFmpeg HEVC bridge")
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    cameras.forEachIndexed { i, c -> Button(onClick = { selectedCamera = i }) { Text(c.name) } }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    (1..3).forEach { s -> Button(onClick = { selectedStream = s }) { Text("Stream $s") } }
                }
                OutlinedTextField(username, { username = it }, label = { Text("Username") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                OutlinedTextField(password, { password = it }, label = { Text("Password") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
                CameraPlayer(cameras[selectedCamera], selectedStream, username, password)
            }
        }
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun CameraPlayer(camera: CameraConfig, stream: Int, username: String, password: String) {
    val context = LocalContext.current
    val mainHandler = remember { Handler(Looper.getMainLooper()) }
    val executor = remember { Executors.newSingleThreadExecutor() }
    var status by remember(camera.name, stream) { mutableStateOf("آماده اتصال") }
    val player = remember(camera.name, stream) { ExoPlayer.Builder(context).build() }
    var ffmpegSession by remember(camera.name, stream) { mutableStateOf<FFmpegSession?>(null) }
    var server by remember(camera.name, stream) { mutableStateOf<ServerSocket?>(null) }

    fun stopBridge() {
        try { ffmpegSession?.let { FFmpegKit.cancel(it.sessionId) } } catch (e: Exception) { Log.w("MobileCameraAI", "FFmpeg cancel: ${e.message}") }
        ffmpegSession = null
        try { player.stop(); player.clearMediaItems() } catch (e: Exception) { Log.w("MobileCameraAI", "Player stop: ${e.message}") }
        try { server?.close() } catch (e: Exception) { Log.w("MobileCameraAI", "Socket close: ${e.message}") }
        server = null
    }

    fun connect() {
        try {
            if (username.isBlank() || password.isBlank()) {
                status = "نام کاربری و رمز دوربین را وارد کنید"
                return
            }
            stopBridge()
            status = "در حال راه‌اندازی FFmpeg..."
            val localServer = ServerSocket(0)
            server = localServer
            localServer.soTimeout = 15000
            val localPort = localServer.localPort
            val user = Uri.encode(username)
            val pass = Uri.encode(password)
            val rtspUri = "rtsp://$user:$pass@${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}"
            val safeLogUri = "rtsp://$user:***@${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}"
            val outputUri = "tcp://127.0.0.1:$localPort"
            val command = "-hide_banner -loglevel warning -rtsp_transport tcp -i '$rtspUri' -map 0:v:0 -c:v copy -an -f mpegts '$outputUri'"

            Log.i("MobileCameraAI", "FFmpeg input: $safeLogUri")
            status = "در حال اتصال RTSP با FFmpeg..."
            ffmpegSession = FFmpegKit.executeAsync(command) { completed ->
                try {
                    if (!ReturnCode.isSuccess(completed.returnCode)) {
                        val detail = completed.failStackTrace ?: completed.state?.toString() ?: "FFmpeg failed"
                        mainHandler.post { status = "🔴 FFmpeg: $detail" }
                    }
                } catch (e: Exception) {
                    mainHandler.post { status = "🔴 FFmpeg callback: ${e.message}" }
                }
            }

            executor.execute {
                try {
                    val client = localServer.accept()
                    mainHandler.post {
                        try {
                            val mediaSource = ProgressiveMediaSource.Factory(LocalTcpDataSource.Factory(client))
                                .createMediaSource(MediaItem.fromUri(outputUri))
                            player.setMediaSource(mediaSource)
                            player.prepare()
                            player.playWhenReady = true
                            status = "در حال دریافت H.265 از FFmpeg..."
                        } catch (e: Exception) {
                            status = "🔴 Player setup: ${e.javaClass.simpleName}: ${e.message}"
                        }
                    }
                } catch (e: Exception) {
                    mainHandler.post { status = "🔴 Local socket: ${e.javaClass.simpleName}: ${e.message}" }
                }
            }
        } catch (e: Exception) {
            Log.e("MobileCameraAI", "Connect error", e)
            status = "🔴 Connection error: ${e.javaClass.simpleName}: ${e.message ?: "unknown"}"
        }
    }

    DisposableEffect(camera.name, stream) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                if (state == Player.STATE_BUFFERING) status = "در حال دریافت تصویر..."
                if (state == Player.STATE_READY) status = "🟢 LIVE • FFmpeg Native"
                if (state == Player.STATE_ENDED) status = "پخش پایان یافت"
            }
            override fun onPlayerError(error: PlaybackException) {
                val cause = generateSequence<Throwable>(error) { it.cause }.joinToString(" → ") {
                    "${it.javaClass.simpleName}: ${it.message ?: "no message"}"
                }
                status = "🔴 ${error.errorCodeName}\n$cause"
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            stopBridge()
            try { executor.shutdownNow() } catch (_: Exception) {}
            try { player.release() } catch (_: Exception) {}
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("${camera.name} • ${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}")
        Text(status)
        AndroidView(
            factory = { ctx -> PlayerView(ctx).also { it.player = player; it.useController = true } },
            modifier = Modifier.fillMaxWidth().height(240.dp)
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { connect() }) { Text("LIVE") }
            Button(onClick = { stopBridge(); status = "متوقف" }) { Text("STOP") }
        }
    }
}
