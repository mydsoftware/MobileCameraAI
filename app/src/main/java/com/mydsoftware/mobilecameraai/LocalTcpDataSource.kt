package com.mydsoftware.mobilecameraai

import androidx.media3.common.C
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import java.io.IOException
import java.net.ServerSocket
import java.net.Socket

/**
 * One-shot DataSource used by the FFmpeg -> localhost MPEG-TS bridge.
 * FFmpeg connects to the ServerSocket and writes a continuous MPEG-TS stream.
 */
class LocalTcpDataSource(
    private val server: ServerSocket
) : DataSource {
    private var socket: Socket? = null
    private var input: java.io.InputStream? = null
    private var uri: android.net.Uri? = null

    override fun addTransferListener(transferListener: androidx.media3.datasource.TransferListener) = Unit

    override fun open(dataSpec: DataSpec): Long {
        uri = dataSpec.uri
        socket = server.accept().also { it.tcpNoDelay = true }
        input = socket!!.getInputStream()
        return C.LENGTH_UNSET.toLong()
    }

    override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
        val stream = input ?: throw IOException("Local FFmpeg stream is not connected")
        if (length == 0) return 0
        return stream.read(buffer, offset, length)
    }

    override fun getUri(): android.net.Uri? = uri

    override fun close() {
        try { input?.close() } catch (_: Exception) {}
        try { socket?.close() } catch (_: Exception) {}
        input = null
        socket = null
        try { server.close() } catch (_: Exception) {}
    }

    class Factory(private val server: ServerSocket) : DataSource.Factory {
        override fun createDataSource(): DataSource = LocalTcpDataSource(server)
    }
}
