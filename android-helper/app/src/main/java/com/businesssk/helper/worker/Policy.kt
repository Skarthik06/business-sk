package com.businesssk.helper.worker

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import com.businesssk.helper.data.HelperSettings
import com.businesssk.helper.data.NetworkMode

/** Whether the phone should take jobs right now (network mode, daily data limit, battery rules). */
object Policy {
    data class Decision(val ok: Boolean, val reason: String? = null)
    data class Device(val network: String, val batteryPercent: Int, val charging: Boolean)

    fun device(context: Context): Device {
        val cm = context.getSystemService(ConnectivityManager::class.java)
        val caps = cm?.getNetworkCapabilities(cm.activeNetwork)
        val network = when {
            caps == null || !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            else -> "other"
        }
        val bm = context.getSystemService(BatteryManager::class.java)
        val pct = bm?.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY) ?: -1
        val charging = bm?.isCharging ?: false
        return Device(network, pct, charging)
    }

    fun evaluate(s: HelperSettings, d: Device, usedBytes: Long): Decision {
        if (d.network == "none") return Decision(false, "No internet connection")
        when (s.networkMode) {
            NetworkMode.WIFI -> if (d.network != "wifi") return Decision(false, "Waiting for Wi-Fi")
            NetworkMode.MOBILE -> if (d.network != "cellular") return Decision(false, "Waiting for mobile data")
            NetworkMode.BOTH -> Unit
        }
        if (usedBytes >= s.dataCapMb * 1_000_000L) return Decision(false, "Daily data limit reached")
        if (s.chargingOnly && !d.charging) return Decision(false, "Waiting for the charger")
        if (!d.charging && s.batteryMin > 0 && d.batteryPercent in 0 until s.batteryMin) {
            return Decision(false, "Battery below ${s.batteryMin}%")
        }
        if (s.allowed.isEmpty()) return Decision(false, "No sites allowed in Settings")
        return Decision(true)
    }
}
