package com.avazbek.nemo_app

import android.app.Notification
import android.app.Person
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.util.concurrent.Executors
import org.json.JSONArray
import org.json.JSONObject

/**
 * Jarvis "message awareness": captures notifications from ALLOWLISTED messaging
 * apps (Telegram + WhatsApp by default) into a small, persisted ring buffer so
 * voice Nemo can give a brief rundown on an explicit "check my messages".
 *
 * Privacy (load-bearing):
 *  - Allowlist only — banking/SMS/OTP apps are NOT captured by default.
 *  - OTP/2FA codes are REDACTED on-device before anything is stored or leaves
 *    the phone.
 *  - The buffer is read only when the server asks (explicit request, owner-only).
 */
class NemoNotificationListenerService : NotificationListenerService() {

    override fun onNotificationPosted(sbn: StatusBarNotification?) {
        if (sbn == null) return
        try {
            capture(sbn)
        } catch (_: Throwable) {
            // Never let a malformed notification crash the listener.
        }
    }

    private fun capture(sbn: StatusBarNotification) {
        val pkg = sbn.packageName ?: return
        if (pkg !in allowlist(this)) return
        val n = sbn.notification ?: return
        // Skip ongoing (music/downloads) + group-summary notifications.
        if (n.flags and Notification.FLAG_ONGOING_EVENT != 0) return
        if (n.flags and Notification.FLAG_GROUP_SUMMARY != 0) return
        val extras = n.extras ?: return
        val pair = extractSenderAndText(extras) ?: return
        val (sender, text) = pair
        if (text.isBlank()) return
        val entry = JSONObject().apply {
            put("app", appLabel(this@NemoNotificationListenerService, pkg))
            put("sender", redact(sender))
            put("text", redact(text).take(MAX_TEXT))
            put("ts", System.currentTimeMillis())
        }
        push(this, sbn.key ?: "$pkg:${System.currentTimeMillis()}", entry)
    }

    /**
     * Prefer MessagingStyle (EXTRA_MESSAGES) → the actual latest message with its
     * sender. Reading only EXTRA_TEXT yields the summary line ("3 new messages"),
     * NOT per-sender content — so fall back to title/text only if there's no
     * MessagingStyle.
     */
    private fun extractSenderAndText(extras: Bundle): Pair<String, String>? {
        val msgs = if (Build.VERSION.SDK_INT >= 33)
            extras.getParcelableArray(Notification.EXTRA_MESSAGES, Bundle::class.java)
        else
            @Suppress("DEPRECATION") extras.getParcelableArray(Notification.EXTRA_MESSAGES)
        if (msgs != null && msgs.isNotEmpty()) {
            val last = msgs.last() as? Bundle
            val body = last?.getCharSequence("text")?.toString() ?: ""
            if (body.isNotBlank()) {
                val who = senderName(last)
                    ?: extras.getCharSequence(Notification.EXTRA_TITLE)?.toString()
                    ?: ""
                return who to body
            }
        }
        val title = extras.getCharSequence(Notification.EXTRA_TITLE)?.toString() ?: ""
        val body = (extras.getCharSequence(Notification.EXTRA_BIG_TEXT)
            ?: extras.getCharSequence(Notification.EXTRA_TEXT)
            ?: "").toString()
        if (body.isBlank()) return null
        return title to body
    }

    /** MessagingStyle stores the sender as a Person (API 28+); fall back to the
     *  legacy "sender" CharSequence — modern Telegram/WhatsApp use the Person. */
    private fun senderName(b: Bundle?): String? {
        if (b == null) return null
        if (Build.VERSION.SDK_INT >= 28) {
            val name = b.getParcelable<Person>("sender_person")?.name?.toString()
            if (!name.isNullOrBlank()) return name
        }
        return b.getCharSequence("sender")?.toString()
    }

    companion object {
        private const val PREFS = "nemo_messages"
        private const val KEY_BUF = "buffer"
        private const val KEY_ALLOW = "allowlist"
        private const val MAX_ENTRIES = 50
        private const val MAX_TEXT = 200
        // Single-thread executor replaces @Synchronized; the executor serializes
        // calls without blocking the Binder notification thread (ANR risk, N-03).
        private val _executor = Executors.newSingleThreadExecutor()

        // Review decision: Telegram + WhatsApp only by default (SMS is the OTP
        // channel and is left OUT). User can extend the allowlist later.
        private val DEFAULT_ALLOW = setOf("org.telegram.messenger", "com.whatsapp")

        // OTP/2FA codes → "[code]" on-device, BEFORE storage or cloud. Matches a
        // 4-8 digit code EVEN when split by spaces/hyphens ("12 345", "123-456")
        // — a contiguous-only regex leaked spaced codes to the cloud. Aggressive
        // by design; safety beats completeness (a redacted year/price is fine).
        private val CODE_RE = Regex("\\b\\d(?:[\\s-]?\\d){3,11}\\b")

        fun redact(s: String): String = CODE_RE.replace(s, "[code]")

        fun allowlist(ctx: Context): Set<String> {
            val csv = prefs(ctx).getString(KEY_ALLOW, null) ?: return DEFAULT_ALLOW
            return csv.split(",").map { it.trim() }.filter { it.isNotEmpty() }.toSet()
        }

        fun push(ctx: Context, key: String, entry: JSONObject) {
            _executor.execute {
                val p = prefs(ctx)
                val arr = JSONArray(p.getString(KEY_BUF, "[]"))
                val kept = JSONArray()
                for (i in 0 until arr.length()) {
                    val o = arr.getJSONObject(i)
                    if (o.optString("key") != key) kept.put(o)
                }
                entry.put("key", key)
                kept.put(entry)
                val start = maxOf(0, kept.length() - MAX_ENTRIES)
                val capped = JSONArray()
                for (i in start until kept.length()) capped.put(kept.getJSONObject(i))
                p.edit().putString(KEY_BUF, capped.toString()).apply()
            }
        }

        /** Buffer as a JSON array string (oldest→newest), without the internal key. */
        fun getJson(ctx: Context): String {
            val arr = JSONArray(prefs(ctx).getString(KEY_BUF, "[]"))
            val out = JSONArray()
            for (i in 0 until arr.length()) {
                val o = arr.getJSONObject(i)
                out.put(JSONObject().apply {
                    put("app", o.optString("app"))
                    put("sender", o.optString("sender"))
                    put("text", o.optString("text"))
                    put("ts", o.optLong("ts"))
                })
            }
            return out.toString()
        }

        fun isAccessGranted(ctx: Context): Boolean {
            val flat = Settings.Secure.getString(
                ctx.contentResolver, "enabled_notification_listeners"
            ) ?: return false
            val cn = android.content.ComponentName(
                ctx, NemoNotificationListenerService::class.java
            ).flattenToString()
            // Use exact component match — flat.contains(cn) would match a partial
            // package name prefix (e.g. com.foo matching com.foobar).
            return flat.split(':').any { it == cn }
        }

        private fun prefs(ctx: Context): SharedPreferences = try {
            val key = MasterKey.Builder(ctx)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                ctx, PREFS, key,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        } catch (_: Throwable) {
            ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        }

        private fun appLabel(ctx: Context, pkg: String): String = try {
            val pm = ctx.packageManager
            pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
        } catch (_: Throwable) {
            when (pkg) {
                "org.telegram.messenger" -> "Telegram"
                "com.whatsapp" -> "WhatsApp"
                else -> pkg.substringAfterLast('.')
            }
        }
    }
}
