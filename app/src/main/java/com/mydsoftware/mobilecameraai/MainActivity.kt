package com.mydsoftware.mobilecameraai

import android.net.Uri
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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MobileCameraAIApp() }
    }
}

private val cameras = listOf(
    CameraConfig("Camera 1", "37.202.152.217", 8001, 8554),
    CameraConfig("Camera 2", "37.202.152.217", 8002, 8552)
)

@Composable
private fun MobileCameraAIApp() {
    var username by remember { mutableStateOf("admin") }
    var password by remember { mutableStateOf("") }
    var selected by remember { mutableStateOf(0) }

    MaterialTheme {
        Surface(Modifier.fillMaxSize()) {
            Column(
                Modifier.fillMaxSize().padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(10.dp)
            ) {
                Text("MobileCameraAI", style = MaterialTheme.typography.headlineSmall)
                Text("2-Camera Live Viewer")

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    cameras.forEachIndexed { index, camera ->
                        Button(onClick = { selected = index }) {
                            Text(camera.name)
                        }
                    }
                }

                OutlinedTextField(
                    value = username,
                    onValueChange = { username = it },
                    label = { Text("Username") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text("Password") },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true
                )

                CameraPlayer(
                    camera = cameras[selected],
                    username = username,
                    password = password
                )
            }
        }
    }
}

@Composable
private fun CameraPlayer(
    camera: CameraConfig,
    username: String,
    password: String
) {
    val context = androidx.compose.ui.platform.LocalContext.current
    var status by remember(camera.name) { mutableStateOf("آماده اتصال") }
    var retry by remember(camera.name) { mutableStateOf(0) }

    val player = remember(camera.name) {
        ExoPlayer.Builder(context).build().apply {
            repeatMode = Player.REPEAT_MODE_OFF
        }
    }

    fun connect() {
        if (username.isBlank() || password.isBlank()) {
            status = "نام کاربری و رمز را وارد کنید"
            return
        }
        status = "در حال اتصال..."
        retry++
        player.setMediaItem(MediaItem.fromUri(Uri.parse(camera.rtspUri(username, password))))
        player.prepare()
        player.playWhenReady = true
    }

    DisposableEffect(camera.name) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(playbackState: Int) {
                status = when (playbackState) {
                    Player.STATE_BUFFERING -> "در حال دریافت تصویر..."
                    Player.STATE_READY -> "🟢 LIVE"
                    Player.STATE_ENDED -> "پخش پایان یافت"
                    else -> status
                }
            }

            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {
                status = "🔴 قطع شد — تلاش مجدد..."
            }
        }
        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }

    LaunchedEffect(retry, username, password, camera.name) {
        if (retry > 0) {
            delay(1500)
            connect()
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text("${camera.name}  •  ${camera.host}:${camera.rtspPort}")
        Text(status)

        AndroidView(
            factory = { PlayerView(it).apply { this.player = player; useController = true } },
            modifier = Modifier.fillMaxWidth().height(240.dp)
        )

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { connect() }) { Text("LIVE") }
            Button(onClick = {
                player.stop()
                status = "متوقف"
            }) { Text("STOP") }
        }
    }
}
