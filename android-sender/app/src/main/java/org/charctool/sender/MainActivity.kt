package org.charctool.sender

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import org.charctool.sender.protocol.HouseRow
import org.charctool.sender.protocol.PacketCodec
import org.charctool.sender.protocol.Precinct

class MainActivity : ComponentActivity() {
    private val viewModel: SenderViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                val state by viewModel.ui.collectAsStateWithLifecycle()
                val zipLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.GetContent(),
                ) { uri ->
                    if (uri != null) {
                        contentResolver.openInputStream(uri)?.use { stream ->
                            viewModel.importZip(stream.readBytes())
                        }
                    }
                }
                val permissionLauncher = rememberLauncherForActivityResult(
                    ActivityResultContracts.RequestMultiplePermissions(),
                ) { granted ->
                    if (granted.values.all { it }) {
                        when (state.pendingBleAction) {
                            "scan" -> viewModel.startScan()
                            else -> viewModel.connectRadio()
                        }
                        viewModel.clearPendingBleAction()
                    }
                }

                fun withBlePermission(action: String, run: () -> Unit) {
                    if (state.mockRadio) {
                        run()
                        return
                    }
                    val needed = blePermissions()
                    val missing = needed.filter {
                        ContextCompat.checkSelfPermission(this, it) !=
                            PackageManager.PERMISSION_GRANTED
                    }
                    if (missing.isEmpty()) run()
                    else {
                        viewModel.setPendingBleAction(action)
                        permissionLauncher.launch(missing.toTypedArray())
                    }
                }

                SenderScreen(
                    state = state,
                    onImport = { zipLauncher.launch("application/zip") },
                    onSelectPrecinct = viewModel::selectPrecinct,
                    onStatus = viewModel::setStatus,
                    onMock = viewModel::setMockRadio,
                    onChannel = viewModel::selectChannel,
                    onDelay = viewModel::setPacketDelayMs,
                    onScan = {
                        withBlePermission("scan") { viewModel.startScan() }
                    },
                    onStopScan = viewModel::stopScan,
                    onSelectRadio = viewModel::selectRadio,
                    onConnect = {
                        withBlePermission("connect") { viewModel.connectRadio() }
                    },
                    onSend = viewModel::sendChanges,
                    onHeartbeat = viewModel::sendHeartbeat,
                )
            }
        }
    }

    private fun blePermissions(): List<String> {
        val perms = mutableListOf<String>()
        if (Build.VERSION.SDK_INT >= 31) {
            perms += Manifest.permission.BLUETOOTH_SCAN
            perms += Manifest.permission.BLUETOOTH_CONNECT
        } else {
            perms += Manifest.permission.ACCESS_FINE_LOCATION
            perms += Manifest.permission.BLUETOOTH
            perms += Manifest.permission.BLUETOOTH_ADMIN
        }
        return perms
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SenderScreen(
    state: SenderUiState,
    onImport: () -> Unit,
    onSelectPrecinct: (String) -> Unit,
    onStatus: (String, String) -> Unit,
    onMock: (Boolean) -> Unit,
    onChannel: (Int) -> Unit,
    onDelay: (Long) -> Unit,
    onScan: () -> Unit,
    onStopScan: () -> Unit,
    onSelectRadio: (org.charctool.sender.mesh.BleRadio) -> Unit,
    onConnect: () -> Unit,
    onSend: () -> Unit,
    onHeartbeat: () -> Unit,
) {
    Scaffold(
        topBar = { TopAppBar(title = { Text("Block Status") }) },
    ) { padding ->
        var setupOpen by remember { mutableStateOf(!state.hasSeed || !state.radioConnected) }
        LaunchedEffect(state.scanning) {
            if (state.scanning) setupOpen = true
        }
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(
                state.statusMessage,
                style = MaterialTheme.typography.bodySmall,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(onClick = { setupOpen = !setupOpen }) {
                    Text(if (setupOpen) "Hide setup" else "Setup")
                }
                Button(
                    onClick = onSend,
                    enabled = !state.busy && state.hasSeed,
                    modifier = Modifier.weight(1f),
                ) { Text("Send") }
                OutlinedButton(
                    onClick = onHeartbeat,
                    enabled = !state.busy && state.hasSeed,
                    modifier = Modifier.weight(1f),
                ) { Text("Heartbeat") }
            }
            AnimatedVisibility(visible = setupOpen) {
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(
                        modifier = Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = onImport, enabled = !state.busy, modifier = Modifier.weight(1f)) {
                                Text("Import seed")
                            }
                            OutlinedButton(onClick = onConnect, enabled = !state.busy, modifier = Modifier.weight(1f)) {
                                Text(if (state.radioConnected) "Reconnect" else "Connect")
                            }
                        }
                        RadioSettings(state, onMock, onDelay, onScan, onStopScan, onSelectRadio)
                    }
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PrecinctPicker(state, onSelectPrecinct, Modifier.weight(1f))
                ChannelPicker(state, onChannel, Modifier.weight(1f))
            }
            Text(
                if (state.houses.isEmpty()) "No houses loaded" else "${state.houses.size} houses",
                style = MaterialTheme.typography.labelMedium,
            )
            LazyColumn(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                items(state.houses, key = { it.houseId }) { house ->
                    HouseCard(house, enabled = !state.busy, onStatus = onStatus)
                }
            }
        }
    }
}

