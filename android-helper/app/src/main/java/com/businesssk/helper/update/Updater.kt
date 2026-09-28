package com.businesssk.helper.update

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import com.businesssk.helper.BuildConfig
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.IOException
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** What the server says is the newest app build (helper-dist/version.json, written by release.sh). */
@Serializable
data class LatestInfo(
    val versionCode: Int,
    val versionName: String,
    val url: String,
    val sha256: String,
    val size: Long = 0,
    val notes: String = "",
)

/**
 * Self-update for a sideloaded app: download the APK from your own server, check its SHA-256, then
 * hand it to Android's PackageInstaller. The first update asks you to confirm; after that this app
 * is the "installer of record", so on Android 12+ later updates install without a prompt.
 */
object Updater {
    const val ACTION_STATUS = "com.businesssk.helper.action.UPDATE_STATUS"

    private val json = Json { ignoreUnknownKeys = true }
    private val client = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(60, TimeUnit.SECONDS)
        .build()

    fun latest(): LatestInfo {
        val req = Request.Builder().url(BuildConfig.UPDATE_INFO_URL).header("Cache-Control", "no-cache").build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException("Update server answered ${r.code}")
            return json.decodeFromString(LatestInfo.serializer(), r.body?.string().orEmpty())
        }
    }

    fun isNewer(info: LatestInfo) = info.versionCode > BuildConfig.VERSION_CODE

    /** Download to the app's cache, verifying the checksum. Returns the verified file. */
    fun download(ctx: Context, info: LatestInfo, onProgress: (Float) -> Unit): File {
        if (!info.url.startsWith("https://")) throw IOException("Update URL must be https")
        val dir = File(ctx.cacheDir, "updates").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }                    // old downloads
        val out = File(dir, "sk-helper-${info.versionCode}.apk")
        val md = MessageDigest.getInstance("SHA-256")
        client.newCall(Request.Builder().url(info.url).build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("Download failed (${r.code})")
            val body = r.body ?: throw IOException("Empty download")
            val total = body.contentLength().takeIf { it > 0 } ?: info.size
            body.byteStream().use { input ->
                out.outputStream().use { o ->
                    val buf = ByteArray(64 * 1024)
                    var done = 0L
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        o.write(buf, 0, n); md.update(buf, 0, n); done += n
                        if (total > 0) onProgress((done.toFloat() / total).coerceIn(0f, 1f))
                    }
                }
            }
        }
        val got = md.digest().joinToString("") { "%02x".format(it) }
        if (!got.equals(info.sha256.trim(), ignoreCase = true)) {
            out.delete()
            throw IOException("The download was damaged (checksum mismatch) — try again")
        }
        return out
    }

    /** Stream the APK into a PackageInstaller session; the result arrives at UpdateReceiver. */
    fun install(ctx: Context, apk: File) {
        val installer = ctx.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(ctx.packageName)
            if (Build.VERSION.SDK_INT >= 31) setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            if (Build.VERSION.SDK_INT >= 34) setPackageSource(PackageInstaller.PACKAGE_SOURCE_DOWNLOADED_FILE)
        }
        val id = installer.createSession(params)
        installer.openSession(id).use { session ->
            session.openWrite("base.apk", 0, apk.length()).use { out ->
                apk.inputStream().use { it.copyTo(out) }
                session.fsync(out)
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0)
            val intent = Intent(ctx, UpdateReceiver::class.java).setAction(ACTION_STATUS)
            session.commit(PendingIntent.getBroadcast(ctx, id, intent, flags).intentSender)
        }
    }
}
