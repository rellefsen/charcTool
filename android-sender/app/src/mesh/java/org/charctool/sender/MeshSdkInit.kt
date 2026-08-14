package org.charctool.sender

import android.app.Application
import org.meshtastic.sdk.storage.sqldelight.AndroidContextHolder

object MeshSdkInit {
    fun init(app: Application) {
        AndroidContextHolder.context = app.applicationContext
    }
}
