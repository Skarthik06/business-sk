package com.businesssk.helper.worker

import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.PowerManager
import android.util.Base64
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleService
import androidx.lifecycle.lifecycleScope
import com.businesssk.helper.BuildConfig
import com.businesssk.helper.cutout.Cutter
import com.businesssk.helper.cutout.StudioCutApi
import com.businesssk.helper.data.ActivityLog
import com.businesssk.helper.data.HelperSettings
import com.businesssk.helper.data.LogEntry
import com.businesssk.helper.data.SettingsRepo
import com.businesssk.helper.data.Usage
import com.businesssk.helper.net.ApiException
import com.businesssk.helper.net.Beat
import com.businesssk.helper.net.Job
import com.businesssk.helper.net.JobResult
import com.businesssk.helper.net.PageFetcher
import com.businesssk.helper.net.ServerApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.ByteArrayOutputStream
import java.util.concurrent.atomic.AtomicInteger
import java.util.zip.GZIPOutputStream
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import kotlinx.coroutines.Job as CoJob

/**
 * The phone as a Scraper API route. Runs as a foreground service ("specialUse": Android 15 limits
 * dataSync services to 6 h/day). Outbound HTTPS only: heartbeat every 20 s + long-poll for jobs.
 */
class WorkerService : LifecycleService() {

