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
import androidx.media3.exoplayer.rtsp.RtspMediaSource
import androidx.media3.ui.PlayerView

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
            Column(
                Modifier.fillMaxSize().padding(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                Text("MobileCameraAI", style = MaterialTheme.typography.headlineSmall)
                Text("Uniview RTSP Live")

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    cameras.forEachIndexed { i, c ->
                        Button(onClick = { selectedCamera = i }) { Text(c.name) }
                    }
                }

                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    (1..3).forEach { s ->
                        Button(onClick = { selectedStream = s }) { Text("Stream $s") }
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

                CameraPlayer(cameras[selectedCamera], selectedStream, username, password)
            }
        }
    }
}

@OptIn(UnstableApi::class)
@Composable
private fun CameraPlayer(
    camera: CameraConfig,
    stream: Int,
    username: String,
    password: String
) {
    val context = LocalContext.current
    var status by remember(camera.name, stream) { mutableStateOf("آماده اتصال") }
    val player = remember(camera.name, stream) { ExoPlayer.Builder(context).build() }

    fun connect() {
        if (password.isBlank()) {
            status = "رمز دوربین را وارد کنید"
            return
        }

        val user = Uri.encode(username)
        val pass = Uri.encode(password)
        val rtspUri = "rtsp://$user:$pass@${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}"

        status = "در حال اتصال RTSP..."
        player.stop()
        player.clearMediaItems()

        val mediaSource = RtspMediaSource.Factory()
            .setForceUseRtpTcp(true)
            .createMediaSource(MediaItem.fromUri(rtspUri))

        player.setMediaSource(mediaSource)
        player.prepare()
        player.playWhenReady = true
    }

    DisposableEffect(camera.name, stream) {
        val listener = object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                status = when (state) {
                    Player.STATE_BUFFERING -> "در حال دریافت تصویر..."
                    Player.STATE_READY -> "🟢 LIVE"
                    Player.STATE_ENDED -> "پخش پایان یافت"
                    else -> status
                }
            }

            override fun onPlayerError(error: PlaybackException) {
                status = "🔴 خطا: ${error.errorCodeName}"
            }
        }

        player.addListener(listener)
        onDispose {
            player.removeListener(listener)
            player.release()
        }
    }

    Column(verticalArrangement = Arrangement.spacedBy(5.dp)) {
        Text("${camera.name} • RTSP ${camera.host}:${camera.rtspPort}${camera.rtspPath(stream)}")
        Text(status)

        AndroidView(
            factory = { PlayerView(it).apply {
                this.player = player
                useController = true
            } },
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
