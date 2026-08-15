package org.charctool.sender.protocol

object PacketCodec {
    const val PREFIX = "NS"
    const val MAX_PAYLOAD_BYTES = 233
    const val GREEN = "GREEN"
    const val YELLOW = "YELLOW"
    const val BLACK = "BLACK"
    const val RED = "RED"

    private val wire = mapOf(RED to "R", YELLOW to "Y", BLACK to "K", GREEN to "G")

    fun encodeStatus(precinctId: String, houseId: String, status: String): String {
        val p = precinctId.trim().uppercase()
        val h = houseId.trim().uppercase()
        val s = status.trim().uppercase()
        val letter = wire[s] ?: throw IllegalArgumentException("Invalid status $status")
        require(p.isNotEmpty() && h.isNotEmpty())
        return "$PREFIX:$p:$h:$letter"
    }

    fun encodeHeartbeatStart(precinctId: String): String {
        val stamp = java.time.Instant.now().toString().replace("+00:00", "Z")
        return "$PREFIX:${precinctId.trim().uppercase()}:HB:S:$stamp"
    }

    fun encodeHeartbeatEnd(precinctId: String): String =
        "$PREFIX:${precinctId.trim().uppercase()}:HB:E"

    fun encodeBulkChunks(
        precinctId: String,
        rows: List<Pair<String, String>>,
        maxBytes: Int = MAX_PAYLOAD_BYTES,
    ): List<String> {
        if (rows.isEmpty()) return emptyList()
        return chunkParts(precinctId, rows.map { housePart(it.first, it.second) }, "B", maxBytes)
    }

    fun encodeClearChunks(
        precinctId: String,
        houseIds: List<String>,
        maxBytes: Int = MAX_PAYLOAD_BYTES,
    ): List<String> {
        if (houseIds.isEmpty()) return emptyList()
        val parts = houseIds.map { housePart(it, GREEN) }
        return chunkParts(precinctId, parts, "C", maxBytes)
    }

    fun buildHeartbeatPackets(
        precinctId: String,
        nonGreen: List<Pair<String, String>>,
        recentClears: List<String>,
        maxBytes: Int = MAX_PAYLOAD_BYTES,
    ): List<String> {
        val packets = mutableListOf(encodeHeartbeatStart(precinctId))
        packets += encodeBulkChunks(precinctId, nonGreen, maxBytes)
        packets += encodeClearChunks(precinctId, recentClears, maxBytes)
        packets += encodeHeartbeatEnd(precinctId)
        return packets
    }

    fun deltaRows(
        current: List<Pair<String, String>>,
        lastSent: Map<String, String>,
    ): List<Pair<String, String>> {
        if (lastSent.isEmpty()) return current
        return current.filter { (id, status) -> lastSent[id.uppercase()] != status }
    }

    fun nonGreenRows(current: List<Pair<String, String>>): List<Pair<String, String>> =
        current.filter { it.second.uppercase() != GREEN }

    private fun housePart(houseId: String, status: String): String {
        val h = houseId.trim().uppercase()
        val letter = wire[status.trim().uppercase()]
            ?: throw IllegalArgumentException("Invalid status $status")
        return "$h$letter"
    }

    private fun chunkParts(
        precinctId: String,
        parts: List<String>,
        kind: String,
        maxBytes: Int,
    ): List<String> {
        val p = precinctId.trim().uppercase()
        val chunks = mutableListOf<List<String>>()
        var current = mutableListOf<String>()
        for (part in parts) {
            val candidate = current + part
            val packet = packetFor(p, kind, candidate)
            if (packet.toByteArray(Charsets.UTF_8).size <= maxBytes) {
                current = candidate.toMutableList()
                continue
            }
            if (current.isNotEmpty()) {
                chunks += current.toList()
                current = mutableListOf(part)
                val single = packetFor(p, kind, current)
                if (single.toByteArray(Charsets.UTF_8).size > maxBytes) {
                    throw IllegalArgumentException("Entry $part exceeds $maxBytes bytes")
                }
            } else {
                throw IllegalArgumentException("Entry $part exceeds $maxBytes bytes")
            }
        }
        if (current.isNotEmpty()) chunks += current
        return chunks.map { packetFor(p, kind, it) }
    }

    private fun packetFor(precinctId: String, kind: String, parts: List<String>): String =
        "$PREFIX:$precinctId:$kind:${parts.joinToString(",")}"
}
