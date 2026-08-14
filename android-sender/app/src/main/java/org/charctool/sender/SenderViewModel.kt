package org.charctool.sender

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import org.charctool.sender.mesh.BleRadio
import org.charctool.sender.mesh.BleRadioFinder
import org.charctool.sender.mesh.MeshtasticBleTransport
import org.charctool.sender.mesh.MeshTransport
import org.charctool.sender.mesh.MockMeshTransport
import org.charctool.sender.mesh.RadioChannel
import org.charctool.sender.protocol.HouseRow
import org.charctool.sender.protocol.Organization
import org.charctool.sender.protocol.PacketCodec
import org.charctool.sender.protocol.Precinct
import org.charctool.sender.protocol.SeedParser

data class SenderUiState(
    val hasSeed: Boolean = false,
    val organization: Organization? = null,
    val precinctId: String = "",
    val houses: List<HouseRow> = emptyList(),
    val mockRadio: Boolean = true,
    val radioConnected: Boolean = false,
    val radioLabel: String = "Mock (no radio)",
    val channelIndex: Int = 1,
    val channels: List<RadioChannel> = emptyList(),
    val packetDelayMs: Long = 2000,
    val bleAddress: String = "",
    val radios: List<BleRadio> = emptyList(),
    val scanning: Boolean = false,
    val pendingBleAction: String = "",
    val statusMessage: String = "Import a seed zip, then mark houses and send.",
    val busy: Boolean = false,
)

class SenderViewModel(app: Application) : AndroidViewModel(app) {
    private val store = SeedStore(app.filesDir)
    private var housesByPrecinct: Map<String, List<HouseRow>> = emptyMap()
    private var transport: MeshTransport = MockMeshTransport()
    private var scanJob: Job? = null

    private val _ui = MutableStateFlow(
        SenderUiState(
            hasSeed = store.hasSeed(),
            channels = MockMeshTransport().channels(),
        ),
    )
    val ui: StateFlow<SenderUiState> = _ui

    init {
        if (store.hasSeed()) {
            reloadFromDisk()
        }
    }

    fun importZip(bytes: ByteArray) {
        viewModelScope.launch {
            try {
                val bundle = SeedParser.parseZip(bytes)
                store.saveBundle(
                    orgJson = kotlinx.serialization.json.Json.encodeToString(
                        Organization.serializer(),
                        bundle.organization,
                    ),
                    houses = bundle.housesByPrecinct,
                )
                reloadFromDisk()
                _ui.update { it.copy(statusMessage = "Seed loaded: ${bundle.organization.precincts.size} precincts") }
            } catch (exc: Exception) {
                _ui.update { it.copy(statusMessage = "Import failed: ${exc.message}") }
            }
        }
    }

    fun selectPrecinct(id: String) {
        val houses = housesByPrecinct[id.uppercase()].orEmpty()
        _ui.update { it.copy(precinctId = id.uppercase(), houses = houses) }
    }

    fun setStatus(houseId: String, status: String) {
        val precinctId = _ui.value.precinctId
        val current = housesByPrecinct[precinctId].orEmpty()
        val previous = current.firstOrNull { it.houseId == houseId }?.status
        val updated = current.map {
            if (it.houseId == houseId) it.withStatus(status) else it
        }
        housesByPrecinct = housesByPrecinct + (precinctId to updated)
        store.writeStatus(precinctId, updated)
        if (previous != null &&
            previous.uppercase() != PacketCodec.GREEN &&
            status.uppercase() == PacketCodec.GREEN
        ) {
            store.recordClear(precinctId, houseId)
        }
        _ui.update { it.copy(houses = updated) }
    }

    fun setMockRadio(mock: Boolean) {
        viewModelScope.launch {
            transport.disconnect()
            stopScan()
            transport = if (mock) MockMeshTransport() else newBleTransport()
            _ui.update {
                it.copy(
                    mockRadio = mock,
                    radioConnected = false,
                    radioLabel = transport.name,
                    radios = if (mock) emptyList() else it.radios,
                    scanning = false,
                    channels = if (mock) MockMeshTransport().channels() else emptyList(),
                    statusMessage = "Radio set to ${transport.name}",
                )
            }
        }
    }

    fun selectChannel(index: Int) {
        _ui.update { it.copy(channelIndex = index.coerceIn(0, 7)) }
    }

    fun setPacketDelayMs(ms: Long) {
        _ui.update { it.copy(packetDelayMs = ms.coerceIn(0, 30_000)) }
    }

    fun setPendingBleAction(action: String) {
        _ui.update { it.copy(pendingBleAction = action) }
    }

    fun clearPendingBleAction() {
        _ui.update { it.copy(pendingBleAction = "") }
    }

    fun setBleAddress(value: String) {
        _ui.update { it.copy(bleAddress = value.trim()) }
    }

    fun selectRadio(radio: BleRadio) {
        stopScan()
        _ui.update {
            it.copy(
                bleAddress = radio.address,
                statusMessage = "Selected ${radio.label}",
            )
        }
    }

    fun startScan() {
        if (_ui.value.mockRadio) {
            _ui.update { it.copy(statusMessage = "Turn Mock radio off, then scan for Meshtastic nodes.") }
            return
        }
        scanJob?.cancel()
        scanJob = viewModelScope.launch {
            _ui.update {
                it.copy(
                    scanning = true,
                    radios = emptyList(),
                    radioConnected = false,
                    statusMessage = "Scanning for Meshtastic radios… tap one to select it.",
                )
            }
            try {
                BleRadioFinder.scan().collect { radios ->
                    _ui.update { it.copy(radios = radios) }
                }
            } catch (exc: CancellationException) {
                throw exc
            } catch (exc: Exception) {
                _ui.update {
                    it.copy(
                        scanning = false,
                        statusMessage = "Scan failed: ${exc.message}",
                    )
                }
            }
        }
    }

