package com.businesssk.helper.worker

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat
import com.businesssk.helper.MainActivity
import com.businesssk.helper.R

object Notifications {
    const val CHANNEL = "helper_status"
    const val ALERTS = "studio_alerts"
    const val ID = 1605

    fun createChannel(context: Context) {
        val ch = NotificationChannel(CHANNEL, context.getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW)
        ch.description = context.getString(R.string.channel_desc)
        ch.setShowBadge(false)
        context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(ch)
        // GPU Watchdog alerts (e.g. "Colab needed") — these should actually get your attention
        val al = NotificationChannel(ALERTS, "Studio alerts", NotificationManager.IMPORTANCE_HIGH)
        al.description = "When Google Colab needs a tap to render your posts"
        context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(al)
    }

    /** A tappable alert: opens `url` (the Colab notebook) in the browser. */
    fun alert(context: Context, key: String, text: String, url: String) {
        runCatching {
            val open = PendingIntent.getActivity(
                context, key.hashCode(),
                Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url.ifBlank { "https://colab.research.google.com" }))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val n = NotificationCompat.Builder(context, ALERTS)
                .setSmallIcon(R.drawable.ic_notification)
                .setColor(0xFFE2B45C.toInt())
                .setContentTitle("Business-SK · render GPU")
                .setContentText(text)
                .setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setAutoCancel(true)
                .setContentIntent(open)
                .addAction(0, "Open Colab", open)
                .build()
            context.getSystemService(NotificationManager::class.java)?.notify(2000 + (key.hashCode() and 0xFFF), n)
        }
    }

    fun build(context: Context, title: String, text: String): Notification {
        val open = PendingIntent.getActivity(
            context, 0, Intent(context, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val stop = PendingIntent.getService(
            context, 1, Intent(context, WorkerService::class.java).setAction(WorkerService.ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(0xFFE2B45C.toInt())
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .setContentIntent(open)
            .addAction(0, "Turn off", stop)
            .build()
    }

    fun update(context: Context, title: String, text: String) {
        runCatching {
            context.getSystemService(NotificationManager::class.java)?.notify(ID, build(context, title, text))
        }
    }
}
