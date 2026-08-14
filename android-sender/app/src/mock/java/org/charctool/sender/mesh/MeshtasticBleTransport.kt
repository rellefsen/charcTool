package org.charctool.sender.mesh

class MeshtasticBleTransport(
    storageDir: String,
    preferredAddress: String? = null,
) : MeshTransport {
    override val name = "Meshtastic BLE (not in this APK)"

    init {
        listOf(storageDir, preferredAddress)
    }

    override suspend fun connect() {
        throw IllegalStateException(
            "This APK is the mock flavor. In Android Studio set Build Variant to meshDebug to include BLE.",
        )
    }

    override suspend fun disconnect() {}

    override suspend fun sendText(text: String, channelIndex: Int) {
        throw IllegalStateException("BLE is not included in the mock APK")
    }

    override fun isConnected() = false

    override fun channels() = emptyList<RadioChannel>()
}
