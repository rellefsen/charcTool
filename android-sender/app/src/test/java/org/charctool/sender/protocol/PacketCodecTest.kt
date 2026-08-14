package org.charctool.sender.protocol

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PacketCodecTest {
    @Test
    fun encodeSingleHouse() {
        assertEquals("NS:SOUTH01:H014:Y", PacketCodec.encodeStatus("south01", "h014", "YELLOW"))
    }

    @Test
    fun heartbeatSequence() {
        val packets = PacketCodec.buildHeartbeatPackets(
            "CHARC01",
            listOf("H001" to "RED", "H014" to "YELLOW"),
            listOf("H003"),
        )
        assertEquals("NS:CHARC01:HB:S", packets.first())
        assertEquals("NS:CHARC01:HB:E", packets.last())
        assertTrue(packets.any { it.startsWith("NS:CHARC01:B:") })
        assertTrue(packets.any { it.startsWith("NS:CHARC01:C:") && it.contains("H003G") })
    }

    @Test
    fun deltaSkipsUnchanged() {
        val current = listOf("H001" to "GREEN", "H002" to "RED")
        val last = mapOf("H001" to "GREEN", "H002" to "GREEN")
        assertEquals(listOf("H002" to "RED"), PacketCodec.deltaRows(current, last))
    }
}

class SeedParserTest {
    @Test
    fun parseOrgAndCsv() {
        val org = SeedParser.parseOrganization(
            """{"districts":[{"id":"CHARC","name":"North"}],"precincts":[{"id":"CHARC01","district_id":"CHARC","name":"P1"}]}""",
        )
        assertEquals("CHARC01", org.precincts.single().id)
        val houses = SeedParser.mergeHouses(
            SeedParser.parseAddressCsv("house_id,address\nH001,1 Oak St\n"),
            SeedParser.parseStatusCsv("house_id,status_code,timestamp\nH001,GREEN,2026-08-14T05:21:42Z\n"),
        )
        assertEquals("1 Oak St", houses.single().address)
        assertEquals("GREEN", houses.single().status)
    }
}
