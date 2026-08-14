package org.charctool.sender.mesh

data class BleRadio(
    val address: String,
    val name: String,
    val rssi: Int,
) {
    val label: String
        get() = if (name.isBlank() || name.equals(address, ignoreCase = true)) {
            address
        } else {
            "$name  $address"
        }

    companion object {
        private val MAC = Regex("^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
        private val MAC_COMPACT = Regex("^[0-9A-Fa-f]{12}$")

        fun normalizeMac(raw: String): String? {
            val trimmed = raw.trim()
            if (MAC.matches(trimmed)) return trimmed.uppercase()
            val compact = trimmed.replace("[:-]".toRegex(), "")
            if (!MAC_COMPACT.matches(compact)) return null
            return compact.chunked(2).joinToString(":").uppercase()
        }
    }
}
