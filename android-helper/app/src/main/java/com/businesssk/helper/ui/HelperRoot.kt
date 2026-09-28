package com.businesssk.helper.ui

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import android.text.format.DateUtils
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.BatteryChargingFull
import androidx.compose.material.icons.rounded.CheckCircle
import androidx.compose.material.icons.rounded.SystemUpdate
import androidx.compose.material.icons.rounded.History
import androidx.compose.material.icons.rounded.Home
import androidx.compose.material.icons.rounded.Link
import androidx.compose.material.icons.rounded.Notifications
import androidx.compose.material.icons.rounded.QrCodeScanner
import androidx.compose.material.icons.rounded.RocketLaunch
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.businesssk.helper.BuildConfig
import com.businesssk.helper.data.HelperSettings
import com.businesssk.helper.data.LogEntry
import com.businesssk.helper.data.NetworkMode
import com.businesssk.helper.data.SUPPORTED_SITES
import com.businesssk.helper.data.Usage
import com.businesssk.helper.ui.theme.Sk
import com.businesssk.helper.update.UpdateEvents
import com.businesssk.helper.worker.Phase
import com.businesssk.helper.worker.Status

@Composable
fun HelperRoot(vm: HelperViewModel) {
    val settings by vm.settings.collectAsStateWithLifecycle()
    val s = settings
    Box(Modifier.fillMaxSize().background(Sk.Bg)) {
        when {
            s == null -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Sk.Gold)
            }
            !s.paired || !s.onboarded -> Onboarding(vm, s)
            else -> MainScreens(vm, s)
        }
    }
}

// ─────────────────────────────── onboarding ───────────────────────────────

@Composable
private fun Onboarding(vm: HelperViewModel, s: HelperSettings) {
    // paired already (e.g. through a pairing link) → straight to the permissions step
    var step by rememberSaveable { mutableIntStateOf(if (s.paired) 2 else 0) }
    val pair by vm.pair.collectAsStateWithLifecycle()
    LaunchedEffect(pair, s.paired) { if (s.paired && step < 2) step = 2 }

    Column(
        Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()
            .verticalScroll(rememberScrollState()).padding(horizontal = 22.dp, vertical = 18.dp),
    ) {
        StepDots(step)
        Spacer(Modifier.height(26.dp))
        AnimatedContent(targetState = step, label = "onboarding") { st ->
            when (st) {
                0 -> Welcome { step = 1 }
                1 -> PairStep(vm, pair)
                else -> PermissionsStep(onDone = { vm.finishOnboarding() })
            }
        }
    }
}

@Composable
private fun StepDots(step: Int) {
    Row(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
        repeat(3) { i ->
            Box(
                Modifier.height(4.dp).width(if (i == step) 26.dp else 12.dp).clip(CircleShape)
                    .background(if (i <= step) Sk.Gold else Sk.Border),
            )
        }
        Spacer(Modifier.weight(1f))
        Text("BUSINESS-SK", style = MaterialTheme.typography.labelSmall, color = Sk.Faint)
    }
}

@Composable
private fun Welcome(next: () -> Unit) {
    Column {
        Box(
            Modifier.size(64.dp).clip(RoundedCornerShape(18.dp)).background(Sk.Gold.copy(alpha = 0.14f)),
            contentAlignment = Alignment.Center,
        ) { Icon(Icons.Rounded.RocketLaunch, null, tint = Sk.Gold, modifier = Modifier.size(32.dp)) }
        Spacer(Modifier.height(22.dp))
        Text("SK Helper", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(10.dp))
        Text(
            "Your phone fetches Amazon, Flipkart and Shopsy pages for your Studio from its own home / mobile " +
                "connection — so \"Find products\" works even when your laptop is off.",
            style = MaterialTheme.typography.bodyLarge, color = Sk.Muted,
        )
        Spacer(Modifier.height(22.dp))
        Bullet("Only shopping pages your Studio asks for — nothing else, ever.")
        Bullet("Respects a daily data limit (150 MB by default), your battery and Wi-Fi rules.")
        Bullet("Pause or unpair any time — here or from the Studio.")
        Spacer(Modifier.height(30.dp))
        PrimaryButton("Get started", onClick = next)
    }
}

