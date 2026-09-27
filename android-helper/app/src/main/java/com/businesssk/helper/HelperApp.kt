package com.businesssk.helper

import android.app.Application
import com.businesssk.helper.data.ActivityLog
import com.businesssk.helper.worker.Notifications

class HelperApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Notifications.createChannel(this)
        ActivityLog.init(this)
    }
}