    fun stopScan() {
        scanJob?.cancel()
        scanJob = null
        _ui.update { it.copy(scanning = false) }
    }

    fun connectRadio() {
        viewModelScope.launch {
            stopScan()
            delay(300)
            val selected = _ui.value.bleAddress
            _ui.update { it.copy(busy = true, statusMessage = "Connecting ${transport.name}…") }
            try {
                if (!_ui.value.mockRadio) {
                    if (BleRadio.normalizeMac(selected) == null) {
                        throw IllegalStateException("Scan radios, then tap the node you want before Connect.")
                    }
                    transport.disconnect()
                    transport = newBleTransport()
                }
                transport.connect()
                val channels = transport.channels()
                val selected = pickChannel(channels, _ui.value.channelIndex)
                _ui.update {
                    it.copy(
                        busy = false,
                        radioConnected = true,
                        radioLabel = transport.name,
                        channels = channels,
                        channelIndex = selected,
                        statusMessage = "Connected: ${it.bleAddress.ifBlank { transport.name }}" +
                            channelStatus(channels, selected),
                    )
                }
            } catch (exc: Exception) {
                _ui.update {
                    it.copy(
                        busy = false,
                        radioConnected = false,
                        statusMessage = "Connect failed: ${exc.message}",
                    )
                }
            }
        }
    }

    fun sendChanges() {
        sendPackets { precinct, houses ->
            val last = store.readLastSync(precinct)
            val packets = houses.deltaPackets(precinct, last)
            packets to { store.writeLastSync(precinct, houses) }
        }
    }

    fun sendHeartbeat() {
        sendPackets { precinct, houses ->
            val nonGreen = PacketCodec.nonGreenRows(houses.pairs())
            val clears = store.takeClears(precinct)
            val packets = PacketCodec.buildHeartbeatPackets(precinct, nonGreen, clears)
            packets to { store.writeLastSync(precinct, houses) }
        }
    }

    private fun sendPackets(
        build: (String, List<HouseRow>) -> Pair<List<String>, () -> Unit>,
    ) {
        viewModelScope.launch {
            val state = _ui.value
            if (state.houses.isEmpty()) {
                _ui.update { it.copy(statusMessage = "No houses loaded for this precinct") }
                return@launch
            }
            val (packets, onSuccess) = build(state.precinctId, state.houses)
            if (packets.isEmpty()) {
                _ui.update { it.copy(statusMessage = "Nothing to send — board matches last sync") }
                return@launch
            }
            _ui.update { it.copy(busy = true, statusMessage = "Sending ${packets.size} packet(s)…") }
            try {
                if (!transport.isConnected()) {
                    stopScan()
                    delay(300)
                    if (!state.mockRadio) {
                        if (BleRadio.normalizeMac(state.bleAddress) == null) {
                            throw IllegalStateException("Scan radios, then tap the node you want before sending.")
                        }
                        transport.disconnect()
                        transport = newBleTransport()
                    }
                    transport.connect()
                }
                for ((index, packet) in packets.withIndex()) {
                    transport.sendText(packet, state.channelIndex)
                    if (index < packets.lastIndex && state.packetDelayMs > 0) {
                        delay(state.packetDelayMs)
                    }
                }
                onSuccess()
                _ui.update {
                    it.copy(
                        busy = false,
                        radioConnected = transport.isConnected(),
                        statusMessage = "Sent ${packets.size} packet(s) on ${channelLabel(state)}",
                    )
                }
            } catch (exc: Exception) {
                _ui.update {
                    it.copy(busy = false, statusMessage = "Send failed: ${exc.message}")
                }
            }
        }
    }

    private fun newBleTransport(): MeshTransport = MeshtasticBleTransport(
        storageDir = getApplication<Application>().filesDir.absolutePath,
        preferredAddress = _ui.value.bleAddress.ifBlank { null },
    )

    private fun pickChannel(channels: List<RadioChannel>, currentIndex: Int): Int {
        val named = channels.firstOrNull { it.name.equals("charcStatus", ignoreCase = true) }
        if (named != null) return named.index
        if (channels.any { it.index == currentIndex }) return currentIndex
        return channels.firstOrNull()?.index ?: currentIndex
    }

    private fun channelStatus(channels: List<RadioChannel>, index: Int): String {
        if (channels.isEmpty()) return ". Channel names were empty — check the radio channel list."
        val selected = channels.firstOrNull { it.index == index }
        return ". Channel ${selected?.label ?: index}"
    }

    private fun channelLabel(state: SenderUiState): String {
        val selected = state.channels.firstOrNull { it.index == state.channelIndex }
        return selected?.label ?: "channel ${state.channelIndex}"
    }

    private fun reloadFromDisk() {
        val bundle = store.loadBundle()
        housesByPrecinct = bundle.housesByPrecinct
        val precincts = bundle.organization.precincts
        val selected = _ui.value.precinctId.ifBlank { precincts.firstOrNull()?.id.orEmpty() }
        _ui.update {
            it.copy(
                hasSeed = true,
                organization = bundle.organization,
                precinctId = selected.uppercase(),
                houses = housesByPrecinct[selected.uppercase()].orEmpty(),
            )
        }
    }

    override fun onCleared() {
        scanJob?.cancel()
        super.onCleared()
    }
}

fun Precinct.label(): String = "$id — $name"
