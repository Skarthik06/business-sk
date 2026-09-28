package com.businesssk.helper.ui

import android.app.Application
import android.net.Uri
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.businesssk.helper.BuildConfig
import com.businesssk.helper.data.ActivityLog
import com.businesssk.helper.data.HelperSettings
import com.businesssk.helper.data.NetworkMode
import com.businesssk.helper.data.SettingsRepo
import com.businesssk.helper.data.Usage
import com.businesssk.helper.net.ApiException
import com.businesssk.helper.net.ServerApi
import com.businesssk.helper.update.LatestInfo
import com.businesssk.helper.update.Updater
import com.businesssk.helper.worker.WorkerService
import com.businesssk.helper.worker.WorkerState
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import java.time.LocalDate

sealed interface UpdateState {
    data object Idle : UpdateState
    data object Checking : UpdateState
    data class UpToDate(val latest: String) : UpdateState
    data class Available(val info: LatestInfo) : UpdateState
    data class Downloading(val info: LatestInfo, val progress: Float) : UpdateState
    data class Installing(val info: LatestInfo) : UpdateState
    data class Failed(val message: String, val info: LatestInfo? = null) : UpdateState
}

sealed interface PairState {
    data object Idle : PairState
    data object Working : PairState
    data class Done(val workerId: String) : PairState
    data class Failed(val message: String) : PairState
}

class HelperViewModel(app: Application) : AndroidViewModel(app) {
    private val repo = SettingsRepo(app)

    val settings: StateFlow<HelperSettings?> = repo.settings.stateIn(viewModelScope, SharingStarted.Eagerly, null)
    val usage: StateFlow<Usage> = repo.usage.stateIn(viewModelScope, SharingStarted.Eagerly,
        Usage(LocalDate.now().toString(), 0, 0, 0))
    val status = WorkerState.status
    val activity = ActivityLog.entries
    val publicIp = WorkerState.ip

    private val _pair = MutableStateFlow<PairState>(PairState.Idle)
    val pair: StateFlow<PairState> = _pair.asStateFlow()

    fun resetPair() { _pair.value = PairState.Idle }

    // ── app updates (Settings → App updates) ──────────────────────────────────────────────────
    private val _update = MutableStateFlow<UpdateState>(UpdateState.Idle)
    val update: StateFlow<UpdateState> = _update.asStateFlow()

    init { checkForUpdate() }

    fun checkForUpdate() {
        if (_update.value is UpdateState.Downloading || _update.value is UpdateState.Installing) return
        _update.value = UpdateState.Checking
        viewModelScope.launch {
            _update.value = withContext(Dispatchers.IO) {
                runCatching { Updater.latest() }.fold(
                    onSuccess = { if (Updater.isNewer(it)) UpdateState.Available(it) else UpdateState.UpToDate(it.versionName) },
                    onFailure = { UpdateState.Failed("Couldn't check for updates — check your internet") },
                )
            }
        }
    }

