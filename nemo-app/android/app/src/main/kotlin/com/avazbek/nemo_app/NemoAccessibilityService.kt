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
        @Volatile var instance: NemoAccessibilityService? = null
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

        fun typeText(text: String, targetPkg: String? = null): Boolean {
            val svc = instance ?: return false
            // Poll until rootInActiveWindow is non-null (and belongs to targetPkg when given).
            // Handles cold-start delays where the target app hasn't drawn yet.
            var root = svc.rootInActiveWindow
            if (root == null || (targetPkg != null && root.packageName?.contains(targetPkg) != true)) {
                val deadline = System.currentTimeMillis() + 5000L
                while (System.currentTimeMillis() < deadline) {
                    Thread.sleep(200)
                    root = svc.rootInActiveWindow
                    if (root != null &&
                        (targetPkg == null || root.packageName?.contains(targetPkg) == true)) break
                }
            }
            root = svc.rootInActiveWindow ?: return false
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

        /** Click the first node whose text/content-description matches (for
         *  driving app UIs by name, e.g. Telegram's "Send" button). */
        fun clickByText(query: String): Boolean {
            val svc = instance ?: return false
            val root = svc.rootInActiveWindow ?: return false
            val target = findByText(root, query.lowercase()) ?: return false
            var node: AccessibilityNodeInfo? = target
            while (node != null && !node.isClickable) node = node.parent
            if (node != null &&
                node.performAction(AccessibilityNodeInfo.ACTION_CLICK)
            ) return true
            val b = Rect().also { target.getBoundsInScreen(it) }
            if (b.isEmpty) return false
            return tap(b.exactCenterX(), b.exactCenterY())
        }

        private fun findByText(node: AccessibilityNodeInfo?, q: String): AccessibilityNodeInfo? {
            node ?: return null
            val text = node.text?.toString()?.lowercase() ?: ""
            val desc = node.contentDescription?.toString()?.lowercase() ?: ""
            if (text == q || desc == q) return node
            for (i in 0 until node.childCount) {
                findByText(node.getChild(i), q)?.let { return it }
            }
            if (text.contains(q) || desc.contains(q)) return node
            return null
        }

        /** Tap the first clickable row inside the first scrollable list — used
         *  to open the top hit of an app's own search results (Telegram, etc). */
        fun clickFirstResult(): Boolean {
            val svc = instance ?: return false
            val root = svc.rootInActiveWindow ?: return false
            val list = findScrollable(root) ?: root
            val row = findFirstClickable(list) ?: return false
            if (row.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true
            val b = Rect().also { row.getBoundsInScreen(it) }
            if (b.isEmpty) return false
            return tap(b.exactCenterX(), b.exactCenterY())
        }

        private fun findScrollable(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            node ?: return null
            if (node.isScrollable) return node
            for (i in 0 until node.childCount) {
                findScrollable(node.getChild(i))?.let { return it }
            }
            return null
        }

        private fun findFirstClickable(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
            node ?: return null
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                if (!child.isEnabled || !child.isVisibleToUser) continue
                // Skip section headers: clickable containers with many children
                // and no text of their own (they are layout wrappers, not results).
                val childText = child.text?.toString()?.trim() ?: ""
                val childDesc = child.contentDescription?.toString()?.trim() ?: ""
                val hasOwnText = childText.isNotEmpty() || childDesc.isNotEmpty()
                if (child.isClickable && (hasOwnText || child.childCount <= 2)) return child
                findFirstClickable(child)?.let { return it }
            }
            return null
        }

        fun getUiTree(): String {
            val svc = instance ?: return "accessibility_disabled"
            val root = svc.rootInActiveWindow ?: return "no_window"
            return buildUiTree(root, 0)
        }

        /** The package currently in the foreground — captured before Nemo opens
         *  another app so it can hand the phone back afterwards (best-effort). */
        fun getForegroundPackage(): String? =
            instance?.rootInActiveWindow?.packageName?.toString()

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
            for (i in 0 until node.childCount) {
                val found = findFocusedInput(node.getChild(i))
                if (found != null) return found
            }
            // Only fall back to any editable node if nothing focused was found;
            // class-name match is intentionally removed (wrong node on multi-input screens).
            if (node.isEditable && node.isEnabled) return node
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
