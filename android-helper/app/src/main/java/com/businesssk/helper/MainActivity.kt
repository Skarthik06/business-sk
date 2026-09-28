package com.businesssk.helper

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import com.businesssk.helper.ui.HelperRoot
import com.businesssk.helper.ui.HelperViewModel
import com.businesssk.helper.ui.theme.HelperTheme

class MainActivity : ComponentActivity() {
    private val vm: HelperViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        if (savedInstanceState == null) vm.handleLink(intent?.data)
        setContent { HelperTheme { HelperRoot(vm) } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        vm.handleLink(intent.data)
    }

    override fun onResume() {
        super.onResume()
        vm.ensureRunning()
    }
}