    /** Download (checksum-verified) → Android installer. The app restarts on the new version. */
    fun installUpdate(info: LatestInfo) {
        val ctx = getApplication<Application>()
        viewModelScope.launch {
            _update.value = UpdateState.Downloading(info, 0f)
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    val apk = Updater.download(ctx, info) { p -> _update.value = UpdateState.Downloading(info, p) }
                    _update.value = UpdateState.Installing(info)
                    Updater.install(ctx, apk)
                }
            }
            result.onFailure { e -> _update.value = UpdateState.Failed(e.message ?: "Update failed", info) }
        }
    }

    fun updateFailed(message: String) {
        val info = when (val u = _update.value) {
            is UpdateState.Installing -> u.info
            is UpdateState.Downloading -> u.info
            is UpdateState.Available -> u.info
            else -> null
        }
        _update.value = UpdateState.Failed(message, info)
    }

    /** skhelper://pair?server=<url-encoded>&code=ABCD2345 (the Studio's QR / a tapped link). */
    fun pairFromLink(uri: Uri?) {
        if (uri == null || uri.scheme != "skhelper" || uri.host != "pair") return
        val code = uri.getQueryParameter("code").orEmpty()
        val server = uri.getQueryParameter("server").orEmpty().ifBlank { BuildConfig.DEFAULT_SERVER }
        if (!server.startsWith("https://")) { _pair.value = PairState.Failed("That QR code isn't from your Studio."); return }
        pairWithCode(code, server)
    }

    fun scanQr() {
        val opts = GmsBarcodeScannerOptions.Builder().setBarcodeFormats(Barcode.FORMAT_QR_CODE).build()
        viewModelScope.launch {
            try {
                val code = GmsBarcodeScanning.getClient(getApplication(), opts).startScan().await()
                val raw = code.rawValue.orEmpty()
                if (!raw.startsWith("skhelper://pair")) {
                    _pair.value = PairState.Failed("That QR code isn't a Business-SK pairing code.")
                } else pairFromLink(Uri.parse(raw))
            } catch (e: Exception) {
                if (e.message?.contains("cancel", ignoreCase = true) != true) {
                    _pair.value = PairState.Failed("The scanner couldn't start. Type the code instead.")
                }
            }
        }
    }

    fun pairWithCode(code: String, server: String = BuildConfig.DEFAULT_SERVER) {
        val clean = code.uppercase().filter { it.isLetterOrDigit() }
        if (clean.length != 8) { _pair.value = PairState.Failed("The code has 8 letters/numbers, like ABCD-2345."); return }
        _pair.value = PairState.Working
        val name = "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}".trim()
        viewModelScope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching { ServerApi(server, null).pair(clean, name, Build.MODEL) }
            }
            result.onSuccess { r ->
                if (r.token.isBlank() || r.worker_id.isBlank()) {
                    _pair.value = PairState.Failed("The server didn't return a device token.")
                } else {
                    repo.savePairing(server, r.worker_id, r.token, name)
                    _pair.value = PairState.Done(r.worker_id)
                }
            }.onFailure { e ->
                _pair.value = PairState.Failed(when (e) {
                    is ApiException -> e.message ?: "Pairing failed (${e.code})"
                    else -> "Can't reach the server — check your internet and try again."
                })
            }
        }
    }

    fun finishOnboarding() = viewModelScope.launch {
        repo.setOnboarded(true)
        setEnabled(true)
    }

    fun setEnabled(on: Boolean) = viewModelScope.launch {
        repo.setEnabled(on)
        val ctx = getApplication<Application>()
        if (on) WorkerService.start(ctx) else WorkerService.stop(ctx)
    }

    fun setNetworkMode(m: NetworkMode) = viewModelScope.launch { repo.setNetworkMode(m) }
    fun setDataCap(mb: Int) = viewModelScope.launch { repo.setDataCap(mb) }
    fun setBatteryMin(pct: Int) = viewModelScope.launch { repo.setBatteryMin(pct) }
    fun setChargingOnly(v: Boolean) = viewModelScope.launch { repo.setChargingOnly(v) }
    fun setAllowed(domain: String, on: Boolean) = viewModelScope.launch { repo.setAllowed(domain, on) }
    fun clearActivity() = ActivityLog.clear()

    // pull-to-refresh on Activity: re-read the log and check for an app update
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing.asStateFlow()
    fun refreshActivity() = viewModelScope.launch {
        _refreshing.value = true
        withContext(Dispatchers.IO) { ActivityLog.init(getApplication()) }
        checkForUpdate()
        kotlinx.coroutines.delay(600)
        _refreshing.value = false
    }

    fun unpair() = viewModelScope.launch {
        WorkerService.stop(getApplication())
        repo.unpair()
        _pair.value = PairState.Idle
    }

    /** If the app was opened while it should be running (e.g. after the system killed it), restart it. */
    fun ensureRunning() = viewModelScope.launch {
        val s = repo.current()
        if (s.enabled && s.paired && status.value.phase == com.businesssk.helper.worker.Phase.OFF) {
            WorkerService.start(getApplication())
        }
    }
}
