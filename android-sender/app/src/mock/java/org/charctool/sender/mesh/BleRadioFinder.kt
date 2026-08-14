package org.charctool.sender.mesh

import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow

object BleRadioFinder {
    fun scan(): Flow<List<BleRadio>> = flow {
        emit(emptyList())
    }
}
