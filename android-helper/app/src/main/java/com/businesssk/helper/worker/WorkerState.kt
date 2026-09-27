package com.businesssk.helper.worker

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** What the helper is doing right now — written by the service, shown by the UI + notification. */
enum class Phase { OFF, STARTING, READY, WORKING, PAUSED, OFFLINE }

data class Status(val phase: Phase = Phase.OFF, val detail: String = "", val host: String? = null)

object WorkerState {
    private val _status = MutableStateFlow(Status())
    val status: StateFlow<Status> = _status.asStateFlow()

    fun set(phase: Phase, detail: String = "", host: String? = null) {
        _status.value = Status(phase, detail, host)
    }
}
