package com.businesssk.helper

import android.app.Activity
import android.content.ComponentName
import android.content.Intent
import android.net.Uri
import android.os.Bundle

/**
 * Invisible link trampoline (no UI, finishes at once).
 *
 *  skhelper://open?url=<https link>  → opens the link in the separate CHROME APP (its own task in
 *      recent apps, your Chrome profile) instead of the Business-SK app's in-app tab. The Studio uses
 *      it for Google Colab, so Colab and the Studio are two separate screens you switch between.
 *  skhelper://studio                 → brings the Business-SK Studio back to the front exactly as you
 *      left it (same as tapping its icon — no reload). Colab's "Back to Business-SK" button uses it.
 */
class LinkActivity : Activity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val uri = intent?.data
        when (uri?.host) {
            "open" -> openInChrome(uri.getQueryParameter("url"))
            "studio" -> backToStudio()
        }
        finish()
    }

    private fun openInChrome(raw: String?) {
        val url = raw?.trim().orEmpty()
        if (!url.startsWith("https://")) return            // only real web links
        val view = Intent(Intent.ACTION_VIEW, Uri.parse(url)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            startActivity(Intent(view).setPackage("com.android.chrome"))
        } catch (_: Exception) {
            try { startActivity(view) } catch (_: Exception) { }   // no Chrome → the default browser
        }
    }

    private fun backToStudio() {
        // The launcher intent of the Studio (TWA) activity: Android resumes its existing task as it is.
        val launch = Intent(Intent.ACTION_MAIN)
            .addCategory(Intent.CATEGORY_LAUNCHER)
            .setComponent(ComponentName(packageName, "com.google.androidbrowserhelper.trusted.LauncherActivity"))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        try { startActivity(launch) } catch (_: Exception) { }
    }
}