@Composable
private fun PairStep(vm: HelperViewModel, pair: PairState) {
    var code by rememberSaveable { mutableStateOf("") }
    val busy = pair is PairState.Working
    Column {
        Text("Pair with your Studio", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text(
            "In the Studio open Business-SK → Scraper → Pair a phone. Then either scan its QR code, tap " +
                "\"Open in Helper app\" if the Studio is open on this phone, or type the 8-character code.",
            style = MaterialTheme.typography.bodyMedium, color = Sk.Muted,
        )
        Spacer(Modifier.height(22.dp))
        PrimaryButton("Scan QR code", icon = Icons.Rounded.QrCodeScanner, enabled = !busy) { vm.resetPair(); vm.scanQr() }
        Spacer(Modifier.height(18.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            HorizontalDivider(Modifier.weight(1f), color = Sk.Border)
            Text("  or type the code  ", style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
            HorizontalDivider(Modifier.weight(1f), color = Sk.Border)
        }
        Spacer(Modifier.height(18.dp))
        OutlinedTextField(
            value = code,
            onValueChange = { v -> code = v.uppercase().filter { it.isLetterOrDigit() || it == '-' }.take(9) },
            label = { Text("Pairing code") },
            placeholder = { Text("ABCD-2345") },
            singleLine = true,
            enabled = !busy,
            textStyle = MaterialTheme.typography.titleLarge.copy(fontFamily = FontFamily.Monospace, letterSpacing = 3.sp),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Characters,
                keyboardType = KeyboardType.Ascii, imeAction = ImeAction.Done, autoCorrectEnabled = false),
            keyboardActions = KeyboardActions(onDone = { vm.pairWithCode(code) }),
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(12.dp))
        OutlinedButton(
            onClick = { vm.pairWithCode(code) },
            enabled = !busy && code.count { it.isLetterOrDigit() } == 8,
            modifier = Modifier.fillMaxWidth().height(50.dp),
        ) {
            if (busy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = Sk.Gold)
            else { Icon(Icons.Rounded.Link, null, Modifier.size(18.dp)); Spacer(Modifier.width(8.dp)); Text("Pair") }
        }
        Spacer(Modifier.height(14.dp))
        when (pair) {
            is PairState.Failed -> Notice(pair.message, Sk.Danger)
            is PairState.Done -> Notice("Paired as ${pair.workerId}", Sk.Ok)
            else -> Text(
                "Codes work once and expire after 10 minutes.",
                style = MaterialTheme.typography.bodySmall, color = Sk.Faint,
            )
        }
    }
}

@Composable
private fun PermissionsStep(onDone: () -> Unit) {
    val ctx = LocalContext.current
    var notifOk by remember { mutableStateOf(notificationsGranted(ctx)) }
    var batteryOk by remember { mutableStateOf(ignoringBattery(ctx)) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) {
        notifOk = notificationsGranted(ctx); batteryOk = ignoringBattery(ctx)
    }
    val notifLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { notifOk = it }

    Column {
        Text("Keep it running", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Text(
            "Android (and especially iQOO / Vivo) stops background apps to save battery. These three switches " +
                "keep the helper alive — it uses almost no power while it waits.",
            style = MaterialTheme.typography.bodyMedium, color = Sk.Muted,
        )
        Spacer(Modifier.height(20.dp))
        PermissionRow(
            Icons.Rounded.Notifications, "Show its notification",
            "A small ongoing notification is how Android lets a helper run.", notifOk,
        ) {
            if (Build.VERSION.SDK_INT >= 33) notifLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            else openAppSettings(ctx)
        }
        PermissionRow(
            Icons.Rounded.BatteryChargingFull, "Allow background use",
            "Choose \"Allow\" so battery saver doesn't pause it.", batteryOk,
        ) { requestIgnoreBattery(ctx) }
        PermissionRow(
            Icons.Rounded.RocketLaunch, "Auto-start (iQOO / Vivo)",
            "i Manager → App manager → Auto-start → turn on SK Helper. Also lock it in Recents.", null,
        ) { openAutoStart(ctx) }
        Spacer(Modifier.height(26.dp))
        PrimaryButton("Start helping", onClick = onDone)
        Spacer(Modifier.height(8.dp))
        Text(
            "You can change data, battery and site rules any time in Settings.",
            style = MaterialTheme.typography.bodySmall, color = Sk.Faint,
            modifier = Modifier.fillMaxWidth(),
        )
    }
}

@Composable
private fun PermissionRow(icon: ImageVector, title: String, body: String, done: Boolean?, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().padding(bottom = 10.dp).clip(RoundedCornerShape(14.dp)).background(Sk.Panel)
            .border(1.dp, if (done == true) Sk.Ok.copy(alpha = 0.4f) else Sk.Border, RoundedCornerShape(14.dp))
            .clickable(enabled = done != true, onClick = onClick).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(icon, null, tint = Sk.Gold, modifier = Modifier.size(22.dp))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(body, style = MaterialTheme.typography.bodySmall, color = Sk.Muted)
        }
        Spacer(Modifier.width(8.dp))
        if (done == true) Icon(Icons.Rounded.CheckCircle, null, tint = Sk.Ok)
        else Text("Open", color = Sk.Gold, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
    }
}

// ─────────────────────────────── main screens ───────────────────────────────

@Composable
private fun MainScreens(vm: HelperViewModel, s: HelperSettings) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val goSettings by vm.goSettings.collectAsStateWithLifecycle()
    LaunchedEffect(goSettings) { if (goSettings) { tab = 2; vm.consumeGoSettings() } }
    Scaffold(
        containerColor = Sk.Bg,
        bottomBar = {
            NavigationBar(containerColor = Sk.Panel, tonalElevation = 0.dp) {
                listOf(Triple("Home", Icons.Rounded.Home, 0), Triple("Activity", Icons.Rounded.History, 1),
                    Triple("Settings", Icons.Rounded.Settings, 2)).forEach { (label, icon, i) ->
                    NavigationBarItem(
                        selected = tab == i, onClick = { tab = i },
                        icon = { Icon(icon, null) }, label = { Text(label) },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = Sk.Gold, selectedTextColor = Sk.Gold, indicatorColor = Sk.Gold.copy(alpha = 0.14f),
                            unselectedIconColor = Sk.Faint, unselectedTextColor = Sk.Faint,
                        ),
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.fillMaxSize().padding(pad).statusBarsPadding()) {
            when (tab) {
                0 -> HomeScreen(vm, s)
                1 -> ActivityScreen(vm)
                else -> SettingsScreen(vm, s)
            }
        }
    }
}

@Composable
private fun HomeScreen(vm: HelperViewModel, s: HelperSettings) {
    val status by vm.status.collectAsStateWithLifecycle()
    val usage by vm.usage.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp)) {
        val ctx = LocalContext.current
        Header("Helper", s.deviceName.ifBlank { "This phone" })
        StatusCard(status, s.enabled) { vm.setEnabled(it) }
        val upd by vm.update.collectAsStateWithLifecycle()
        (upd as? UpdateState.Available)?.let { a ->
            Spacer(Modifier.height(12.dp))
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(Sk.Gold.copy(alpha = 0.12f))
                    .border(1.dp, Sk.Gold.copy(alpha = 0.5f), RoundedCornerShape(14.dp)).padding(14.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(Icons.Rounded.SystemUpdate, null, tint = Sk.Gold)
                Spacer(Modifier.width(10.dp))
                Text("Version ${a.info.versionName} is available", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { vm.installUpdate(a.info) }) { Text("Update", color = Sk.Gold, fontWeight = FontWeight.SemiBold) }
            }
        }
        Spacer(Modifier.height(14.dp))
        PrimaryButton("Open Business-SK Studio", icon = Icons.Rounded.RocketLaunch) {
            tryStart(ctx, Intent(ctx, com.google.androidbrowserhelper.trusted.LauncherActivity::class.java))
        }
        Spacer(Modifier.height(14.dp))
        UsageCard(usage, s.dataCapMb)
        Spacer(Modifier.height(14.dp))
        Card {
            Eyebrow("CONNECTED TO")
            Spacer(Modifier.height(6.dp))
            Text(Uri.parse(s.server).host ?: s.server, style = MaterialTheme.typography.titleMedium)
            Text(s.workerId, style = MaterialTheme.typography.bodySmall, color = Sk.Faint, fontFamily = FontFamily.Monospace)
            val ip by vm.publicIp.collectAsStateWithLifecycle()
            if (ip.isNotBlank()) Text("📱 Mobile · public IP $ip", style = MaterialTheme.typography.bodySmall,
                color = Sk.Muted, fontFamily = FontFamily.Monospace)
            Spacer(Modifier.height(10.dp))
            Text(
                "Sites: " + SUPPORTED_SITES.filter { it.domain in s.allowed }.joinToString { it.label }.ifBlank { "none" },
                style = MaterialTheme.typography.bodySmall, color = Sk.Muted,
            )
            Text(s.networkMode.label, style = MaterialTheme.typography.bodySmall, color = Sk.Muted)
        }
        Spacer(Modifier.height(10.dp))
        Text(
            "Run \"Find products\" in the Studio — this phone fetches the pages when your laptop is off.",
            style = MaterialTheme.typography.bodySmall, color = Sk.Faint,
        )
    }
}

@Composable
private fun StatusCard(status: Status, enabled: Boolean, onToggle: (Boolean) -> Unit) {
    val (color, title) = when {
        !enabled -> Sk.Faint to "Off"
        status.phase == Phase.WORKING -> Sk.Gold to "Working"
        status.phase == Phase.READY -> Sk.Ok to "Ready"
        status.phase == Phase.PAUSED -> Sk.Warn to "Paused"
        status.phase == Phase.OFFLINE -> Sk.Danger to "Offline"
        else -> Sk.Muted to "Starting"
    }
    Card(border = if (enabled) color.copy(alpha = 0.45f) else Sk.Border) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(12.dp).clip(CircleShape).background(color))
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.titleLarge)
                val detail = if (!enabled) "Turn on to take scrape jobs" else status.detail
                if (detail.isNotBlank()) Text(detail, style = MaterialTheme.typography.bodySmall, color = Sk.Muted,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            Switch(
                checked = enabled, onCheckedChange = onToggle,
                colors = SwitchDefaults.colors(checkedTrackColor = Sk.Gold, checkedThumbColor = Sk.Bg),
            )
        }
    }
}