    companion object {
        const val ACTION_STOP = "com.businesssk.helper.action.STOP"

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, WorkerService::class.java))
        }

        fun stop(context: Context) {
            context.startService(Intent(context, WorkerService::class.java).setAction(ACTION_STOP))
        }
    }

    private lateinit var repo: SettingsRepo
    private var loop: CoJob? = null
    private val active = AtomicInteger(0)

    override fun onCreate() {
        super.onCreate()
        repo = SettingsRepo(applicationContext)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        super.onStartCommand(intent, flags, startId)
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, Notifications.ID, Notifications.build(this, "SK Helper", "Starting…"), type)
        if (intent?.action == ACTION_STOP) {
            lifecycleScope.launch {
                repo.setEnabled(false)
                WorkerState.set(Phase.OFF)
                ServiceCompat.stopForeground(this@WorkerService, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
            return START_NOT_STICKY
        }
        if (loop?.isActive != true) {
            WorkerState.set(Phase.STARTING, "Connecting…")
            loop = lifecycleScope.launch(Dispatchers.IO) { run() }
        }
        return START_STICKY
    }

    override fun onDestroy() {
        loop?.cancel()
        if (WorkerState.status.value.phase != Phase.OFF) WorkerState.set(Phase.OFF)
        super.onDestroy()
    }

    // ── main loop ─────────────────────────────────────────────────────────────────────────────
    private suspend fun run() = coroutineScope {
        launch { heartbeats() }
        launch { cutouts() }
        var failures = 0
        while (isActive) {
            val s = repo.current()
            val token = repo.token()
            if (!s.enabled) { stopSelf(); return@coroutineScope }
            if (!s.paired || token == null) {
                show(Phase.OFFLINE, "Not paired — open the app to pair"); delay(15_000); continue
            }
            val api = ServerApi(s.server, token)
            val usage = repo.usageNow()
            val device = Policy.device(this@WorkerService)
            val decision = Policy.evaluate(s, device, usage.bytes)
            try {
                if (!decision.ok) {
                    WorkerState.setIp(api.heartbeat(beat(s, usage, device, decision.reason)))
                    show(Phase.PAUSED, decision.reason ?: "Paused")
                    delay(20_000)
                    continue
                }
                if (WorkerState.status.value.phase != Phase.WORKING) show(Phase.READY, readyText(usage))
                val jobs = api.lease(beat(s, usage, device, null))           // long-poll ≤ ~20 s
                for (job in jobs) runJob(api, s, job)
                failures = 0
            } catch (e: ApiException) {
                if (e.code == 401 || e.code == 403) {
                    show(Phase.OFFLINE, "This phone was unpaired in the Studio — pair again"); delay(60_000)
                } else {
                    failures++; show(Phase.OFFLINE, "Server error (${e.code}) — retrying"); delay(backoff(failures))
                }
            } catch (e: Exception) {
                failures++
                show(Phase.OFFLINE, "Can't reach the server — retrying")
                delay(backoff(failures))
            }
        }
    }

    private suspend fun heartbeats() {
        while (true) {
            delay(20_000)
            runCatching {
                val s = repo.current()
                val token = repo.token() ?: return@runCatching
                if (!s.paired) return@runCatching
                val usage = repo.usageNow()
                val device = Policy.device(this)
                val paused = Policy.evaluate(s, device, usage.bytes).reason
                WorkerState.setIp(ServerApi(s.server, token).heartbeat(beat(s, usage, device, paused)))
            }
        }
    }

    private fun backoff(failures: Int) = minOf(30_000L, 3_000L * failures)

    // ── product cut-outs (ML Kit) — only while the laptop GPU is off ──────────────────────────
    // Efficient by design: one tiny request every 20 s while the laptop GPU is off (60 s while it's
    // on — then the laptop does every cut-out), same data/battery/network rules as scraping, and
    // every downloaded + uploaded byte counts toward the daily limit.
    private suspend fun cutouts() {
        while (true) {
            var wait = 60_000L
            runCatching {
                val s = repo.current()
                val token = repo.token() ?: return@runCatching
                if (!s.paired || !s.enabled || !s.cutouts) return@runCatching
                val usage = repo.usageNow()
                if (!Policy.evaluate(s, Policy.device(this), usage.bytes).ok) return@runCatching
                val api = StudioCutApi(s.server, token)
                val res = api.jobs(2, BuildConfig.VERSION_NAME)
                wait = if (res.laptop_gpu) 60_000L else 20_000L
                for (job in res.jobs) {
                    if (job.url.isBlank()) continue
                    if (!doCutout(api, job.id, job.url)) break
                }
                if (res.jobs.isNotEmpty()) wait = 2_000L        // more may be queued for this post
            }
            delay(wait)
        }
    }

    private suspend fun doCutout(api: StudioCutApi, jobId: String, url: String): Boolean {
        val host = url.toHttpUrlOrNull()?.host ?: "?"
        show(Phase.WORKING, "Cutting out a product", host)
        active.incrementAndGet()
        return try {
            val r = Cutter.cut(url)
            val sent = api.result(jobId, r.png, r.facts)
            repo.addUsage(r.downloaded + sent, 0)
            ActivityLog.add(LogEntry(System.currentTimeMillis(), "✂️ Cut-out · ML Kit", host, 200, r.downloaded + sent,
                r.ms, true, "${r.facts.subject} · ${r.png.size / 1024} KB", WorkerState.ip.value))
            true
        } catch (e: Exception) {
            ActivityLog.add(LogEntry(System.currentTimeMillis(), "✂️ Cut-out · ML Kit", host, null, 0, 0, false,
                e.message?.take(80) ?: "failed", WorkerState.ip.value))
            false
        } finally {
            active.decrementAndGet()
            show(Phase.READY, readyText(repo.usageNow()))
        }
    }

    private fun beat(s: HelperSettings, u: Usage, d: Policy.Device, pausedReason: String?) = Beat(
        worker_id = s.workerId,
        status = if (pausedReason != null) "paused" else "healthy",
        version = BuildConfig.VERSION_NAME,
        active_jobs = active.get(),
        battery_percent = d.batteryPercent.takeIf { it >= 0 }?.toDouble(),
        charging = d.charging,
        network = d.network,
        data_today_mb = u.megabytes,
        jobs_today = u.jobs,
        allowed_domains = s.allowed.toList(),
        paused_reason = pausedReason,
        wait = if (pausedReason != null) 0.0 else 20.0,
    )

    // ── one job ───────────────────────────────────────────────────────────────────────────────
    private suspend fun runJob(api: ServerApi, s: HelperSettings, job: Job) {
        val url = job.url.toHttpUrlOrNull()
        val host = url?.host ?: "?"
        show(Phase.WORKING, "Fetching $host", host)
        active.incrementAndGet()
        val wake = (getSystemService(POWER_SERVICE) as PowerManager)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "BusinessSKHelper:job")
        wake.acquire(3 * 60 * 1000L)
        val fetcher = PageFetcher(s.allowed)
        try {
            val page = fetcher.fetch(job.url, job.timeout)
            val payload = if (page.body.isNotEmpty()) Base64.encodeToString(gzip(page.body), Base64.NO_WRAP) else ""
            val (sent, ip) = api.result(job.attempt_id, JobResult(
                worker_id = s.workerId, status_code = page.status, content_type = page.contentType,
                final_url = page.finalUrl, html = payload, gz = true, error = page.error, duration_ms = page.ms,
            ))
            WorkerState.setIp(ip)
            repo.addUsage(fetcher.networkBytes.get() + sent, 1)
            val ok = page.status == 200 && page.error == null
            ActivityLog.add(LogEntry(
                at = System.currentTimeMillis(), host = host, path = url?.encodedPath?.take(60) ?: "",
                status = page.status, bytes = fetcher.networkBytes.get(), ms = page.ms, ok = ok,
                note = page.error ?: if (page.body.size < 20_000 && page.status == 200) "short page" else "",
                ip = ip.ifBlank { WorkerState.ip.value },
            ))
        } catch (e: Exception) {
            repo.addUsage(fetcher.networkBytes.get(), 0)
            ActivityLog.add(LogEntry(System.currentTimeMillis(), host, url?.encodedPath?.take(60) ?: "",
                null, fetcher.networkBytes.get(), 0, false, "upload failed: ${e.message?.take(80)}"))
        } finally {
            active.decrementAndGet()
            if (wake.isHeld) wake.release()
            show(Phase.READY, readyText(repo.usageNow()))
        }
    }

    private fun gzip(bytes: ByteArray): ByteArray {
        val out = ByteArrayOutputStream(bytes.size / 6 + 64)
        GZIPOutputStream(out).use { it.write(bytes) }
        return out.toByteArray()
    }

    private fun readyText(u: Usage) =
        "Ready · ${u.jobs} page${if (u.jobs == 1) "" else "s"} · ${"%.1f".format(u.megabytes)} MB today"

    private fun show(phase: Phase, detail: String, host: String? = null) {
        WorkerState.set(phase, detail, host)
        val title = when (phase) {
            Phase.WORKING -> "Working"
            Phase.PAUSED -> "Paused"
            Phase.OFFLINE -> "Offline"
            else -> "SK Helper"
        }
        Notifications.update(this, title, detail)
    }
}
