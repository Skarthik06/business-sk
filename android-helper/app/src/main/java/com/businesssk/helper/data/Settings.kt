package com.businesssk.helper.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.longPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.businesssk.helper.BuildConfig
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import java.time.LocalDate

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "helper")

/** Sites the phone may fetch. The server only sends jobs for these; the phone refuses anything else. */
val SUPPORTED_SITES = listOf(
    Site("amazon.in", "Amazon India"),
    Site("flipkart.com", "Flipkart"),
    Site("shopsy.in", "Shopsy"),
)

data class Site(val domain: String, val label: String)

enum class NetworkMode(val key: String, val label: String) {
    BOTH("both", "Wi-Fi + mobile data"),
    WIFI("wifi", "Wi-Fi only"),
    MOBILE("mobile", "Mobile data only");

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key } ?: BOTH
    }
}

data class HelperSettings(
    val onboarded: Boolean = false,
    val server: String = BuildConfig.DEFAULT_SERVER,
    val workerId: String = "",
    val deviceName: String = "",
    val paired: Boolean = false,
    val enabled: Boolean = false,
    val networkMode: NetworkMode = NetworkMode.BOTH,
    val dataCapMb: Int = 150,
    val batteryMin: Int = 15,
    val chargingOnly: Boolean = false,
    val allowed: Set<String> = SUPPORTED_SITES.map { it.domain }.toSet(),
)

data class Usage(val day: String, val bytes: Long, val jobs: Int, val lastJobAt: Long) {
    val megabytes: Double get() = bytes / 1_000_000.0
}

class SettingsRepo(private val context: Context) {
    private object K {
        val ONBOARDED = booleanPreferencesKey("onboarded")
        val SERVER = stringPreferencesKey("server")
        val WORKER_ID = stringPreferencesKey("worker_id")
        val DEVICE_NAME = stringPreferencesKey("device_name")
        val TOKEN_ENC = stringPreferencesKey("token_enc")
        val ENABLED = booleanPreferencesKey("enabled")
        val NETWORK = stringPreferencesKey("network_mode")
        val CAP = intPreferencesKey("data_cap_mb")
        val BATTERY_MIN = intPreferencesKey("battery_min")
        val CHARGING_ONLY = booleanPreferencesKey("charging_only")
        val ALLOWED = stringSetPreferencesKey("allowed_sites")
        val DAY = stringPreferencesKey("usage_day")
        val BYTES = longPreferencesKey("usage_bytes")
        val JOBS = intPreferencesKey("usage_jobs")
        val LAST_JOB = longPreferencesKey("last_job_at")
    }

    val settings: Flow<HelperSettings> = context.dataStore.data.map { p ->
        HelperSettings(
            onboarded = p[K.ONBOARDED] ?: false,
            server = p[K.SERVER] ?: BuildConfig.DEFAULT_SERVER,
            workerId = p[K.WORKER_ID] ?: "",
            deviceName = p[K.DEVICE_NAME] ?: "",
            paired = !p[K.TOKEN_ENC].isNullOrEmpty() && !p[K.WORKER_ID].isNullOrEmpty(),
            enabled = p[K.ENABLED] ?: false,
            networkMode = NetworkMode.of(p[K.NETWORK]),
            dataCapMb = p[K.CAP] ?: 150,
            batteryMin = p[K.BATTERY_MIN] ?: 15,
            chargingOnly = p[K.CHARGING_ONLY] ?: false,
            allowed = p[K.ALLOWED] ?: SUPPORTED_SITES.map { it.domain }.toSet(),
        )
    }

    val usage: Flow<Usage> = context.dataStore.data.map { p -> usageOf(p) }

    private fun today() = LocalDate.now().toString()

    private fun usageOf(p: Preferences): Usage {
        val day = today()
        return if (p[K.DAY] == day) Usage(day, p[K.BYTES] ?: 0L, p[K.JOBS] ?: 0, p[K.LAST_JOB] ?: 0L)
        else Usage(day, 0L, 0, p[K.LAST_JOB] ?: 0L)                   // a new day starts at zero
    }

    suspend fun current(): HelperSettings = settings.first()
    suspend fun usageNow(): Usage = usage.first()

    suspend fun token(): String? = context.dataStore.data.first()[K.TOKEN_ENC]?.let { SecureStore.decrypt(it) }

    suspend fun savePairing(server: String, workerId: String, token: String, deviceName: String) {
        context.dataStore.edit {
            it[K.SERVER] = server
            it[K.WORKER_ID] = workerId
            it[K.DEVICE_NAME] = deviceName
            it[K.TOKEN_ENC] = SecureStore.encrypt(token)
        }
    }

    suspend fun unpair() {
        context.dataStore.edit {
            it.remove(K.TOKEN_ENC); it.remove(K.WORKER_ID); it[K.ENABLED] = false; it[K.ONBOARDED] = false
        }
    }

    suspend fun setOnboarded(v: Boolean) = context.dataStore.edit { it[K.ONBOARDED] = v }
    suspend fun setEnabled(v: Boolean) = context.dataStore.edit { it[K.ENABLED] = v }
    suspend fun setNetworkMode(m: NetworkMode) = context.dataStore.edit { it[K.NETWORK] = m.key }
    suspend fun setDataCap(mb: Int) = context.dataStore.edit { it[K.CAP] = mb.coerceIn(10, 2000) }
    suspend fun setBatteryMin(pct: Int) = context.dataStore.edit { it[K.BATTERY_MIN] = pct.coerceIn(0, 80) }
    suspend fun setChargingOnly(v: Boolean) = context.dataStore.edit { it[K.CHARGING_ONLY] = v }
    suspend fun setAllowed(domain: String, on: Boolean) = context.dataStore.edit {
        val cur = (it[K.ALLOWED] ?: SUPPORTED_SITES.map { s -> s.domain }.toSet()).toMutableSet()
        if (on) cur.add(domain) else cur.remove(domain)
        it[K.ALLOWED] = cur
    }

    /** Count data (downloaded + uploaded bytes) and finished jobs against TODAY's totals. */
    suspend fun addUsage(bytes: Long, jobs: Int) {
        context.dataStore.edit {
            val day = today()
            if (it[K.DAY] != day) { it[K.DAY] = day; it[K.BYTES] = 0L; it[K.JOBS] = 0 }
            it[K.BYTES] = (it[K.BYTES] ?: 0L) + bytes.coerceAtLeast(0)
            it[K.JOBS] = (it[K.JOBS] ?: 0) + jobs
            if (jobs > 0) it[K.LAST_JOB] = System.currentTimeMillis()
        }
    }
}