@Composable
private fun PrecinctPicker(state: SenderUiState, onSelect: (String) -> Unit, modifier: Modifier = Modifier) {
    val precincts = state.organization?.precincts.orEmpty()
    if (precincts.isEmpty()) {
        Text("Import a seed zip first.", style = MaterialTheme.typography.bodySmall, modifier = modifier)
        return
    }
    var expanded by remember { mutableStateOf(false) }
    val current = precincts.firstOrNull { it.id.equals(state.precinctId, true) }
        ?: precincts.first()
    Column(modifier) {
        OutlinedButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) {
            Text(current.id, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            precincts.forEach { precinct: Precinct ->
                DropdownMenuItem(
                    text = { Text(precinct.label()) },
                    onClick = {
                        expanded = false
                        onSelect(precinct.id)
                    },
                )
            }
        }
    }
}

@Composable
private fun RadioSettings(
    state: SenderUiState,
    onMock: (Boolean) -> Unit,
    onDelay: (Long) -> Unit,
    onScan: () -> Unit,
    onStopScan: () -> Unit,
    onSelectRadio: (org.charctool.sender.mesh.BleRadio) -> Unit,
) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("Mock", style = MaterialTheme.typography.bodySmall)
        Switch(checked = state.mockRadio, onCheckedChange = onMock)
        Text(
            state.radioLabel,
            style = MaterialTheme.typography.bodySmall,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.weight(1f),
        )
    }
    OutlinedTextField(
        value = (state.packetDelayMs / 1000.0).toString(),
        onValueChange = { onDelay(((it.toDoubleOrNull() ?: 2.0) * 1000).toLong()) },
        label = { Text("Delay seconds") },
        modifier = Modifier.fillMaxWidth(),
        singleLine = true,
    )
    if (!state.mockRadio) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            if (state.scanning) {
                OutlinedButton(onClick = onStopScan, enabled = !state.busy) { Text("Stop scan") }
            } else {
                Button(onClick = onScan, enabled = !state.busy) { Text("Scan radios") }
            }
        }
        if (state.radios.isEmpty() && state.scanning) {
            Text("Looking for Meshtastic radios…", style = MaterialTheme.typography.bodySmall)
        }
        state.radios.forEach { radio ->
            val selected = radio.address.equals(state.bleAddress, ignoreCase = true)
            FilterChip(
                selected = selected,
                onClick = { onSelectRadio(radio) },
                enabled = !state.busy,
                label = { Text("${radio.label}  ${radio.rssi} dBm") },
            )
        }
        if (state.bleAddress.isNotBlank()) {
            Text("Selected: ${state.bleAddress}", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun ChannelPicker(state: SenderUiState, onSelect: (Int) -> Unit, modifier: Modifier = Modifier) {
    val channels = state.channels
    if (channels.isEmpty()) {
        Text(
            "Connect for channels",
            style = MaterialTheme.typography.bodySmall,
            modifier = modifier,
        )
        return
    }
    var expanded by remember { mutableStateOf(false) }
    val current = channels.firstOrNull { it.index == state.channelIndex } ?: channels.first()
    Column(modifier) {
        OutlinedButton(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth()) {
            Text(current.name.ifBlank { current.label }, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            channels.forEach { channel ->
                DropdownMenuItem(
                    text = { Text(channel.label) },
                    onClick = {
                        expanded = false
                        onSelect(channel.index)
                    },
                )
            }
        }
    }
}

@Composable
private fun HouseCard(
    house: HouseRow,
    enabled: Boolean,
    onStatus: (String, String) -> Unit,
) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Column(modifier = Modifier.weight(1f)) {
            Text(house.houseId, style = MaterialTheme.typography.titleSmall)
            Text(
                house.address.ifBlank { "—" },
                style = MaterialTheme.typography.bodySmall,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        }
        StatusChip("R", "RED", house.status, Color(0xFFDC2626), enabled) {
            onStatus(house.houseId, PacketCodec.RED)
        }
        StatusChip("Y", "YELLOW", house.status, Color(0xFFEAB308), enabled) {
            onStatus(house.houseId, PacketCodec.YELLOW)
        }
        StatusChip(
            "K",
            "BLACK",
            house.status,
            Color(0xFF111827),
            enabled,
            selectedLabelColor = Color(0xFFF9FAFB),
            selectedContainerAlpha = 1f,
        ) {
            onStatus(house.houseId, PacketCodec.BLACK)
        }
        StatusChip("G", "GREEN", house.status, Color(0xFF16A34A), enabled) {
            onStatus(house.houseId, PacketCodec.GREEN)
        }
    }
}

@Composable
private fun StatusChip(
    shortLabel: String,
    fullLabel: String,
    current: String,
    color: Color,
    enabled: Boolean,
    selectedLabelColor: Color = color,
    selectedContainerAlpha: Float = 0.25f,
    onClick: () -> Unit,
) {
    FilterChip(
        selected = current.equals(fullLabel, ignoreCase = true),
        onClick = onClick,
        enabled = enabled,
        label = { Text(shortLabel) },
        colors = androidx.compose.material3.FilterChipDefaults.filterChipColors(
            selectedContainerColor = color.copy(alpha = selectedContainerAlpha),
            selectedLabelColor = selectedLabelColor,
        ),
    )
}
