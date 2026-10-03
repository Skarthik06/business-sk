package com.businesssk.helper.cutout

import android.content.Context
import com.google.android.gms.common.moduleinstall.ModuleInstall
import com.google.android.gms.common.moduleinstall.ModuleInstallRequest
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import com.businesssk.helper.net.PageFetcher
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.segmentation.subject.SubjectSegmentation
import com.google.mlkit.vision.segmentation.subject.SubjectSegmenterOptions
import kotlinx.coroutines.tasks.await
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import okhttp3.Dns
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.UnknownHostException
import java.util.concurrent.TimeUnit
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

@Serializable
data class CutJob(val id: String, val type: String = "cutout", val url: String = "")

@Serializable
data class CutJobs(val jobs: List<CutJob> = emptyList(), val laptop_gpu: Boolean = false,
                   val alerts: List<StudioAlert> = emptyList(),
                   val post_plan: com.businesssk.helper.worker.PostPlan? = null)

/** A GPU Watchdog alert from the Studio ("Colab needed — open Colab → Run all"). */
@Serializable
data class StudioAlert(val id: String, val message: String, val url: String = "")

/** The Studio's phone cut-out endpoints (/api/gpu/device/…), authenticated with this phone's token. */
class StudioCutApi(server: String, private val token: String) {
    // the worker URL is https://<studio>/scraper-worker → the Studio itself is its origin
    private val base = server.trimEnd('/').removeSuffix("/scraper-worker")

    fun jobs(n: Int, version: String): CutJobs {
        val req = Request.Builder().url("$base/api/gpu/device/jobs?n=$n&ver=$version")
            .header("X-Worker-Token", token).header("User-Agent", "SK-Helper-Android").build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) throw IOException("cut-out jobs: ${r.code}")
            return json.decodeFromString(CutJobs.serializer(), r.body?.string().orEmpty())
        }
    }

    /** Today's 2 best posting times + posts done today (no jobs, no side effects). */
    fun postPlan(): com.businesssk.helper.worker.PostPlan? {
        val req = Request.Builder().url("$base/api/gpu/device/post-plan")
            .header("X-Worker-Token", token).header("User-Agent", "SK-Helper-Android").build()
        client.newCall(req).execute().use { r ->
            if (!r.isSuccessful) return null
            return json.decodeFromString(com.businesssk.helper.worker.PostPlan.serializer(), r.body?.string().orEmpty())
        }
    }

    /** Returns the bytes uploaded (counted against the daily data limit). */
    fun result(jobId: String, png: ByteArray, meta: Cutter.Facts): Long {
        val body = buildJsonObject {
            put("job_id", jobId)
            put("b64", Base64.encodeToString(png, Base64.NO_WRAP))
            put("meta", buildJsonObject {
                put("subject", meta.subject); put("touches_bottom", meta.touchesBottom)
                put("aspect", meta.aspect); put("fill", meta.fill)
                put("colors", buildJsonArray { meta.colors.forEach { add(JsonPrimitive(it)) } })
            })
        }.toString()
        val req = Request.Builder().url("$base/api/gpu/device/result")
            .header("X-Worker-Token", token).header("User-Agent", "SK-Helper-Android")
            .post(body.toRequestBody("application/json".toMediaType())).build()
        client.newCall(req).execute().use { r -> if (!r.isSuccessful) throw IOException("upload refused: ${r.code}") }
        return body.length.toLong()
    }

    /** Tell the Studio this phone couldn't do the job → it's released for another GPU at once. */
    fun fail(jobId: String, reason: String) {
        val body = buildJsonObject { put("job_id", jobId); put("error", reason.take(200)) }.toString()
        val req = Request.Builder().url("$base/api/gpu/device/result")
            .header("X-Worker-Token", token).header("User-Agent", "SK-Helper-Android")
            .post(body.toRequestBody("application/json".toMediaType())).build()
        client.newCall(req).execute().close()
    }

    companion object {
        private val json = Json { ignoreUnknownKeys = true }
        private val client = OkHttpClient.Builder().connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS).writeTimeout(90, TimeUnit.SECONDS).build()
    }
}

/**
 * Product cut-out on the phone: ML Kit subject segmentation (on-device) → the same output the
 * laptop's BiRefNet worker makes — a tight-cropped RGBA PNG (original pixels, only alpha added,
 * ≤1600 px) plus the facts the art director uses (person/object, touches bottom, aspect, fill, colours).
 */
object Cutter {
    data class Facts(val subject: String, val touchesBottom: Boolean, val aspect: Double, val fill: Double, val colors: List<String>)
    data class Result(val png: ByteArray, val facts: Facts, val downloaded: Long, val ms: Long)

    private const val MAX_IMAGE_BYTES = 15_000_000L
    private const val MAX_SIDE = 1600

