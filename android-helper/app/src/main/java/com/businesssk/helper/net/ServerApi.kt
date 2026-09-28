package com.businesssk.helper.net

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.util.concurrent.TimeUnit

/** An error answer from the server (HTTP status + its message). 401 = this phone was unpaired. */
class ApiException(val code: Int, message: String) : IOException(message)

@Serializable
data class PairResponse(val ok: Boolean = false, val device_id: Int = 0, val worker_id: String = "", val token: String = "")

@Serializable
data class Job(val attempt_id: String, val url: String, val mode: String = "http", val timeout: Int = 30)

@Serializable
data class LeaseResponse(val jobs: List<Job> = emptyList())

@Serializable
data class IpResponse(val ip: String = "")

@Serializable
data class Beat(
    val worker_id: String,
    val status: String,                       // healthy | paused
    val capabilities: List<String> = listOf("http"),
    val active_jobs: Int = 0,
    val kind: String = "phone",
    val version: String,
    val battery_percent: Double? = null,
    val charging: Boolean? = null,
    val network: String? = null,
    val data_today_mb: Double? = null,
    val jobs_today: Int? = null,
    val allowed_domains: List<String>? = null,
    val paused_reason: String? = null,
    val max: Int = 1,
    val wait: Double = 20.0,
)

@Serializable
data class JobResult(
    val worker_id: String,
    val status_code: Int? = null,
    val content_type: String = "",
    val final_url: String = "",
    val html: String = "",
    val gz: Boolean = true,
    val error: String? = null,
    val duration_ms: Long = 0,
)

/** The Scraper API's worker endpoints: /pair, /heartbeat, /jobs/lease, /jobs/{id}/result. */
class ServerApi(base: String, private val token: String?) {
    private val base = base.trimEnd('/')

    companion object {
        val json = Json { ignoreUnknownKeys = true; explicitNulls = false; encodeDefaults = true }
        private val JSON = "application/json; charset=utf-8".toMediaType()
        private val client: OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(45, TimeUnit.SECONDS)                      // lease long-polls ~20 s
            .writeTimeout(90, TimeUnit.SECONDS)                     // uploading a page
            .retryOnConnectionFailure(true)
            .build()
    }

    private fun post(path: String, body: String): String {
        val req = Request.Builder().url(base + path)
            .header("User-Agent", "BusinessSK-Helper-Android")
            .apply { if (token != null) header("X-Worker-Token", token) }
            .post(body.toRequestBody(JSON))
            .build()
        client.newCall(req).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val detail = runCatching {
                    json.parseToJsonElement(text).jsonObject["detail"]?.jsonPrimitive?.content
                }.getOrNull()
                throw ApiException(resp.code, detail ?: "Server answered ${resp.code}")
            }
            return text
        }
    }

    fun pair(code: String, name: String, model: String): PairResponse {
        val body = """{"code":${jsonString(code)},"name":${jsonString(name)},"model":${jsonString(model)}}"""
        return json.decodeFromString(PairResponse.serializer(), post("/pair", body))
    }

    /** Returns this phone's public IP as the server sees it. */
    fun heartbeat(beat: Beat): String =
        runCatching { json.decodeFromString(IpResponse.serializer(), post("/heartbeat", json.encodeToString(Beat.serializer(), beat))).ip }
            .getOrDefault("")

    fun lease(beat: Beat): List<Job> =
        json.decodeFromString(LeaseResponse.serializer(), post("/jobs/lease", json.encodeToString(Beat.serializer(), beat))).jobs

    /** Bytes uploaded (counted against the daily data limit) + the public IP the server saw. */
    fun result(attemptId: String, result: JobResult): Pair<Long, String> {
        val body = json.encodeToString(JobResult.serializer(), result)
        val answer = post("/jobs/$attemptId/result", body)
        val ip = runCatching { json.decodeFromString(IpResponse.serializer(), answer).ip }.getOrDefault("")
        return body.length.toLong() to ip
    }

    private fun jsonString(s: String) = JsonPrimitive(s).toString()     // quoted + escaped
}
