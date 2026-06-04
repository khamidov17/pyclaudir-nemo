package com.avazbek.nemo_app

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import io.flutter.plugin.common.MethodChannel

/**
 * Nemo Accessibility Service — full UI control.
 *
 * User must enable once: Settings → Accessibility → Nemo → Enable.
 * Flutter calls via MethodChannel "com.avazbek.nemo_app/accessibility".
 */
class NemoAccessibilityService : AccessibilityService() {

    companion object {
        var instance: NemoAccessibilityService? = null
        const val CHANNEL = "com.avazbek.nemo_app/accessibility"

        fun isEnabled(): Boolean = instance != null

        fun tap(x: Float, y: Float): Boolean {
            val svc = instance ?: return false
            val path = Path().apply { moveTo(x, y) }
            val stroke = GestureDescription.StrokeDescription(path, 0, 50)
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            svc.dispatchGesture(gesture, null, null)
            return true
        }

        fun swipe(x1: Float, y1: Float, x2: Float, y2: Float): Boolean {
            val svc = instance ?: return false
            val path = Path().apply { moveTo(x1, y1); lineTo(x2, y2) }
            val stroke = GestureDescription.StrokeDescription(path, 0, 300)
            val gesture = GestureDescription.Builder().addStroke(stroke).build()
            svc.dispatchGesture(gesture, null, null)
            return true
        }

        fun typeText(text: String): Boolean {
            val svc = instance ?: return false
            val root = svc.rootInActiveWindow ?: return false
            val focused = findFocusedInput(root) ?: return false
            val args = Bundle().apply {
                putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
            }
            return focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        }

        fun pressButton(button: String): Boolean {
            val svc = instance ?: return false
            return when (button.lowercase()) {
                "back" -> svc.performGlobalAction(GLOBAL_ACTION_BACK)
                "home" -> svc.performGlobalAction(GLOBAL_ACTION_HOME)
                "recents" -> svc.performGlobalAction(GLOBAL_ACTION_RECENTS)
                "notifications" -> svc.performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
                else -> false
            }
        }

        fun getUiTree(): String {
            val svc = instance ?: return "accessibility_disabled"
            val root = svc.rootInActiveWindow ?: return "no_window"
            return buildUiTree(root, 0)
        }

        private fun buildUiTree(node: AccessibilityNodeInfo?, depth: Int): String {
            node ?: return ""
            val sb = StringBuilder()
            val indent = "  ".repeat(depth)
            val cls = node.className?.toString()?.substringAfterLast('.') ?: ""
            val text = node.text?.toString() ?: ""
            val desc = node.contentDescription?.toString() ?: ""
            val bounds = Rect().also { node.getBoundsInScreen(it) }
            // Skip password fields — never expose content
            val isPassword = node.isPassword
            val label = when {
                isPassword -> "[PASSWORD_FIELD]"
                text.isNotEmpty() -> text.take(80)
                desc.isNotEmpty() -> desc.take(80)
                else -> ""
            }
            if (cls.isNotEmpty() || label.isNotEmpty()) {
                sb.appendLine("$indent[$cls] ${bounds.left},${bounds.top}-${bounds.right},${bounds.bottom} ${label}")
            }
            for (i in 0 until node.childCount) {
                if (depth < 8) { // cap depth to avoid huge trees
                    sb.append(buildUiTree(node.getChild(i), depth + 1))
                }
            }
            return sb.toString()
        }

        private fun findFocusedInput(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            node ?: return null
            if (node.isFocused && node.isEditable) return node
            if (node.className?.contains("EditText") == true) return node
            for (i in 0 until node.childCount) {
                val found = findFocusedInput(node.getChild(i))
                if (found != null) return found
            }
            return null
        }
    }

    override fun onServiceConnected() {
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onDestroy() {
        instance = null
        super.onDestroy()
    }
}
