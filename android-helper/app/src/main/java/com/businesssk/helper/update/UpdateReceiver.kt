package com.businesssk.helper.update

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** Installer results: shows Android's confirm screen when needed, reports failures to the UI. */
class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= 33) intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                              else @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
                if (confirm != null) {
                    UpdateEvents.post("Confirm the update on the next screen")
                    context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                }
            }
            PackageInstaller.STATUS_SUCCESS -> UpdateEvents.post("Updated")     // the app restarts itself
            else -> {
                val msg = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)
                UpdateEvents.post(if (status == PackageInstaller.STATUS_FAILURE_ABORTED) "Update cancelled"
                                  else "Update failed${msg?.let { ": $it" } ?: ""}", failed = true)
            }
        }
    }
}

object UpdateEvents {
    data class Event(val message: String, val failed: Boolean, val at: Long = System.currentTimeMillis())
    private val _last = MutableStateFlow<Event?>(null)
    val last: StateFlow<Event?> = _last.asStateFlow()
    fun post(message: String, failed: Boolean = false) { _last.value = Event(message, failed) }
}
