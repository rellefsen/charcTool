package org.charctool.sender.mesh

data class RadioChannel(
    val index: Int,
    val name: String,
) {
    val label: String
        get() {
            val display = name.ifBlank {
                if (index == 0) "Primary" else "Channel $index"
            }
            return "$display  ($index)"
        }
}