@Composable
private fun UsageCard(u: Usage, capMb: Int) {
    Card {
        Eyebrow("TODAY")
        Spacer(Modifier.height(10.dp))
        Row {
            Stat("%.1f MB".format(u.megabytes), "of $capMb MB data", Modifier.weight(1f))
            Stat(u.jobs.toString(), "pages fetched", Modifier.weight(1f))
        }
        Spacer(Modifier.height(12.dp))
        LinearProgressIndicator(
            progress = { (u.megabytes / capMb.coerceAtLeast(1)).toFloat().coerceIn(0f, 1f) },
            modifier = Modifier.fillMaxWidth().height(6.dp).clip(CircleShape),
            color = Sk.Gold, trackColor = Sk.Border,
        )
        Spacer(Modifier.height(10.dp))
        Text(
            if (u.lastJobAt > 0) "Last job " + DateUtils.getRelativeTimeSpanString(u.lastJobAt) else "No jobs yet",
            style = MaterialTheme.typography.bodySmall, color = Sk.Faint,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ActivityScreen(vm: HelperViewModel) {
    val entries by vm.activity.collectAsStateWithLifecycle()
    val refreshing by vm.refreshing.collectAsStateWithLifecycle()
    Column(Modifier.fillMaxSize().padding(horizontal = 18.dp)) {
        Row(Modifier.padding(top = 18.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.weight(1f)) { Header("Activity", "Pages this phone fetched — runs from the phone's Studio go here") }
            if (entries.isNotEmpty()) TextButton(onClick = { vm.clearActivity() }) { Text("Clear", color = Sk.Muted) }
        }
        // pull down to refresh (works on an empty list too — it's a scrollable list either way)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = { vm.refreshActivity() }, modifier = Modifier.fillMaxSize()) {
            LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 18.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)) {
                if (entries.isEmpty()) item {
                    Text("Nothing fetched yet — pull down to refresh.", color = Sk.Faint,
                        modifier = Modifier.fillMaxWidth().padding(top = 60.dp))
                }
                items(entries, key = { it.at.toString() + it.path }) { LogRow(it) }
            }
        }
    }
}

