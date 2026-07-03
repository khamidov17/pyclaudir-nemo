package com.avazbek.nemo_app

import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.AlarmClock
import androidx.core.content.FileProvider
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.security.MessageDigest

/**
 * Handlers for the "intents" MethodChannel: launching apps (by package OR by
 * human name), alarms/timers via the system clock app, contact lookup for the
 * Telegram-message flow, verified APK install, URL open.
 */
object IntentActions {

    fun handle(activity: Activity, call: MethodCall, result: MethodChannel.Result) {
        when (call.method) {
            "openApp" -> result.success(openApp(activity, call.argument("package") ?: ""))
            "openAppByName" -> result.success(openAppByName(activity, call.argument("name") ?: ""))
            "listApps" -> result.success(listApps(activity).map { it.second })
            "listAppsWithLabels" -> result.success(listApps(activity).map { "${it.first}|${it.second}" })
            "openUrl" -> result.success(openUrl(activity, call.argument("url") ?: ""))
            "installApk" -> result.success(installApk(activity, call.argument("path") ?: ""))
            "setAlarm" -> result.success(setAlarm(activity, call))
            "setTimer" -> result.success(setTimer(activity, call))
            else -> result.notImplemented()
        }
    }

    private fun openApp(activity: Activity, pkg: String): Boolean {
        val intent = activity.packageManager.getLaunchIntentForPackage(pkg) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity.startActivity(intent)
        return true
    }

    /** Launch by human-readable label ("Spotify", "gallery"), fuzzy match. */
    private fun openAppByName(activity: Activity, name: String): String? {
        val query = name.trim().lowercase()
        if (query.isEmpty()) return null
        val apps = listApps(activity)
        val match = apps.firstOrNull { it.first.lowercase() == query }
            ?: apps.filter { it.first.lowercase().contains(query) }
                .minByOrNull { it.first.length }
        match ?: return null
        return if (openApp(activity, match.second)) match.first else null
    }

    /** (label, package) for every launcher app. */
    private fun listApps(activity: Activity): List<Pair<String, String>> {
        val intent = Intent(Intent.ACTION_MAIN, null).apply {
            addCategory(Intent.CATEGORY_LAUNCHER)
        }
        val pm = activity.packageManager
        return pm.queryIntentActivities(intent, 0)
            .map { Pair(it.loadLabel(pm).toString(), it.activityInfo.packageName) }
            .distinctBy { it.second }
            .sortedBy { it.first.lowercase() }
    }

    private fun openUrl(activity: Activity, url: String): Boolean {
        return try {
            val i = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            activity.startActivity(i)
            true
        } catch (e: Exception) {
            false
        }
    }

    /** Install an APK the Dart side has already hash-verified. */
    // SHA-256 of the Nemo release signing certificate. An update is only
    // installed if its APK is signed by THIS key — so even a compromised
    // server (or MITM that beat the hash check) cannot push a foreign APK.
    private const val RELEASE_CERT_SHA256 =
        "03F523475B06503E7EEAFD3D4C6DA5C7F71EB43CCC588213C077C44F274AF2B8"

    private fun installApk(activity: Activity, path: String): Boolean {
        return try {
            val file = File(path)
            if (!file.exists()) return false
            if (!isSignedByReleaseKey(activity, file.absolutePath)) return false
            val uri = FileProvider.getUriForFile(
                activity, "${activity.packageName}.fileprovider", file
            )
            val i = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            activity.startActivity(i)
            true
        } catch (e: Exception) {
            false
        }
    }

    /** Verify the APK file's signing cert matches our pinned release key. */
    private fun isSignedByReleaseKey(activity: Activity, apkPath: String): Boolean {
        return try {
            val pm = activity.packageManager
            val signatures: Array<android.content.pm.Signature> =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    val info = pm.getPackageArchiveInfo(
                        apkPath, PackageManager.GET_SIGNING_CERTIFICATES
                    ) ?: return false
                    info.signingInfo?.apkContentsSigners ?: return false
                } else {
                    @Suppress("DEPRECATION")
                    val info = pm.getPackageArchiveInfo(
                        apkPath, PackageManager.GET_SIGNATURES
                    ) ?: return false
                    @Suppress("DEPRECATION")
                    info.signatures ?: return false
                }
            val md = MessageDigest.getInstance("SHA-256")
            signatures.any { sig ->
                md.digest(sig.toByteArray()).joinToString("") { "%02X".format(it) } ==
                    RELEASE_CERT_SHA256
            }
        } catch (e: Exception) {
            false
        }
    }

    /** Set an alarm silently via the system clock app (no UI when skip_ui). */
    private fun setAlarm(activity: Activity, call: MethodCall): Boolean {
        val hour = call.argument<Int>("hour") ?: return false
        val minutes = call.argument<Int>("minutes") ?: 0
        val message = call.argument<String>("message") ?: "Nemo"
        return try {
            val i = Intent(AlarmClock.ACTION_SET_ALARM).apply {
                putExtra(AlarmClock.EXTRA_HOUR, hour)
                putExtra(AlarmClock.EXTRA_MINUTES, minutes)
                putExtra(AlarmClock.EXTRA_MESSAGE, message)
                putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            if (activity.packageManager.resolveActivity(i, 0) == null) return false
            activity.startActivity(i)
            true
        } catch (e: Exception) {
            false
        }
    }

    private fun setTimer(activity: Activity, call: MethodCall): Boolean {
        val seconds = call.argument<Int>("seconds") ?: return false
        val message = call.argument<String>("message") ?: "Nemo timer"
        return try {
            val i = Intent(AlarmClock.ACTION_SET_TIMER).apply {
                putExtra(AlarmClock.EXTRA_LENGTH, seconds)
                putExtra(AlarmClock.EXTRA_MESSAGE, message)
                putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            if (activity.packageManager.resolveActivity(i, 0) == null) return false
            activity.startActivity(i)
            true
        } catch (e: Exception) {
            false
        }
    }
}
