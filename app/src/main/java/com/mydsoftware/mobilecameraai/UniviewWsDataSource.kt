package com.mydsoftware.mobilecameraai

import android.net.Uri
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

class UniviewWsDataSource(private val host: String, private val port: Int, private val path: String, private val username: String, private val password: String, transferListener: TransferListener? = null) : BaseDataSource(false) {
    private val mediaQueue = LinkedBlockingQueue<ByteArray>()
    private val textQueue = LinkedBlockingQueue<String>()
    private var socket: WebSocketClient? = null
    private var opened = false
    private var closed = false
    private var currentPacket: ByteArray? = null
    private var currentOffset = 0
    private val uri = "ws://$host:$port$path"

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
        if (!first.connectBlocking(10, TimeUnit.SECONDS)) throw IllegalStateException("WebSocket connect timeout")
        val msg = textQueue.poll(10, TimeUnit.SECONDS) ?: throw IllegalStateException("No Digest challenge")
        if (!msg.contains("\"errorCode\":401")) throw IllegalStateException("Unexpected WebSocket response: $msg")
        val detail = JSONObject(msg).optString("detail")
        val realm = Regex("realm=([^,\\s]+)").find(detail)?.groupValues?.get(1) ?: throw IllegalStateException("Digest realm missing")
        val nonce = Regex("nonce=([^,\\s]+)").find(detail)?.groupValues?.get(1) ?: throw IllegalStateException("Digest nonce missing")
        val qop = Regex("qop=([^,\\s]+)").find(detail)?.groupValues?.get(1) ?: "auth"
        first.closeBlocking()
        textQueue.clear()
        val second = newClient(digestAuthorization(realm, nonce, qop))
        socket = second
        if (!second.connectBlocking(10, TimeUnit.SECONDS)) throw IllegalStateException("Authenticated WebSocket connect timeout")
        Thread.sleep(150)
        val authError = textQueue.poll()
        if (authError != null && authError.contains("\"errorCode\":401")) throw IllegalStateException("Digest authentication rejected")
    }

    private fun newClient(auth: String?): WebSocketClient = object : WebSocketClient(URI(uri), Draft_6455(Collections.emptyList())) {
        override fun onOpen(handshakedata: ServerHandshake?) {}
        override fun onMessage(message: String?) { if (!message.isNullOrBlank()) textQueue.offer(message) }
        override fun onMessage(bytes: ByteArray?) { if (bytes != null && bytes.isNotEmpty()) mediaQueue.offer(bytes) }
        override fun onClose(code: Int, reason: String?, remote: Boolean) {}
        override fun onError(ex: Exception?) { if (ex != null) textQueue.offer("ERROR:${ex.message}") }
    }.also {
        it.addHeader("Origin", "http://$host:$port")
        it.addHeader("Cache-Control", "no-cache")
        it.addHeader("Pragma", "no-cache")
        if (!auth.isNullOrBlank()) it.addHeader("Cookie", "langInfo_=1; noShowTip=1; Authorization=$auth")
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
        if (currentPacket == null || currentOffset >= currentPacket!!.size) {
            currentPacket = mediaQueue.poll(15, TimeUnit.SECONDS) ?: return if (socket?.isOpen == true) 0 else C.RESULT_END_OF_INPUT
            currentOffset = 0
        }
        val packet = currentPacket!!
        val count = minOf(length, packet.size - currentOffset)
        System.arraycopy(packet, currentOffset, buffer, offset, count)
        currentOffset += count
        if (currentOffset >= packet.size) currentPacket = null
        bytesTransferred(count)
        return count
    }

    override fun getUri(): Uri = Uri.parse(uri)

    override fun close() {
        closed = true
        socket?.close()
        socket = null
        mediaQueue.clear()
        currentPacket = null
        if (opened) { transferEnded(); opened = false }
    }
}
