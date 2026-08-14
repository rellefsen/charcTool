package org.charctool.sender

import org.charctool.sender.protocol.HouseRow
import org.charctool.sender.protocol.PacketCodec
import org.charctool.sender.protocol.SeedParser
import java.io.File
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter

class SeedStore(private val root: File) {
    private val orgFile = File(root, "organization.json")
    private val lastSyncDir = File(root, "last_sync")
    private val clearsFile = File(root, "recent_clears.json")

    fun hasSeed(): Boolean = orgFile.exists()

    fun saveBundle(orgJson: String, houses: Map<String, List<HouseRow>>) {
        root.mkdirs()
        orgFile.writeText(orgJson)
        for ((precinctId, rows) in houses) {
            val dir = File(root, "precincts/${precinctId.uppercase()}")
            dir.mkdirs()
            File(dir, "neighborhood_status.csv").writeText(SeedParser.statusCsv(rows))
            val addr = StringBuilder("house_id,address\n")
            for (row in rows.sortedBy { it.houseId }) {
                addr.append(row.houseId).append(',').append(row.address).append('\n')
            }
            File(dir, "house_addresses.csv").writeText(addr.toString())
        }
    }

    fun loadBundle() = SeedParser.parseDirectory(root)

    fun writeStatus(precinctId: String, rows: List<HouseRow>) {
        val dir = File(root, "precincts/${precinctId.uppercase()}")
        dir.mkdirs()
        File(dir, "neighborhood_status.csv").writeText(SeedParser.statusCsv(rows))
    }

    fun readLastSync(precinctId: String): Map<String, String> {
        val file = File(lastSyncDir, "${precinctId.uppercase()}.csv")
        if (!file.exists()) return emptyMap()
        return SeedParser.parseStatusCsv(file.readText()).mapValues { it.value.first }
    }

    fun writeLastSync(precinctId: String, rows: List<HouseRow>) {
        lastSyncDir.mkdirs()
        File(lastSyncDir, "${precinctId.uppercase()}.csv").writeText(SeedParser.statusCsv(rows))
    }

    fun recordClear(precinctId: String, houseId: String) {
        val all = readClears().toMutableMap()
        val list = all.getOrPut(precinctId.uppercase()) { mutableListOf() }
        val id = houseId.uppercase()
        if (id !in list) list += id
        writeClears(all)
    }

    fun takeClears(precinctId: String): List<String> {
        val all = readClears().toMutableMap()
        val taken = all.remove(precinctId.uppercase()).orEmpty()
        writeClears(all)
        return taken
    }

    private fun readClears(): MutableMap<String, MutableList<String>> {
        if (!clearsFile.exists()) return mutableMapOf()
        val text = clearsFile.readText().trim()
        if (text.isEmpty()) return mutableMapOf()
        val out = mutableMapOf<String, MutableList<String>>()
        for (line in text.lineSequence()) {
            val parts = line.split('=')
            if (parts.size == 2) {
                out[parts[0]] = parts[1].split(',').filter { it.isNotBlank() }.toMutableList()
            }
        }
        return out
    }

    private fun writeClears(map: Map<String, List<String>>) {
        clearsFile.writeText(
            map.entries.joinToString("\n") { "${it.key}=${it.value.joinToString(",")}" },
        )
    }

    companion object {
        fun nowUtc(): String =
            DateTimeFormatter.ISO_INSTANT.format(Instant.now().atOffset(ZoneOffset.UTC))
                .replace("+00:00", "Z")
    }
}

fun HouseRow.withStatus(status: String): HouseRow =
    copy(status = status.uppercase(), timestamp = SeedStore.nowUtc())

fun List<HouseRow>.pairs(): List<Pair<String, String>> =
    map { it.houseId to it.status }

fun List<HouseRow>.deltaPackets(precinctId: String, lastSent: Map<String, String>): List<String> {
    val delta = PacketCodec.deltaRows(pairs(), lastSent)
    if (delta.isEmpty()) return emptyList()
    return if (delta.size == 1) {
        listOf(PacketCodec.encodeStatus(precinctId, delta[0].first, delta[0].second))
    } else {
        PacketCodec.encodeBulkChunks(precinctId, delta)
    }
}
