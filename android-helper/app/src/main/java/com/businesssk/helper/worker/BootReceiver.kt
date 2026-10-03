package com.businesssk.helper.worker

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.businesssk.helper.data.SettingsRepo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

/** After a reboot (or an app update) the helper comes back on by itself — if it was on before. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                PostReminders.reschedule(context.applicationContext)    // alarms don't survive a reboot
                PostReminders.refresh(context.applicationContext)
                val s = SettingsRepo(context.applicationContext).current()
                if (s.enabled && s.paired) WorkerService.start(context.applicationContext)
            } catch (_: Exception) {
            } finally {
                pending.finish()
            }
        }
    }
}
