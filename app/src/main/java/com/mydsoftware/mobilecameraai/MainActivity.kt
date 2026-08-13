package com.mydsoftware.mobilecameraai

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.media3.common.MediaItem
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.compose.ui.viewinterop.AndroidView

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { MobileCameraAIApp() }
    }
}

@Composable
private fun MobileCameraAIApp() {
    var uri by remember { mutableStateOf("") }
    var playing by remember { mutableStateOf(false) }

    val context = androidx.compose.ui.platform.LocalContext.current
    val player = remember { ExoPlayer.Builder(context).build() }

    androidx.compose.runtime.DisposableEffect(Unit) {
        onDispose { player.release() }
    }

    MaterialTheme {
        Surface(modifier = Modifier.fillMaxSize()) {
            Column(
                modifier = Modifier.fillMaxSize().padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp)
            ) {
                Text("MobileCameraAI", style = MaterialTheme.typography.headlineMedium)

                TextField(
                    value = uri,
                    onValueChange = { uri = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text("RTSP Stream URI") },
                    singleLine = true
                )

                Button(
                    onClick = {
                        if (uri.isNotBlank()) {
                            player.setMediaItem(MediaItem.fromUri(Uri.parse(uri)))
                            player.prepare()
                            player.playWhenReady = true
                            playing = true
                        }
                    },
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Text("START LIVE")
                }

                if (playing) {
                    AndroidView(
                        factory = { PlayerView(it).apply { this.player = player } },
                        modifier = Modifier.fillMaxWidth().weight(1f)
                    )
                } else {
                    Text("Waiting for stream…")
                }
            }
        }
    }
}