@Composable
private fun LogRow(e: LogEntry) {
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Sk.Panel).padding(12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(if (e.ok) Sk.Ok else Sk.Danger))
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(e.host, style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
            Text((if (e.host.startsWith("✂️")) "📱 Mobile · on-device" else "📱 Mobile") +
                (if (e.ip.isNotBlank()) " · IP ${e.ip}" else ""), style = MaterialTheme.typography.bodySmall,
                color = Sk.Gold, fontFamily = FontFamily.Monospace)
            Text(
                listOfNotNull(e.path.takeIf { it.isNotBlank() }, e.note.takeIf { it.isNotBlank() }).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, color = Sk.Muted, maxLines = 1, overflow = TextOverflow.Ellipsis,
            )
        }
        Spacer(Modifier.width(8.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(
                listOfNotNull(e.status?.toString(), "%.1f MB".format(e.bytes / 1_000_000.0)).joinToString(" · "),
                style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace, color = Sk.Muted,
            )
            Text(DateUtils.getRelativeTimeSpanString(e.at).toString(), style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
        }
    }
}

@Composable
private fun SettingsScreen(vm: HelperViewModel, s: HelperSettings) {
    var confirmUnpair by remember { mutableStateOf(false) }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp)) {
        Header("Settings", "App updates and the rules the helper follows")
        UpdatesCard(vm)
        Spacer(Modifier.height(12.dp))

        Card {
            Eyebrow("NETWORK")
            NetworkMode.entries.forEach { m ->
                Row(
                    Modifier.fillMaxWidth().clickable { vm.setNetworkMode(m) }.padding(vertical = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    RadioButton(selected = s.networkMode == m, onClick = { vm.setNetworkMode(m) })
                    Text(m.label, style = MaterialTheme.typography.bodyLarge)
                }
            }
        }
        Spacer(Modifier.height(12.dp))
        Card {
            Eyebrow("DAILY DATA LIMIT")
            var cap by remember(s.dataCapMb) { mutableFloatStateOf(s.dataCapMb.toFloat()) }
            Text("${cap.toInt()} MB a day", style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 6.dp))
            Slider(value = cap, onValueChange = { cap = it }, valueRange = 25f..500f, steps = 18,
                onValueChangeFinished = { vm.setDataCap(cap.toInt()) })
            Text("A product search page is about 0.3–1 MB.", style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
        }
        Spacer(Modifier.height(12.dp))
        Card {
            Eyebrow("BATTERY")
            var min by remember(s.batteryMin) { mutableFloatStateOf(s.batteryMin.toFloat()) }
            Text(if (min.toInt() == 0) "Any battery level" else "Pause below ${min.toInt()}%",
                style = MaterialTheme.typography.titleMedium, modifier = Modifier.padding(top = 6.dp))
            Slider(value = min, onValueChange = { min = it }, valueRange = 0f..50f, steps = 9,
                onValueChangeFinished = { vm.setBatteryMin(min.toInt()) })
            ToggleRow("Only while charging", s.chargingOnly) { vm.setChargingOnly(it) }
        }
        Spacer(Modifier.height(12.dp))
        Card {
            Eyebrow("PRODUCT CUT-OUTS")
            ToggleRow("Cut products out for posts", s.cutouts) { vm.setCutouts(it) }
            Text("When your laptop's GPU is off, this phone removes the background from product photos " +
                "(Google ML Kit, on the phone) so posts still get clean cut-outs. About 1–2 MB per product.",
                style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
        }
        Spacer(Modifier.height(12.dp))
        Card {
            Eyebrow("SITES THIS PHONE MAY FETCH")
            SUPPORTED_SITES.forEach { site ->
                Row(
                    Modifier.fillMaxWidth().clickable { vm.setAllowed(site.domain, site.domain !in s.allowed) },
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Checkbox(checked = site.domain in s.allowed, onCheckedChange = { vm.setAllowed(site.domain, it) })
                    Column {
                        Text(site.label, style = MaterialTheme.typography.bodyLarge)
                        Text(site.domain, style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
                    }
                }
            }
        }
        Spacer(Modifier.height(12.dp))
        Card {
            Eyebrow("THIS DEVICE")
            Spacer(Modifier.height(6.dp))
            Text(s.deviceName.ifBlank { Build.MODEL }, style = MaterialTheme.typography.titleMedium)
            Text(s.workerId, style = MaterialTheme.typography.bodySmall, color = Sk.Faint, fontFamily = FontFamily.Monospace)
            Text("App ${BuildConfig.VERSION_NAME}", style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = { confirmUnpair = true }, modifier = Modifier.fillMaxWidth(),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = Sk.Danger),
            ) { Text("Unpair this phone") }
        }
        Spacer(Modifier.height(24.dp))
    }
    if (confirmUnpair) {
        AlertDialog(
            onDismissRequest = { confirmUnpair = false },
            title = { Text("Unpair this phone?") },
            text = { Text("It stops taking jobs and forgets its token. You can pair again with a new code from the Studio.") },
            confirmButton = { TextButton(onClick = { confirmUnpair = false; vm.unpair() }) { Text("Unpair", color = Sk.Danger) } },
            dismissButton = { TextButton(onClick = { confirmUnpair = false }) { Text("Cancel") } },
            containerColor = Sk.Panel2,
        )
    }
}

@Composable
private fun UpdatesCard(vm: HelperViewModel) {
    val u by vm.update.collectAsStateWithLifecycle()
    val event by UpdateEvents.last.collectAsStateWithLifecycle()
    LaunchedEffect(event) { event?.takeIf { it.failed }?.let { vm.updateFailed(it.message) } }
    Card(border = if (u is UpdateState.Available) Sk.Gold.copy(alpha = 0.55f) else Sk.Border) {
        Eyebrow("APP UPDATES")
        Spacer(Modifier.height(8.dp))
        Row {
            Stat(BuildConfig.VERSION_NAME, "installed", Modifier.weight(1f))
            val latest = when (val x = u) {
                is UpdateState.UpToDate -> x.latest
                is UpdateState.Available -> x.info.versionName
                is UpdateState.Downloading -> x.info.versionName
                is UpdateState.Installing -> x.info.versionName
                is UpdateState.Failed -> x.info?.versionName ?: "—"
                else -> "…"
            }
            Stat(latest, "latest", Modifier.weight(1f))
        }
        Spacer(Modifier.height(12.dp))
        when (val x = u) {
            is UpdateState.Available -> {
                if (x.info.notes.isNotBlank()) {
                    Text(x.info.notes, style = MaterialTheme.typography.bodySmall, color = Sk.Muted)
                    Spacer(Modifier.height(10.dp))
                }
                PrimaryButton("Update to ${x.info.versionName}", icon = Icons.Rounded.SystemUpdate) { vm.installUpdate(x.info) }
            }
            is UpdateState.Downloading -> {
                Text("Downloading… ${(x.progress * 100).toInt()}%", style = MaterialTheme.typography.bodyMedium)
                Spacer(Modifier.height(8.dp))
                LinearProgressIndicator(progress = { x.progress }, modifier = Modifier.fillMaxWidth().height(6.dp).clip(CircleShape),
                    color = Sk.Gold, trackColor = Sk.Border)
            }
            is UpdateState.Installing -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = Sk.Gold)
                Spacer(Modifier.width(10.dp))
                Text(event?.message?.takeIf { !it.startsWith("Updated") } ?: "Installing — the app restarts on the new version",
                    style = MaterialTheme.typography.bodyMedium)
            }
            is UpdateState.Failed -> {
                Notice(x.message, Sk.Danger)
                Spacer(Modifier.height(10.dp))
                OutlinedButton(onClick = { x.info?.let { vm.installUpdate(it) } ?: vm.checkForUpdate() },
                    modifier = Modifier.fillMaxWidth()) { Text(if (x.info != null) "Try again" else "Check again") }
            }
            is UpdateState.Checking, UpdateState.Idle -> Row(verticalAlignment = Alignment.CenterVertically) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp, color = Sk.Gold)
                Spacer(Modifier.width(10.dp))
                Text("Checking for updates…", style = MaterialTheme.typography.bodyMedium, color = Sk.Muted)
            }
            is UpdateState.UpToDate -> Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Rounded.CheckCircle, null, tint = Sk.Ok, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("You're on the latest version", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                TextButton(onClick = { vm.checkForUpdate() }) { Text("Check again", color = Sk.Gold) }
            }
        }
        Spacer(Modifier.height(8.dp))
        Text("The Business-SK Studio icon always shows the live website — only this app needs updating.",
            style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
    }
}

