package org.charctool.sender.mesh

interface MeshTransport {
    val name: String
    suspend fun connect()
    suspend fun disconnect()
    suspend fun sendText(text: String, channelIndex: Int)
    fun isConnected(): Boolean
    fun channels(): List<RadioChannel> = emptyList()
}

class MockMeshTransport : MeshTransport {
    override val name = "Mock (no radio)"
    private var connected = false
    val sent = mutableListOf<String>()

    override suspend fun connect() {
        connected = true
    }

    override suspend fun disconnect() {
        connected = false
    }

    override suspend fun sendText(text: String, channelIndex: Int) {
        sent += text
    }

    override fun isConnected() = connected

    override fun channels() = listOf(
        RadioChannel(0, "Primary"),
        RadioChannel(1, "charcStatus"),
    )
}
