package com.businesssk.helper

import android.app.Application
import com.businesssk.helper.data.ActivityLog
import com.businesssk.helper.worker.Notifications
import com.businesssk.helper.worker.PostReminders

class HelperApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Notifications.createChannel(this)
        PostReminders.createChannel(this)
        ActivityLog.init(this)
        PostReminders.refresh(this)                 // latest best posting times → the 2 daily reminders
    }
}