// ─────────────────────────────── small pieces ───────────────────────────────

@Composable
private fun Header(title: String, sub: String) {
    Column(Modifier.padding(bottom = 16.dp)) {
        Text(title, style = MaterialTheme.typography.headlineMedium)
        Text(sub, style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
    }
}

@Composable
private fun Card(border: Color = Sk.Border, content: @Composable () -> Unit) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Sk.Panel)
            .border(1.dp, border, RoundedCornerShape(16.dp)).padding(16.dp),
    ) { content() }
}

@Composable
private fun Eyebrow(text: String) = Text(text, style = MaterialTheme.typography.labelSmall, color = Sk.Faint)

@Composable
private fun Stat(value: String, label: String, modifier: Modifier = Modifier) {
    Column(modifier) {
        Text(value, style = MaterialTheme.typography.titleLarge, color = Sk.Text)
        Text(label, style = MaterialTheme.typography.bodySmall, color = Sk.Faint)
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange,
            colors = SwitchDefaults.colors(checkedTrackColor = Sk.Gold, checkedThumbColor = Sk.Bg))
    }
}

@Composable
private fun Bullet(text: String) {
    Row(Modifier.padding(bottom = 10.dp)) {
        Box(Modifier.padding(top = 8.dp).size(5.dp).clip(CircleShape).background(Sk.Gold))
        Spacer(Modifier.width(10.dp))
        Text(text, style = MaterialTheme.typography.bodyMedium, color = Sk.Text)
    }
}

