package org.charctool.sender.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.ByteArrayInputStream
import java.io.File
import java.util.zip.ZipInputStream

@Serializable
data class Organization(
    val districts: List<District> = emptyList(),
    val precincts: List<Precinct> = emptyList(),
)

@Serializable
data class District(
    val id: String,
    val name: String,
)

@Serializable
data class Precinct(
    val id: String,
    @SerialName("district_id") val districtId: String,
    val name: String,
)

data class HouseRow(
    val houseId: String,
    val address: String,
    val status: String,
    val timestamp: String,
)

data class SeedBundle(
    val organization: Organization,
    val housesByPrecinct: Map<String, List<HouseRow>>,
)

object SeedParser {
    private val json = Json { ignoreUnknownKeys = true }

    fun parseOrganization(text: String): Organization =
        json.decodeFromString(Organization.serializer(), text)

    fun parseAddressCsv(text: String): Map<String, String> {
        val lines = text.lineSequence().map { it.trim() }.filter { it.isNotEmpty() }.toList()
        if (lines.isEmpty()) return emptyMap()
        val header = splitCsv(lines.first()).map { it.lowercase() }
        val idIdx = header.indexOf("house_id").takeIf { it >= 0 } ?: 0
        val addrIdx = header.indexOf("address").takeIf { it >= 0 } ?: 1
        val out = linkedMapOf<String, String>()
        for (line in lines.drop(1)) {
            val cols = splitCsv(line)
            val id = cols.getOrNull(idIdx)?.trim()?.uppercase().orEmpty()
            if (id.isNotEmpty()) {
                out[id] = cols.getOrNull(addrIdx)?.trim().orEmpty()
            }
        }
        return out
    }

    fun parseStatusCsv(text: String): Map<String, Pair<String, String>> {
        val lines = text.lineSequence().map { it.trim() }.filter { it.isNotEmpty() }.toList()
        if (lines.isEmpty()) return emptyMap()
        val header = splitCsv(lines.first()).map { it.lowercase() }
        val idIdx = header.indexOf("house_id").takeIf { it >= 0 } ?: 0
        val statusIdx = header.indexOf("status_code").takeIf { it >= 0 } ?: 1
        val tsIdx = header.indexOf("timestamp").takeIf { it >= 0 } ?: 2
        val out = linkedMapOf<String, Pair<String, String>>()
        for (line in lines.drop(1)) {
            val cols = splitCsv(line)
            val id = cols.getOrNull(idIdx)?.trim()?.uppercase().orEmpty()
            if (id.isNotEmpty()) {
                val status = cols.getOrNull(statusIdx)?.trim()?.uppercase() ?: PacketCodec.GREEN
                val ts = cols.getOrNull(tsIdx)?.trim().orEmpty()
                out[id] = status to ts
            }
        }
        return out
    }

    fun mergeHouses(
        addresses: Map<String, String>,
        statuses: Map<String, Pair<String, String>>,
    ): List<HouseRow> {
        val ids = (addresses.keys + statuses.keys).sorted()
        return ids.map { id ->
            val (status, ts) = statuses[id] ?: (PacketCodec.GREEN to "")
            HouseRow(id, addresses[id].orEmpty(), status, ts)
        }
    }

    fun parseZip(bytes: ByteArray): SeedBundle {
        val files = linkedMapOf<String, String>()
        ZipInputStream(ByteArrayInputStream(bytes)).use { zip ->
            while (true) {
                val entry = zip.nextEntry ?: break
                if (entry.isDirectory) continue
                files[entry.name.replace('\\', '/')] = zip.readBytes().toString(Charsets.UTF_8)
            }
        }
        return parseFileMap(files)
    }

    fun parseDirectory(root: File): SeedBundle {
        val files = linkedMapOf<String, String>()
        root.walkTopDown().filter { it.isFile }.forEach { file ->
            val rel = file.relativeTo(root).path.replace('\\', '/')
            files[rel] = file.readText()
        }
        return parseFileMap(files)
    }

    fun parseFileMap(files: Map<String, String>): SeedBundle {
        val orgPath = files.keys.firstOrNull { it.endsWith("organization.json", ignoreCase = true) }
            ?: throw IllegalArgumentException("Zip/folder must include organization.json")
        val org = parseOrganization(files.getValue(orgPath))
        val houses = mutableMapOf<String, List<HouseRow>>()
        for (precinct in org.precincts) {
            val id = precinct.id.uppercase()
            val addrFile = files.entries.firstOrNull {
                matchesPrecinctPath(it.key, id) &&
                    it.key.endsWith("house_addresses.csv", ignoreCase = true)
            }
            val statusFile = files.entries.firstOrNull {
                matchesPrecinctPath(it.key, id) &&
                    it.key.endsWith("neighborhood_status.csv", ignoreCase = true)
            }
            val addresses = addrFile?.let { parseAddressCsv(it.value) }.orEmpty()
            val statuses = statusFile?.let { parseStatusCsv(it.value) }.orEmpty()
            houses[id] = mergeHouses(addresses, statuses)
        }
        return SeedBundle(org, houses)
    }

    fun statusCsv(rows: List<HouseRow>): String {
        val sb = StringBuilder("house_id,status_code,timestamp\n")
        for (row in rows.sortedBy { it.houseId }) {
            sb.append(row.houseId).append(',')
                .append(row.status).append(',')
                .append(row.timestamp).append('\n')
        }
        return sb.toString()
    }

    private fun splitCsv(line: String): List<String> {
        val out = mutableListOf<String>()
        val cur = StringBuilder()
        var inQuotes = false
        var i = 0
        while (i < line.length) {
            when (val ch = line[i]) {
                '"' -> inQuotes = !inQuotes
                ',' -> if (inQuotes) cur.append(ch) else {
                    out += cur.toString()
                    cur.clear()
                }
                else -> cur.append(ch)
            }
            i++
        }
        out += cur.toString()
        return out
    }

    private fun matchesPrecinctPath(path: String, precinctId: String): Boolean {
        val normalized = path.replace('\\', '/')
        return normalized.contains("/$precinctId/", ignoreCase = true) ||
            normalized.startsWith("$precinctId/", ignoreCase = true)
    }
}
