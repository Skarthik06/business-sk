package com.businesssk.helper.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import java.io.File

@Serializable
data class LogEntry(
    val at: Long,
    val host: String,
    val path: String,
    val status: Int? = null,
    val bytes: Long = 0,
    val ms: Long = 0,
    val ok: Boolean = false,
    val note: String = "",
    val ip: String = "",                     // the public IP this phone fetched from (as the server saw it)
)

/** The last 100 fetches (kept on the phone only), newest first. */
object ActivityLog {
    private const val MAX = 100
    private val json = Json { ignoreUnknownKeys = true }
    private var file: File? = null
    private val _entries = MutableStateFlow<List<LogEntry>>(emptyList())
    val entries: StateFlow<List<LogEntry>> = _entries.asStateFlow()

    fun init(context: Context) {
        val f = File(context.filesDir, "activity.json")
        file = f
        _entries.value = runCatching {
            if (f.exists()) json.decodeFromString(ListSerializer(LogEntry.serializer()), f.readText()) else emptyList()
        }.getOrDefault(emptyList())
    }

    @Synchronized
    fun add(entry: LogEntry) {
        val next = (listOf(entry) + _entries.value).take(MAX)
        _entries.value = next
        runCatching { file?.writeText(json.encodeToString(ListSerializer(LogEntry.serializer()), next)) }
    }

    @Synchronized
    fun clear() {
        _entries.value = emptyList()
        runCatching { file?.delete() }
    }
}