@Composable
private fun Notice(text: String, color: Color) {
    Text(
        text, style = MaterialTheme.typography.bodyMedium, color = color,
        modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(color.copy(alpha = 0.1f)).padding(12.dp),
    )
}

@Composable
private fun PrimaryButton(text: String, icon: ImageVector? = null, enabled: Boolean = true, onClick: () -> Unit) {
    Button(
        onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth().height(52.dp),
        shape = RoundedCornerShape(14.dp),
        colors = ButtonDefaults.buttonColors(containerColor = Sk.Gold, contentColor = Color(0xFF1A1407)),
    ) {
        if (icon != null) { Icon(icon, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)) }
        Text(text, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
    }
}

// ─────────────────────────────── system helpers ───────────────────────────────

private fun notificationsGranted(ctx: Context): Boolean =
    Build.VERSION.SDK_INT < 33 ||
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

private fun ignoringBattery(ctx: Context): Boolean =
    ctx.getSystemService(PowerManager::class.java)?.isIgnoringBatteryOptimizations(ctx.packageName) == true

private fun requestIgnoreBattery(ctx: Context) {
    val direct = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}"))
    if (!tryStart(ctx, direct)) tryStart(ctx, Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
}

private fun openAppSettings(ctx: Context) {
    tryStart(ctx, Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}")))
}

/** iQOO / Vivo keep auto-start in their own security app; fall back to the app's settings page. */
private fun openAutoStart(ctx: Context) {
    val candidates = listOf(
        ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.BgStartUpManager"),
        ComponentName("com.iqoo.secure", "com.iqoo.secure.ui.phoneoptimize.AddWhiteListActivity"),
        ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.BgStartUpManagerActivity"),
        ComponentName("com.vivo.permissionmanager", "com.vivo.permissionmanager.activity.PurviewTabActivity"),
    )
    if (candidates.none { tryStart(ctx, Intent().setComponent(it)) }) openAppSettings(ctx)
}

private fun tryStart(ctx: Context, intent: Intent): Boolean = try {
    ctx.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); true
} catch (_: ActivityNotFoundException) { false } catch (_: SecurityException) { false }
