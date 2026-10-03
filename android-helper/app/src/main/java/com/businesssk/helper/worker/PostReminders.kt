package com.businesssk.helper.worker

import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import com.businesssk.helper.R
import com.businesssk.helper.cutout.StudioCutApi
import com.businesssk.helper.data.SettingsRepo
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** One of the day's 2 best posting times, from the Studio's Post Timing agent. */
@Serializable
data class PostSlot(val id: String, val n: Int = 1, val at: Long = 0, val label: String = "", val day: String = "",
                    val state: String = "upcoming")

@Serializable
data class PostPlan(val slots: List<PostSlot> = emptyList(), val posts_today: Int = 0, val cap: Int = 2,
                    val left_today: Int = 2, val lead_min: Int = 30, val studio: String = "")

/**
 * Post reminders — "time to post" twice a day, at the best times the Studio picked.
 *
 * Alarms live on the PHONE, so they ring even when the helper's background work is off. The plan
 * (today + 2 days) comes with every Studio poll and on app start / reboot; right before a reminder
 * the phone asks the Studio again — if that post is already done (2 a day, strictly), it stays quiet.
 */
object PostReminders {
    const val CHANNEL = "post_reminders"
    private const val PREFS = "post_reminders"
    private const val ACTION = "com.businesssk.helper.action.POST_REMINDER"
    private val json = Json { ignoreUnknownKeys = true }

    fun createChannel(context: Context) {
        val ch = NotificationChannel(CHANNEL, "Post reminders", NotificationManager.IMPORTANCE_HIGH)
        ch.description = "Twice a day: the best time to post on Instagram"
        context.getSystemService(NotificationManager::class.java)?.createNotificationChannel(ch)
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    fun saved(context: Context): PostPlan? = prefs(context).getString("plan", null)?.let {
        runCatching { json.decodeFromString(PostPlan.serializer(), it) }.getOrNull()
    }

    /** A fresh plan from the Studio → save it and (re)schedule the alarms (cheap; same plan = no-op). */
    fun update(context: Context, plan: PostPlan?) {
        if (plan == null || plan.slots.isEmpty()) return
        val raw = json.encodeToString(PostPlan.serializer(), plan)
        val p = prefs(context)
        val sig = plan.slots.joinToString { "${it.id}@${it.at}" } + "/${plan.lead_min}"
        p.edit().putString("plan", raw).putLong("plan_at", System.currentTimeMillis()).apply()
        if (p.getString("sig", "") == sig) return
        p.edit().putString("sig", sig).apply()
        schedule(context, plan)
    }

    fun reschedule(context: Context) {
        saved(context)?.let { schedule(context, it) }
    }

    private fun schedule(context: Context, plan: PostPlan) {
        val am = context.getSystemService(AlarmManager::class.java) ?: return
        val fired = prefs(context).getStringSet("fired", emptySet()) ?: emptySet()
        val now = System.currentTimeMillis()
        for (s in plan.slots) {
            val trigger = (s.at - plan.lead_min * 60L) * 1000L
            if (trigger <= now || s.id in fired) continue
            val pi = PendingIntent.getBroadcast(
                context, s.id.hashCode(),
                Intent(context, PostReminderReceiver::class.java).setAction(ACTION).putExtra("slot", s.id),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )
            val exact = Build.VERSION.SDK_INT < Build.VERSION_CODES.S || am.canScheduleExactAlarms()
            runCatching {
                if (exact) am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger, pi)
                else am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger, pi)   // within a few minutes
            }
        }
    }

    /** The alarm rang: re-check with the Studio, then remind — only if that post isn't done yet. */
    suspend fun fire(context: Context, slotId: String) {
        val p = prefs(context)
        val fired = p.getStringSet("fired", emptySet())?.toMutableSet() ?: mutableSetOf()
        if (slotId in fired) return
        val fresh = runCatching {
            val repo = SettingsRepo(context)
            val token = repo.token() ?: return@runCatching null
            StudioCutApi(repo.current().server, token).postPlan()
        }.getOrNull()
        if (fresh != null) update(context, fresh)
        val plan = fresh ?: saved(context) ?: return
        val slot = plan.slots.firstOrNull { it.id == slotId } ?: return     // the times moved → the new alarm reminds
        val today = plan.slots.firstOrNull()?.day
        if (slot.day == today && plan.posts_today >= slot.n) return        // already posted for this slot
        if (slot.day == today && plan.posts_today >= plan.cap) return      // strictly 2 a day
        fired.add(slotId)
        p.edit().putStringSet("fired", fired.sortedDescending().take(30).toSet()).apply()
        notify(context, plan, slot)
    }

    private fun notify(context: Context, plan: PostPlan, slot: PostSlot) {
        val url = plan.studio.ifBlank { "https://140-238-247-18.nip.io/#sk-post" }
        // open the Studio app (TWA) right in Content Studio → Post to IG
        val open = Intent().setClassName(context, "com.google.androidbrowserhelper.trusted.LauncherActivity")
            .setData(Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(context, slot.id.hashCode(), open,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val left = (plan.cap - plan.posts_today).coerceAtLeast(1)
        val title = "⏰ Time to post · ${slot.n} of ${plan.cap} today"
        val text = "Best time today: ${slot.label} — your audience is most active then. Open Content Studio, " +
            "pick a post and publish around ${slot.label}. $left post${if (left == 1) "" else "s"} left today."
        val n = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setColor(0xFFE2B45C.toInt())
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(text))
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .addAction(0, "Open Content Studio", pi)
            .build()
        runCatching { context.getSystemService(NotificationManager::class.java)?.notify(3000 + slot.n, n) }
    }

    /** App start / reboot: ask the Studio for the latest plan (the alarms survive without it). */
    fun refresh(context: Context) {
        val app = context.applicationContext
        CoroutineScope(Dispatchers.IO).launch {
            runCatching {
                val repo = SettingsRepo(app)
                val token = repo.token() ?: return@runCatching
                update(app, StudioCutApi(repo.current().server, token).postPlan())
            }
        }
    }
}

class PostReminderReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val slot = intent.getStringExtra("slot") ?: return
        val pending = goAsync()
        CoroutineScope(Dispatchers.IO).launch {
            try {
                PostReminders.fire(context.applicationContext, slot)
            } catch (_: Exception) {
            } finally {
                pending.finish()
            }
        }
    }
}