    private val safeDns = object : Dns {
        override fun lookup(hostname: String) = Dns.SYSTEM.lookup(hostname).also { all ->
            if (all.isEmpty() || all.any { !PageFetcher.isPublic(it) }) throw UnknownHostException("refused: $hostname")
        }
    }
    private val http = OkHttpClient.Builder().dns(safeDns).connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS).build()

    private val segmenter by lazy {
        SubjectSegmentation.getClient(SubjectSegmenterOptions.Builder().enableForegroundBitmap().build())
    }

    /** ML Kit's cut-out model comes from Google Play services. If it isn't on the phone yet, ask
     *  Play to download it now (instead of failing silently) and report that clearly. */
    suspend fun ensureModel(ctx: Context) {
        val mi = ModuleInstall.getClient(ctx)
        if (mi.areModulesAvailable(segmenter).await().areModulesAvailable()) return
        mi.installModules(ModuleInstallRequest.newBuilder().addApi(segmenter).build())
        throw IOException("Google Play is downloading the cut-out model — this phone will start in a few minutes")
    }

    suspend fun cut(ctx: Context, url: String): Result {
        ensureModel(ctx)
        val t0 = System.currentTimeMillis()
        val u = url.toHttpUrlOrNull() ?: throw IOException("bad image URL")
        if (u.scheme != "https" && u.scheme != "http") throw IOException("bad image URL")
        val raw = http.newCall(Request.Builder().url(u).header("User-Agent", "Mozilla/5.0").build()).execute().use { r ->
            if (!r.isSuccessful) throw IOException("image download failed (${r.code})")
            val body = r.body ?: throw IOException("empty image")
            if (body.contentLength() > MAX_IMAGE_BYTES) throw IOException("image too large")
            body.source().use { it.request(MAX_IMAGE_BYTES); it.buffer.readByteArray(min(it.buffer.size, MAX_IMAGE_BYTES)) }
        }
        val src = decode(raw) ?: throw IOException("not an image")
        val fg = segmenter.process(InputImage.fromBitmap(src, 0)).await().foregroundBitmap
            ?: throw IOException("no product found in the photo")
        val out = cropAndMeasure(src, fg)
        return Result(out.first, out.second, raw.size.toLong(), System.currentTimeMillis() - t0)
    }

    /** Decode, never larger than MAX_SIDE (keeps memory low on the phone). */
    private fun decode(raw: ByteArray): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(raw, 0, raw.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null
        var sample = 1
        while (max(bounds.outWidth, bounds.outHeight) / (sample * 2) >= MAX_SIDE) sample *= 2
        val bmp = BitmapFactory.decodeByteArray(raw, 0, raw.size, BitmapFactory.Options().apply {
            inSampleSize = sample; inPreferredConfig = Bitmap.Config.ARGB_8888
        }) ?: return null
        val side = max(bmp.width, bmp.height)
        return if (side <= MAX_SIDE) bmp else Bitmap.createScaledBitmap(
            bmp, (bmp.width * MAX_SIDE / side.toFloat()).roundToInt(), (bmp.height * MAX_SIDE / side.toFloat()).roundToInt(), true)
    }

    private fun cropAndMeasure(src: Bitmap, fg: Bitmap): Pair<ByteArray, Facts> {
        val w = fg.width
        val h = fg.height
        val px = IntArray(w * h)
        fg.getPixels(px, 0, w, 0, 0, w, h)
        // tight box around SOLID product pixels (alpha > 128), like the laptop worker
        var l = w; var t = h; var r = -1; var b = -1
        for (y in 0 until h) {
            val row = y * w
            for (x in 0 until w) if ((px[row + x] ushr 24) > 128) {
                if (x < l) l = x; if (x > r) r = x; if (y < t) t = y; if (y > b) b = y
            }
        }
        if (r < 0) throw IOException("no product found in the photo")
        val pad = max(2, (0.01 * max(w, h)).toInt())
        l = max(0, l - pad); t = max(0, t - pad); r = min(w - 1, r + pad); b = min(h - 1, b + pad)
        val cw = r - l + 1
        val ch = b - t + 1
        val cut = Bitmap.createBitmap(fg, l, t, cw, ch)

        val touchesBottom = b >= h - max(3, h / 100)
        val aspect = ((cw.toDouble() / max(1, ch)) * 100).roundToInt() / 100.0
        var solid = 0
        val cpx = IntArray(cw * ch)
        cut.getPixels(cpx, 0, cw, 0, 0, cw, ch)
        for (p in cpx) if ((p ushr 24) > 128) solid++
        val fill = ((solid.toDouble() / max(1, cw * ch)) * 100).roundToInt() / 100.0
        fun rowWidth(frac: Float): Double {
            val y = min(ch - 1, max(0, (ch * frac).toInt()))
            var n = 0
            for (x in 0 until cw) if ((cpx[y * cw + x] ushr 24) > 128) n++
            return n.toDouble() / max(1, cw)
        }
        // model shot = wide at the bottom edge, narrow at the top (the laptop's silhouette rule)
        val person = touchesBottom && aspect < 1.0 && rowWidth(0.97f) >= 0.45 && rowWidth(0.08f) <= 0.6
        val facts = Facts(if (person) "person" else "object", touchesBottom, aspect, fill, colors(cpx, cw, ch))

        val out = ByteArrayOutputStream(cw * ch)
        cut.compress(Bitmap.CompressFormat.PNG, 100, out)
        if (cut !== fg) cut.recycle()
        return out.toByteArray() to facts
    }

    /** Up to 3 dominant product colours (ignoring near-white), as #RRGGBB. */
    private fun colors(px: IntArray, w: Int, h: Int): List<String> {
        val step = max(1, max(w, h) / 96)
        val buckets = HashMap<Int, IntArray>()                 // key → [count, r, g, b]
        var y = 0
        while (y < h) {
            var x = 0
            while (x < w) {
                val p = px[y * w + x]
                if ((p ushr 24) > 128) {
                    val rr = (p shr 16) and 0xFF; val gg = (p shr 8) and 0xFF; val bb = p and 0xFF
                    if (!(rr > 235 && gg > 235 && bb > 235)) {
                        val key = (rr shr 6 shl 4) or (gg shr 6 shl 2) or (bb shr 6)
                        val a = buckets.getOrPut(key) { IntArray(4) }
                        a[0]++; a[1] += rr; a[2] += gg; a[3] += bb
                    }
                }
                x += step
            }
            y += step
        }
        return buckets.values.sortedByDescending { it[0] }.take(3).map { a ->
            "#%02X%02X%02X".format(a[1] / a[0], a[2] / a[0], a[3] / a[0])
        }
    }
}
