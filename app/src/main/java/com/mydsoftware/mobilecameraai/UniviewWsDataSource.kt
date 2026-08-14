package com.mydsoftware.mobilecameraai

import androidx.media3.common.C
import androidx.media3.datasource.BaseDataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.TransferListener
import org.java_websocket.client.WebSocketClient
import org.java_websocket.drafts.Draft_6455
import org.java_websocket.handshake.ServerHandshake
import org.json.JSONObject
import java.net.URI
import java.security.MessageDigest
import java.util.Collections
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class UniviewWsDataSource(
    private val host: String,
    private val port: Int,
    private val path: String,
    private val username: String,
    private val password: String,
    transferListener: TransferListener? = null
) : BaseDataSource(false) {
    private val queue = LinkedBlockingQueue<ByteArray>()
    private var socket: WebSocketClient? = null
    private var opened = false
    private var closed = false
    private var challenge: String? = null
    private var bytesRead = 0L
    private var uri = "ws://$host:$port$path"

    init { if (transferListener != null) addTransferListener(transferListener) }

    override fun open(dataSpec: DataSpec): Long {
        transferInitializing(dataSpec)
        connectAuthenticated()
        opened = true
        transferStarted(dataSpec)
        return C.LENGTH_UNSET.toLong()
    }

    private fun connectAuthenticated() {
        val first = newClient(null)
        socket = first
        first.connectBlocking(10, TimeUnit.SECONDS)
        val msg = firstHandshakeMessage(first)
        if (msg.contains("\"errorCode\":401")) {
            val detail = JSONObject(msg).optString("detail")
            challenge = detail
            val realm = Regex("realm=([^,\\s]+)").find(detail)?.groupValues?.get(1)
                ?: throw IllegalStateException("Digest realm missing")
            val nonce = Regex("nonce=([^,\\s]+)").find(detail)?.groupValues?.get(1)
                ?: throw IllegalStateException("Digest nonce missing")
            val qop = Regex("qop=([^,\\s]+)").find(detail)?.groupValues?.get(1) ?: "auth"
            first.closeBlocking()
            val auth = digestAuthorization(realm, nonce, qop)
            val second = newClient(auth)
            socket = second
            second.connectBlocking(10, TimeUnit.SECONDS)
            waitForMedia(second)
        } else if (msg.isNotEmpty()) {
            throw IllegalStateException("Unexpected WebSocket response: $msg")
        }
        if (socket?.isOpen != true) throw IllegalStateException("WebSocket connection failed")
    }

    private fun firstHandshakeMessage(client: WebSocketClient): String {
        repeat(30) {
            val value = challengeQueue.poll(1, TimeUnit.SECONDS) ?: return@repeat
            if (value.isNotEmpty()) return value.toString(Charsets.UTF_8)
        }
        return ""
    }

    private fun waitForMedia(client: WebSocketClient) {
        repeat(50) {
            if (queue.isNotEmpty()) return
            if (!client.isOpen) throw IllegalStateException("Authenticated WebSocket closed")
            Thread.sleep(100)
        }
    }

    private val challengeQueue = LinkedBlockingQueue<ByteArray>()

    private fun newClient(auth: String?): WebSocketClient {
        val client = object : WebSocketClient(URI(uri), Draft_6455(Collections.emptyList())) {
            override fun onOpen(handshakedata: ServerHandshake?) {}
            override fun onMessage(message: String?) {
                if (!message.isNullOrBlank()) challengeQueue.offer(message.toByteArray())
            }
            override fun onMessage(bytes: ByteArray?) { if (bytes != null && bytes.isNotEmpty()) queue.offer(bytes) }
            override fun onClose(code: Int, reason: String?, remote: Boolean) {}
            override fun onError(ex: Exception?) { if (ex != null) challengeQueue.offer("ERROR:${ex.message}".toByteArray()) }
        }
        client.addHeader("Origin", "http://$host:$port")
        client.addHeader("Cache-Control", "no-cache")
        client.addHeader("Pragma", "no-cache")
        if (!auth.isNullOrBlank()) client.addHeader("Cookie", "langInfo_=1; noShowTip=1; Authorization=$auth")
        return client
    }

    private fun digestAuthorization(realm: String, nonce: String, qop: String): String {
        val nc = "00000001"
        val cnonce = md5(System.nanoTime().toString() + password)
        val ha1 = md5("$username:$realm:$password")
        val ha2 = md5("GET:$uri")
        val response = md5("$ha1:$nonce:$nc:$cnonce:$qop:$ha2")
        return "Digest username=\"$username\", realm=\"$realm\", nonce=\"$nonce\", algorithm=\"MD5\", uri=\"$uri\", response=\"$response\", qop=\"$qop\", nc=\"$nc\", cnonce=\"$cnonce\""
    }

    private fun md5(value: String): String = MessageDigest.getInstance("MD5").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        if (length == 0) return 0
        if (closed) return C.RESULT_END_OF_INPUT
        var packet = queue.poll(15, TimeUnit.SECONDS) ?: return if (socket?.isOpen == true) 0 else C.RESULT_END_OF_INPUT
        val count = minOf(length, packet.size)
        System.arraycopy(packet, 0, buffer, offset, count)
        if (count < packet.size) queue.offer(packet.copyOfRange(count, packet.size))
        bytesRead += count
        transferBytesTransferred(DataSpec(Uri.parse(uri)), false, count)
        return count
    }

    override fun getUri() = android.net.Uri.parse(uri)
    override fun close() {
        closed = true
        socket?.close()
        socket = null
        queue.clear()
        if (opened) { transferEnded() ; opened = false }
    }
}
