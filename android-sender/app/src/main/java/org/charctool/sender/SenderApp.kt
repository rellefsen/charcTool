package org.charctool.sender

import android.app.Application

class SenderApp : Application() {
    override fun onCreate() {
        super.onCreate()
        MeshSdkInit.init(this)
    }
}
