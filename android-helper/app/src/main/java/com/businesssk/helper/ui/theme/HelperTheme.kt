package com.businesssk.helper.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** The Studio's dark + gold look, so the helper feels like part of Business-SK. */
object Sk {
    val Bg = Color(0xFF0E0F12)
    val Panel = Color(0xFF16181D)
    val Panel2 = Color(0xFF1D2027)
    val Border = Color(0xFF2A2E37)
    val Gold = Color(0xFFE2B45C)
    val Text = Color(0xFFECE9E2)
    val Muted = Color(0xFFA7A39A)
    val Faint = Color(0xFF6F6C66)
    val Ok = Color(0xFF3FB950)
    val Warn = Color(0xFFE3A13B)
    val Danger = Color(0xFFF0605A)
}

private val scheme = darkColorScheme(
    primary = Sk.Gold, onPrimary = Color(0xFF1A1407),
    secondary = Sk.Gold, onSecondary = Color(0xFF1A1407),
    background = Sk.Bg, onBackground = Sk.Text,
    surface = Sk.Panel, onSurface = Sk.Text,
    surfaceVariant = Sk.Panel2, onSurfaceVariant = Sk.Muted,
    surfaceContainer = Sk.Panel, surfaceContainerHigh = Sk.Panel2, surfaceContainerHighest = Sk.Panel2,
    outline = Sk.Border, outlineVariant = Sk.Border,
    error = Sk.Danger,
)

private val type = Typography(
    headlineMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 26.sp, lineHeight = 32.sp, letterSpacing = (-0.3).sp),
    titleLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 20.sp, lineHeight = 26.sp),
    titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp, lineHeight = 22.sp),
    bodyLarge = TextStyle(fontSize = 15.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 17.sp),
    labelSmall = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 10.5.sp, letterSpacing = 1.4.sp),
)

@Composable
fun HelperTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = scheme,
        typography = type,
        shapes = Shapes(small = RoundedCornerShape(10.dp), medium = RoundedCornerShape(14.dp), large = RoundedCornerShape(20.dp)),
        content = content,
    )
}
