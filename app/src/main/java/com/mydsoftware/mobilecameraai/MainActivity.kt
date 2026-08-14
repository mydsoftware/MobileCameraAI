package com.mydsoftware.mobilecameraai

import android.os.Bundle
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
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!Python.isStarted()) Python.start(AndroidPlatform(this))
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
                Text("Python RTSP Engine")
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
    var status by remember(camera.name, stream) { mutableStateOf("آماده اتصال") }
    val player = remember(camera.name, stream) { ExoPlayer.Builder(context).build() }

    fun connect() {
        try {
            if (username.isBlank() || password.isBlank()) {
                status = "نام کاربری و رمز دوربین را وارد کنید"
                return
            }
            status = "در حال بررسی RTSP با Python..."
            val py = Python.getInstance()
            val module = py.getModule("rtsp_engine")
            val result = module.callAttr("probe", camera.host, camera.rtspPort, camera.rtspPath(stream), username, password)
            status = result.toString()
        } catch (e: Exception) {
            status = "🔴 Python error: ${e.javaClass.simpleName}: ${e.message ?: "unknown"}"
        }
    }

    DisposableEffect(camera.name, stream) {
        val listener = object : Player.Listener {
            override fun onPlayerError(error: PlaybackException) {
                status = "🔴 ${error.errorCodeName}: ${error.message ?: "player error"}"
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("${camera.name} • ${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}")
        Text(status)
        AndroidView(
            factory = { ctx -> PlayerView(ctx).also { view -> view.player = player; view.useController = true } },
            modifier = Modifier.fillMaxWidth().height(240.dp)
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { connect() }) { Text("LIVE") }
            Button(onClick = { player.stop(); status = "متوقف" }) { Text("STOP") }
        }
    }
}
